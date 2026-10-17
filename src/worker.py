from __future__ import annotations

import functools
import hashlib
from pathlib import Path
import re
from typing import Callable
import urllib.parse

from anki.collection import Collection
from markdown2anki.build import MODEL_CLOZE, build
from markdown2anki.config import load_vault_config
from markdown2anki.flags import write_flags
from markdown2anki.ids import new_card_id, with_marker
from markdown2anki.models import MODEL_NAME_BASIC, MODEL_NAME_CLOZE
from markdown2anki.occlusions import export_occlusions
from markdown2anki.vault import collect

from .bridge import (
    apply_note_create,
    apply_note_update,
    build_existing_note_index,
    ensure_models,
)
from .contracts import (
    ExistingNoteRecord,
    FileSyncPlan,
    MediaItem,
    ModelName,
    NoteCreateOp,
    NoteUpdateOp,
    OrphanNoteRecord,
    SyncPlan,
    SyncReport,
)


def _compute_sha256(file_path: Path) -> str:
    return hashlib.sha256(file_path.read_bytes()).hexdigest()


def _write_occlusion_index(out_dir: Path, staged: dict[str, list[str]]) -> None:
    """Append staged image occlusions into index.md, preserving earlier batch history."""
    try:
        import datetime
        now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        index_file = out_dir / "index.md"

        batch_lines = [
            f"## Batch: {now}",
            "",
        ]
        for tag, images in sorted(staged.items()):
            batch_lines.append(f"### Tag: `{tag}`")
            for img in images:
                batch_lines.append(f"- `{img}`")
            batch_lines.append("")

        if not index_file.exists():
            header = [
                "# VaultSync - Staged Image Occlusions Index",
                "",
                "This index maps each Obsidian tag to the staged image files in this folder.",
                "",
                "---",
                "",
            ]
            full_content = "\n".join(header + batch_lines)
        else:
            existing_text = index_file.read_text(encoding="utf-8")
            if not existing_text.endswith("\n"):
                existing_text += "\n"
            full_content = existing_text + "\n---\n\n" + "\n".join(batch_lines)

        index_file.write_text(full_content, encoding="utf-8")
    except Exception:
        pass


@functools.lru_cache(maxsize=2048)
def _get_media_diff_pattern(orig_name: str) -> re.Pattern:
    escaped_variants = [
        re.escape(orig_name),
        re.escape(orig_name.lower()),
        re.escape(urllib.parse.quote(orig_name)),
        re.escape(urllib.parse.quote(orig_name.lower())),
    ]
    variants_str = "|".join(escaped_variants)
    return re.compile(
        r'src=(["\'])(' + variants_str + r')\1',
        re.IGNORECASE,
    )


def _normalize_html_for_diff(text: str, media_refs: list[MediaItem]) -> str:
    """Normalize unhashed image references in HTML text to their staged hashed names.

    When hash_media is True, markdown2anki produces hashed filenames (e.g. 5bcc4ef4_01.png).
    Existing notes in Anki may still reference the unhashed filename (e.g. pasted image 20261007165418.png).
    This function replaces unhashed references with their hashed counterpart so that diff detection
    does not falsely flag the card as modified when only the media naming format changed.
    """
    if not media_refs or not text:
        return text
    normalized = text
    for item in media_refs:
        if item.staged_filename != item.source_path.name:
            pattern = _get_media_diff_pattern(item.source_path.name)
            normalized = pattern.sub(lambda m, s=item.staged_filename: f"src={m.group(1)}{s}{m.group(1)}", normalized)
    return normalized


def build_sync_plan(
    col: Collection,
    vault_path: Path,
    default_deck: str = "Default",
    trace_fn: Callable[[str], None] | None = None,
    basic_model_id: int | None = None,
    cloze_model_id: int | None = None,
) -> SyncPlan:
    """Stage 1: Discovers cards in vault, compares against Anki SQLite index, and builds SyncPlan."""
    vault_path = Path(vault_path).expanduser().resolve()
    model_ids = ensure_models(col, basic_model_id=basic_model_id, cloze_model_id=cloze_model_id, create=False)
    existing_index = build_existing_note_index(col, model_ids)

    # Deck lookups memoization to avoid redundant FFI round-trips and prevent deck creation on scan
    deck_id_cache: dict[str, int | None] = {}
    deck_name_cache: dict[int, str] = {}

    def _get_deck_id_readonly(name: str) -> int | None:
        if name not in deck_id_cache:
            if hasattr(col.decks, "id_for_name"):
                deck_id_cache[name] = col.decks.id_for_name(name)
            else:
                d = col.decks.by_name(name)
                deck_id_cache[name] = d["id"] if d else None
        return deck_id_cache[name]

    def _get_deck_name(did: int) -> str:
        if did not in deck_name_cache:
            deck_name_cache[did] = col.decks.name(did)
        return deck_name_cache[did]

    vault_cfg = load_vault_config(vault_path)
    collected = collect(vault_cfg, trace_fn=trace_fn)
    result = build(
        collected.cards,
        collected.diagnostics,
        vault_cfg.cloze_notes,
        vault_root=vault_cfg.vault,
        hash_media=vault_cfg.hash_media,
    )

    # Group rendered notes by source file
    notes_by_file: dict[Path, list] = {}
    for note in result.notes:
        notes_by_file.setdefault(note.card.file, []).append(note)

    file_plans: list[FileSyncPlan] = []
    seen_card_ids: set[str] = set()

    for file_path, file_notes in sorted(notes_by_file.items(), key=lambda x: str(x[0])):
        if not file_path.is_file():
            continue

        mtime_ns = file_path.stat().st_mtime_ns
        sha256_hex = _compute_sha256(file_path)

        creates: list[NoteCreateOp] = []
        updates: list[NoteUpdateOp] = []
        unchanged_count = 0

        for note in file_notes:
            card = note.card
            card_id = card.card_id

            if card_id is not None and card_id in seen_card_ids:
                msg = f"Duplicate card ID '{card_id}' in {file_path.name}:{card.line}; skipping duplicate."
                collected.diagnostics.append(msg)
                if trace_fn:
                    trace_fn(msg)
                continue

            model_name: ModelName = (
                MODEL_NAME_CLOZE if note.model == MODEL_CLOZE else MODEL_NAME_BASIC
            )
            target_deck = note.deck or vault_cfg.deck or default_deck

            media_refs: list[MediaItem] = [
                MediaItem(
                    source_path=img,
                    staged_filename=result.media_names.get(img, img.name),
                )
                for img in note.media
            ]

            is_existing = card_id is not None and card_id in existing_index

            if not is_existing:
                # New card
                assigned_id = card_id if card_id else new_card_id()
                seen_card_ids.add(assigned_id)
                field_names = ("Text", "Back Extra") if model_name == MODEL_NAME_CLOZE else ("Front", "Back")
                fields = {
                    field_names[0]: note.fields[0] if len(note.fields) > 0 else "",
                    field_names[1]: with_marker(note.fields[1] if len(note.fields) > 1 else "", assigned_id),
                }
                creates.append(
                    NoteCreateOp(
                        card_id=assigned_id,
                        model_name=model_name,
                        target_deck_name=target_deck,
                        fields=fields,
                        tags=list(note.tags),
                        source_file=file_path,
                        source_line=card.line,
                        media_refs=media_refs,
                        card=card,
                    )
                )
            else:
                assert card_id is not None
                seen_card_ids.add(card_id)
                rec = existing_index[card_id]

                field_names = ("Text", "Back Extra") if model_name == MODEL_NAME_CLOZE else ("Front", "Back")
                new_fields = {
                    field_names[0]: note.fields[0] if len(note.fields) > 0 else "",
                    field_names[1]: with_marker(note.fields[1] if len(note.fields) > 1 else "", card_id),
                }

                normalized_rec_fields = {
                    fname: _normalize_html_for_diff(fval, media_refs)
                    for fname, fval in rec.fields.items()
                }

                # Diff detection
                fields_changed = new_fields != normalized_rec_fields
                tags_changed = set(note.tags) != set(rec.tags)

                target_deck_id = _get_deck_id_readonly(target_deck)
                deck_changed = (target_deck_id is None) or (target_deck_id not in rec.deck_ids)
                old_deck_names = [_get_deck_name(did) for did in rec.deck_ids]

                if fields_changed or tags_changed or deck_changed:
                    updates.append(
                        NoteUpdateOp(
                            note_id=rec.note_id,
                            card_id=card_id,
                            model_name=model_name,
                            target_deck_name=target_deck,
                            old_deck_names=old_deck_names,
                            old_fields=normalized_rec_fields,
                            new_fields=new_fields,
                            old_tags=rec.tags,
                            new_tags=list(note.tags),
                            deck_changed=deck_changed,
                            fields_changed=fields_changed,
                            tags_changed=tags_changed,
                            source_file=file_path,
                            source_line=card.line,
                            media_refs=media_refs,
                            card=card,
                            approved=True,
                        )
                    )
                else:
                    unchanged_count += 1

        file_plans.append(
            FileSyncPlan(
                file_path=file_path,
                mtime_ns=mtime_ns,
                sha256_hex=sha256_hex,
                creates=creates,
                updates=updates,
                unchanged_count=unchanged_count,
            )
        )

    # Detect orphaned cards (present in Anki under M2A models, but deleted from vault)
    orphans: list[OrphanNoteRecord] = []
    orphan_ids = set(existing_index.keys()) - seen_card_ids
    for oid in sorted(orphan_ids):
        rec = existing_index[oid]
        deck_names = [_get_deck_name(did) for did in rec.deck_ids]
        preview = next(iter(rec.fields.values()), "") if rec.fields else ""
        orphans.append(
            OrphanNoteRecord(
                note_id=rec.note_id,
                card_id=oid,
                model_name=rec.model_name,
                preview_question=preview,
                deck_names=deck_names,
                fields=rec.fields,
                tags=rec.tags,
            )
        )

    # Filter image occlusions: only stage if re_stage_occlusions is enabled or if not yet added
    occlusions_to_stage = [
        occ for occ in result.occlusions
        if vault_cfg.re_stage_occlusions or not occ.card.added
    ]

    return SyncPlan(
        vault_path=vault_path,
        default_deck=vault_cfg.deck or default_deck,
        file_plans=file_plans,
        occlusions_to_stage=occlusions_to_stage,
        orphans=orphans,
        diagnostics=[str(d) for d in collected.diagnostics],
    )


def execute_sync_plan(
    col: Collection,
    plan: SyncPlan,
    include_updates: bool = True,
    occlusion_dir: Path | None = None,
    basic_model_id: int | None = None,
    cloze_model_id: int | None = None,
) -> SyncReport:
    """Stage 3: Applies atomic Flag-Before-Insert persistence and Anki DB mutations."""
    report = SyncReport()
    model_ids = ensure_models(col, basic_model_id=basic_model_id, cloze_model_id=cloze_model_id)

    try:
        undo_id: int | None = col.add_custom_undo_entry("VaultSync")
    except Exception:
        undo_id = None

    def _merge_undo() -> None:
        if undo_id is not None:
            try:
                col.merge_undo_entries(undo_id)
            except Exception:
                pass

    for file_plan in plan.file_plans:
        has_creates = len(file_plan.creates) > 0
        approved_updates = [u for u in file_plan.updates if u.approved] if include_updates else []

        if not has_creates and not approved_updates:
            continue

        # 1. Verify file has not been modified externally since Stage 1 scan
        try:
            current_bytes = file_plan.file_path.read_bytes()
        except OSError as exc:
            report.skipped_files.append((file_plan.file_path, f"Cannot read file: {exc}"))
            continue

        current_sha256 = hashlib.sha256(current_bytes).hexdigest()
        if current_sha256 != file_plan.sha256_hex:
            report.skipped_files.append(
                (file_plan.file_path, "File modified externally in Obsidian after scan; skipped for safety to prevent overwriting.")
            )
            report.externally_modified_files.append(file_plan.file_path)
            continue

        # 2. Flag-Before-Insert protocol: persist flags to Markdown FIRST
        if has_creates:
            flags_to_write = [
                (create_op.card, create_op.card_id)
                for create_op in file_plan.creates
                if create_op.card is not None
            ]
            if flags_to_write:
                try:
                    flagged_count, problems = write_flags(flags_to_write)
                    if problems:
                        report.skipped_files.append(
                            (file_plan.file_path, f"Flag write mismatch: {'; '.join(problems)}")
                        )
                        continue
                except OSError as exc:
                    report.skipped_files.append(
                        (file_plan.file_path, f"Failed to write ADDED flags: {exc}")
                    )
                    continue

        # 3. Apply Note Creations in Anki
        for create_op in file_plan.creates:
            try:
                media_added = apply_note_create(col, create_op, model_ids)
                report.created_count += 1
                report.media_added_count += media_added
                _merge_undo()
            except Exception as exc:
                report.warnings.append(
                    f"{file_plan.file_path.name}:{create_op.source_line} ({create_op.card_id}): {exc}"
                )

        # 4. Apply Approved Note Updates in Anki
        for update_op in approved_updates:
            try:
                media_added, deck_moved = apply_note_update(col, update_op, model_ids)
                report.updated_count += 1
                report.media_added_count += media_added
                if deck_moved:
                    report.deck_moved_count += 1
                _merge_undo()
            except Exception as exc:
                report.warnings.append(
                    f"{file_plan.file_path.name}:{update_op.source_line} ({update_op.card_id}): {exc}"
                )

    # 5. Stage Image Occlusions
    if plan.occlusions_to_stage:
        if occlusion_dir is not None:
            try:
                base_dir = Path(occlusion_dir).expanduser().resolve()
                out_dir, flags, staged = export_occlusions(plan.occlusions_to_stage, base_dir)
                if flags:
                    write_flags(flags)
                total_imgs = sum(len(imgs) for imgs in staged.values())
                report.occlusions_staged_count = total_imgs
                report.staged_occlusions_by_tag = staged
                report.occlusion_dir = out_dir

                # Automatically write markdown index mapping tags to generated image filenames
                _write_occlusion_index(out_dir, staged)
            except Exception as exc:
                report.warnings.append(f"Image occlusions staging error: {exc}")
        else:
            report.warnings.append(
                f"{len(plan.occlusions_to_stage)} image occlusion(s) found, but no Image Occlusion Export Folder is configured in Settings."
            )

    # Commit to SQLite and register in undo history
    try:
        col.save()
    except Exception:
        pass
    _merge_undo()
    return report
