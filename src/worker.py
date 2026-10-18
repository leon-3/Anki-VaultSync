from __future__ import annotations

import functools
import hashlib
from pathlib import Path
import re
from typing import Callable
import urllib.parse

from anki.collection import Collection
from anki.models import NotetypeId
from markdown2anki.build import MODEL_CLOZE, MODEL_IMAGE_OCCLUSION, build
from markdown2anki.config import load_vault_config
from markdown2anki.constants import OCCLUSION_TAG
from markdown2anki.flags import clear_flags, write_flags
from markdown2anki.ids import new_card_id, with_marker
from markdown2anki.models import (
    MODEL_NAME_BASIC,
    MODEL_NAME_CLOZE,
    MODEL_NAME_IMAGE_OCCLUSION,
)
from markdown2anki.occlusions import export_occlusions
from markdown2anki.parser import Card
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
    MissingInAnkiRecord,
    ModelName,
    NoteCreateOp,
    NoteUpdateOp,
    OrphanNoteRecord,
    SyncPlan,
    SyncReport,
)


def _compute_sha256(file_path: Path) -> str:
    return hashlib.sha256(file_path.read_bytes()).hexdigest()


def _get_model_field_names(
    col: Collection, mid: int | None, fallback: tuple[str, ...]
) -> tuple[str, ...]:
    """Retrieve actual field names for a note type in the collection, or use fallback."""
    if mid is not None:
        try:
            m = col.models.get(NotetypeId(mid))
            if m is not None and len(m.get("flds", [])) >= 2:
                return tuple(f["name"] for f in m["flds"])
        except Exception:
            pass
    return fallback


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
    recreate_missing: bool = False,
    recreate_missing_ids: set[str] | None = None,
) -> SyncPlan:
    """Stage 1: Discovers cards in vault, compares against Anki SQLite index, and builds SyncPlan."""
    vault_path = Path(vault_path).expanduser().resolve()
    model_ids = ensure_models(col, basic_model_id=basic_model_id, cloze_model_id=cloze_model_id, create=False)
    existing_index = build_existing_note_index(col, model_ids)

    basic_flds = _get_model_field_names(col, model_ids.get(MODEL_NAME_BASIC), ("Front", "Back"))
    cloze_flds = _get_model_field_names(col, model_ids.get(MODEL_NAME_CLOZE), ("Text", "Back Extra"))

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
    effective_cloze_notes = vault_cfg.cloze_notes or vault_cfg.suspend_new_clozes
    result = build(
        collected.cards,
        collected.diagnostics,
        effective_cloze_notes,
        vault_root=vault_cfg.vault,
        hash_media=vault_cfg.hash_media,
        image_occlusion_mode=vault_cfg.image_occlusion_mode,
    )

    # Group rendered notes by source file
    notes_by_file: dict[Path, list] = {}
    for note in result.notes:
        notes_by_file.setdefault(note.card.file, []).append(note)

    file_plans: list[FileSyncPlan] = []
    missing_in_anki: list[MissingInAnkiRecord] = []
    seen_card_ids: set[str] = set()

    for file_path, file_notes in sorted(notes_by_file.items(), key=lambda x: str(x[0])):
        if not file_path.is_file():
            continue

        mtime_ns = file_path.stat().st_mtime_ns

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

            if note.model == MODEL_IMAGE_OCCLUSION:
                model_name: ModelName = MODEL_NAME_IMAGE_OCCLUSION
            elif note.model == MODEL_CLOZE or (card is not None and getattr(card, "has_cloze_markers", False) and effective_cloze_notes):
                model_name: ModelName = MODEL_NAME_CLOZE
            else:
                model_name: ModelName = MODEL_NAME_BASIC

            target_deck = note.deck or vault_cfg.deck or default_deck

            media_refs: list[MediaItem] = [
                MediaItem(
                    source_path=img,
                    staged_filename=result.media_names.get(img, img.name),
                )
                for img in note.media
            ]

            is_existing = card_id is not None and card_id in existing_index
            should_recreate = recreate_missing or (recreate_missing_ids is not None and card_id in recreate_missing_ids)

            if card_id is not None and getattr(card, "added", False) and not is_existing and not should_recreate:
                # Card was exported previously but no longer exists in Anki database.
                # Default behavior: do NOT recreate automatically (avoid zombie cards).
                seen_card_ids.add(card_id)
                preview = card.question if card and card.question else (note.fields[0] if note.fields else "")
                missing_in_anki.append(
                    MissingInAnkiRecord(
                        card_id=card_id,
                        file_path=file_path,
                        line=card.line,
                        preview_question=preview,
                        target_deck=target_deck,
                        tags=list(note.tags),
                        card=card,
                    )
                )
                continue

            if not is_existing:
                # New card (or user-requested recreation of missing card)
                assigned_id = card_id if card_id else new_card_id()
                seen_card_ids.add(assigned_id)
                if model_name == MODEL_NAME_IMAGE_OCCLUSION:
                    fields = {
                        "Occlusion": note.fields[0] if len(note.fields) > 0 else "",
                        "Image": note.fields[1] if len(note.fields) > 1 else "",
                        "Header": note.fields[2] if len(note.fields) > 2 else "",
                        "Back Extra": note.fields[3] if len(note.fields) > 3 else "",
                        "Comments": with_marker(note.fields[4] if len(note.fields) > 4 else "", assigned_id),
                    }
                elif model_name == MODEL_NAME_CLOZE:
                    fields = {
                        cloze_flds[0]: note.fields[0] if len(note.fields) > 0 else "",
                        cloze_flds[1]: with_marker(note.fields[1] if len(note.fields) > 1 else "", assigned_id),
                    }
                else:
                    fields = {
                        basic_flds[0]: note.fields[0] if len(note.fields) > 0 else "",
                        basic_flds[1]: with_marker(note.fields[1] if len(note.fields) > 1 else "", assigned_id),
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

                if model_name == MODEL_NAME_IMAGE_OCCLUSION and getattr(vault_cfg, "freeze_image_occlusions", False):
                    unchanged_count += 1
                    continue

                is_cloze = (model_name == MODEL_NAME_CLOZE)
                target_flds = cloze_flds if is_cloze else basic_flds
                target_mid = model_ids.get(model_name)
                model_changed = (target_mid is not None and rec.model_id != target_mid)

                if model_name == MODEL_NAME_IMAGE_OCCLUSION:
                    new_fields = {
                        "Occlusion": rec.fields.get("Occlusion", ""),
                        "Image": rec.fields.get("Image", ""),
                        "Header": note.fields[2] if len(note.fields) > 2 else "",
                        "Back Extra": note.fields[3] if len(note.fields) > 3 else "",
                        "Comments": with_marker(note.fields[4] if len(note.fields) > 4 else "", card_id),
                    }
                    new_tags = [t for t in note.tags if t != OCCLUSION_TAG or OCCLUSION_TAG in rec.tags]
                elif model_changed:
                    new_fields = {
                        target_flds[0]: note.fields[0] if len(note.fields) > 0 else "",
                        target_flds[1]: with_marker(note.fields[1] if len(note.fields) > 1 else "", card_id),
                    }
                    new_tags = list(note.tags)
                else:
                    rec_keys = list(rec.fields.keys())
                    f0 = rec_keys[0] if len(rec_keys) > 0 else target_flds[0]
                    f1 = rec_keys[1] if len(rec_keys) > 1 else target_flds[1]
                    new_fields = dict(rec.fields)
                    new_fields[f0] = note.fields[0] if len(note.fields) > 0 else ""
                    new_fields[f1] = with_marker(note.fields[1] if len(note.fields) > 1 else "", card_id)
                    new_tags = list(note.tags)

                normalized_rec_fields = {
                    fname: _normalize_html_for_diff(fval, media_refs)
                    for fname, fval in rec.fields.items()
                }

                # Diff detection
                if model_name == MODEL_NAME_IMAGE_OCCLUSION:
                    fields_changed = model_changed or any(
                        new_fields.get(k, "") != normalized_rec_fields.get(k, "")
                        for k in ("Header", "Back Extra", "Comments")
                    )
                else:
                    fields_changed = model_changed or (new_fields != normalized_rec_fields)

                tags_changed = set(new_tags) != set(rec.tags)

                target_deck_id = _get_deck_id_readonly(target_deck)
                deck_changed = (target_deck_id is None) or (rec.deck_ids != {target_deck_id})
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
                            new_tags=new_tags,
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

        sha256_hex = _compute_sha256(file_path) if (creates or updates) else ""
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
        if rec.model_name == MODEL_NAME_IMAGE_OCCLUSION:
            preview = rec.fields.get("Header") or rec.fields.get("Image", "")
        else:
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

    for occ in result.occlusions:
        cid = occ.card.card_id
        if cid:
            if cid in existing_index:
                seen_card_ids.add(cid)
            elif occ.card.added:
                should_recreate_occ = recreate_missing or (recreate_missing_ids is not None and cid in recreate_missing_ids)
                if not should_recreate_occ:
                    seen_card_ids.add(cid)
                    target_deck = occ.card.deck or vault_cfg.deck or default_deck
                    missing_in_anki.append(
                        MissingInAnkiRecord(
                            card_id=cid,
                            file_path=occ.card.file,
                            line=occ.card.line,
                            preview_question=occ.card.question or "Image Occlusion",
                            target_deck=target_deck,
                            tags=[occ.tag] if occ.tag else [],
                            card=occ.card,
                        )
                    )

    # Filter image occlusions: only stage if re_stage_occlusions is enabled or if not yet added
    occlusions_to_stage = [
        occ for occ in result.occlusions
        if vault_cfg.re_stage_occlusions or not occ.card.added or (recreate_missing or (recreate_missing_ids and occ.card.card_id in recreate_missing_ids))
    ]

    return SyncPlan(
        vault_path=vault_path,
        default_deck=vault_cfg.deck or default_deck,
        file_plans=file_plans,
        occlusions_to_stage=occlusions_to_stage,
        orphans=orphans,
        missing_in_anki=missing_in_anki,
        diagnostics=[str(d) for d in collected.diagnostics],
        suspend_new_occlusions=vault_cfg.suspend_new_occlusions,
        suspend_new_clozes=vault_cfg.suspend_new_clozes,
        hash_media=vault_cfg.hash_media,
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
    has_any_creates = any(len(fp.creates) > 0 for fp in plan.file_plans)
    model_ids = ensure_models(
        col,
        basic_model_id=basic_model_id,
        cloze_model_id=cloze_model_id,
        create=has_any_creates,
    )

    from .undo import attach_redo_hook, attach_undo_hook
    attach_undo_hook(col)
    attach_redo_hook(col)

    undo_id: int | None = None
    unmerged_ops: int = 0

    def _ensure_undo() -> None:
        nonlocal undo_id
        if undo_id is None:
            try:
                undo_id = col.add_custom_undo_entry("VaultSync")
            except Exception as exc:
                log_warning(f"Could not initialize custom undo entry: {exc}")
                undo_id = None

    def _merge_undo() -> None:
        nonlocal unmerged_ops
        if undo_id is not None:
            try:
                col.merge_undo_entries(undo_id)
                unmerged_ops = 0
            except Exception as exc:
                log_warning(f"Could not merge undo entries into VaultSync: {exc}")

    def _record_op() -> None:
        nonlocal unmerged_ops
        unmerged_ops += 1
        # Anki's Rust backend undo ring buffer holds ~20-25 entries.
        # Collapsing every 5 operations ensures the target entry never drops off the buffer.
        if unmerged_ops >= 5:
            _merge_undo()

    cards_to_suspend: list[int] = []
    flagged_cards: list[Card] = []

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

        # 2. Pre-validate media files and Flag-Before-Insert protocol: persist flags to Markdown FIRST
        valid_creates: list[NoteCreateOp] = []
        if has_creates:
            for create_op in file_plan.creates:
                missing_media = [m.source_path for m in create_op.media_refs if not m.source_path.is_file()]
                if missing_media:
                    report.warnings.append(
                        f"{file_plan.file_path.name}:{create_op.source_line} ({create_op.card_id}): "
                        f"Missing media file(s): {', '.join(str(p) for p in missing_media)}"
                    )
                    continue
                valid_creates.append(create_op)

            if valid_creates:
                flags_to_write = [
                    (create_op.card, create_op.card_id)
                    for create_op in valid_creates
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

        # 3. Apply Note Creations in Anki with rollback on single failure
        if valid_creates:
            _ensure_undo()
        for create_op in valid_creates:
            try:
                media_added, created_card_ids = apply_note_create(col, create_op, model_ids, hash_media_enabled=plan.hash_media)
                if create_op.card is not None:
                    create_op.card.card_id = create_op.card_id
                    flagged_cards.append(create_op.card)
                report.created_count += 1
                report.media_added_count += media_added

                is_cloze_note = (
                    create_op.model_name == MODEL_NAME_CLOZE
                    or "cloze" in str(create_op.model_name).lower()
                    or (create_op.card is not None and (
                        getattr(create_op.card, "kind", None) in ("cloze", "Cloze")
                        or getattr(create_op.card, "has_cloze_markers", False)
                    ))
                    or "TODO_PROCESS_CLOZES" in create_op.tags
                )
                is_io_note = (
                    create_op.model_name == MODEL_NAME_IMAGE_OCCLUSION
                    or "occlusion" in str(create_op.model_name).lower()
                    or (create_op.card is not None and getattr(create_op.card, "kind", None) in ("image_occlusion", "Image Occlusion"))
                )
                if is_io_note:
                    report.created_image_cloze_count += 1
                elif is_cloze_note:
                    report.created_text_cloze_count += 1
                else:
                    report.created_basic_count += 1

                if (is_io_note and plan.suspend_new_occlusions) or (is_cloze_note and plan.suspend_new_clozes):
                    cards_to_suspend.extend(created_card_ids)
                _record_op()
            except Exception as exc:
                if create_op.card is not None:
                    try:
                        clear_flags([create_op.card])
                    except Exception:
                        pass
                report.warnings.append(
                    f"{file_plan.file_path.name}:{create_op.source_line} ({create_op.card_id}): {exc}"
                )

        # 4. Apply Approved Note Updates in Anki
        if approved_updates:
            _ensure_undo()
        for update_op in approved_updates:
            try:
                media_added, deck_moved = apply_note_update(col, update_op, model_ids, hash_media_enabled=plan.hash_media)
                report.updated_count += 1
                report.media_added_count += media_added
                if deck_moved:
                    report.deck_moved_count += 1

                is_cloze_up = (
                    update_op.model_name == MODEL_NAME_CLOZE
                    or "cloze" in str(update_op.model_name).lower()
                    or (update_op.card is not None and (\
                        getattr(update_op.card, "kind", None) in ("cloze", "Cloze")
                        or getattr(update_op.card, "has_cloze_markers", False)
                    ))
                )
                is_io_up = (
                    update_op.model_name == MODEL_NAME_IMAGE_OCCLUSION
                    or "occlusion" in str(update_op.model_name).lower()
                    or (update_op.card is not None and getattr(update_op.card, "kind", None) in ("image_occlusion", "Image Occlusion"))
                )
                if is_io_up:
                    report.updated_image_cloze_count += 1
                elif is_cloze_up:
                    report.updated_text_cloze_count += 1
                else:
                    report.updated_basic_count += 1
                _record_op()
            except Exception as exc:
                report.warnings.append(
                    f"{file_plan.file_path.name}:{update_op.source_line} ({update_op.card_id}): {exc}"
                )

    # Batch initial card suspension (Image Occlusions and/or Clozes)
    if cards_to_suspend:
        _ensure_undo()
        try:
            if hasattr(col.sched, "suspend_cards"):
                col.sched.suspend_cards(cards_to_suspend)
            elif hasattr(col.sched, "suspendCards"):
                col.sched.suspendCards(cards_to_suspend)
            report.suspended_count += len(cards_to_suspend)
            _merge_undo()
        except Exception as exc:
            report.warnings.append(f"Card suspension error: {exc}")

    # 5. Stage Image Occlusions
    if plan.occlusions_to_stage:
        if occlusion_dir is not None:
            try:
                base_dir = Path(occlusion_dir).expanduser().resolve()
                out_dir, flags, staged = export_occlusions(plan.occlusions_to_stage, base_dir)
                if flags:
                    valid_flags: list[tuple[Card, str]] = []
                    for card, cid in flags:
                        if card.file in report.externally_modified_files:
                            report.warnings.append(
                                f"{card.file.name}:{card.line} ({cid}): File modified externally; skipping image occlusion flag write."
                            )
                            continue
                        valid_flags.append((card, cid))

                    if valid_flags:
                        flagged_count, problems = write_flags(valid_flags)
                        if problems:
                            for p in problems:
                                report.warnings.append(f"Image occlusion flag mismatch: {p}")
                total_imgs = sum(len(imgs) for imgs in staged.values())
                report.occlusions_staged_count = total_imgs
                report.created_image_cloze_count += len(plan.occlusions_to_stage)
                report.created_count += len(plan.occlusions_to_stage)
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

    # Merge undo entries into a single atomic VaultSync operation
    _merge_undo()

    report.flagged_cards = flagged_cards
    if undo_id is not None and flagged_cards:
        from .undo import register_sync_added_cards
        register_sync_added_cards(flagged_cards)

    return report
