from __future__ import annotations

from pathlib import Path
import re
import urllib.parse
from typing import TYPE_CHECKING

from .logger import log_warning

from anki.collection import Collection
from anki.consts import MODEL_CLOZE, MODEL_STD
from anki.models import NotetypeId
from anki.notes import NoteId
from anki.utils import split_fields
from markdown2anki.models import (
    BASIC_FIELDS,
    CLOZE_FIELDS,
    IMAGE_OCCLUSION_FIELDS,
    KIND_BASIC,
    KIND_CLOZE,
    KIND_IMAGE_OCCLUSION,
    MODEL_NAME_BASIC,
    MODEL_NAME_CLOZE,
    MODEL_NAME_IMAGE_OCCLUSION,
    load_template,
)

from .contracts import ExistingNoteRecord, MediaItem, NoteCreateOp, NoteUpdateOp

M2A_MARKER_RE = re.compile(r"<!--m2a:([a-zA-Z0-9_-]+)-->")


def ensure_image_occlusion_model(col: Collection, create: bool = True) -> int | None:
    """Resolves the stock 'Image Occlusion' notetype ID.

    Strictly uses 'Image Occlusion' as the sole model name and identifier.
    If absent, falls back to stock kind 6 (standard in Anki 23.10+ / 24+).
    When create=True and absent, initializes via backend add_image_occlusion_notetype().
    """
    # 1. Direct lookup by standard model name "Image Occlusion"
    m = col.models.by_name(MODEL_NAME_IMAGE_OCCLUSION)
    if m is not None:
        return int(m["id"])

    # 2. Lookup by original stock kind (6 for Image Occlusion in Anki 23.10+)
    for candidate in col.models.all():
        if getattr(candidate, "originalStockKind", None) == 6 or candidate.get("originalStockKind") == 6:
            return int(candidate["id"])

    if not create:
        return None

    # 3. Programmatic initialization via stock notetype factory if absent
    if hasattr(col.models, "add_image_occlusion"):
        created = col.models.add_image_occlusion()
        if created:
            return int(created["id"])
    elif hasattr(col, "_backend") and hasattr(col._backend, "add_image_occlusion_notetype"):
        try:
            col._backend.add_image_occlusion_notetype()
            created = col.models.by_name(MODEL_NAME_IMAGE_OCCLUSION)
            if created:
                return int(created["id"])
        except Exception:
            pass

    return None


def ensure_models(
    col: Collection,
    basic_model_id: int | None = None,
    cloze_model_id: int | None = None,
    create: bool = True,
) -> dict[str, int]:
    """Ensures M2A Basic and M2A Cloze models exist in collection, returning name -> mid mapping.

    Args:
        col: Anki Collection instance.
        basic_model_id: Optional user-configured notetype ID for Basic cards.
        cloze_model_id: Optional user-configured notetype ID for Cloze cards.
        create: If True, creates models if neither specified ID nor default name exists.
    """
    model_ids: dict[str, int] = {}

    specs = [
        (
            MODEL_NAME_BASIC,
            BASIC_FIELDS,
            MODEL_STD,
            KIND_BASIC,
            basic_model_id,
        ),
        (
            MODEL_NAME_CLOZE,
            CLOZE_FIELDS,
            MODEL_CLOZE,
            KIND_CLOZE,
            cloze_model_id,
        ),
    ]

    for model_name, expected_fields, model_type, tpl_key, preferred_id in specs:
        existing = None
        # 1. Prioritize lookup by specified ID
        if preferred_id is not None:
            existing = col.models.get(NotetypeId(preferred_id))

        # 2. Fall back to lookup by name
        if existing is None:
            existing = col.models.by_name(model_name)

        if existing is not None:
            flds = existing.get("flds", [])
            if len(flds) < 2:
                raise ValueError(
                    f"Note type '{existing.get('name')}' (ID {existing.get('id')}) has only {len(flds)} field(s). "
                    "VaultSync requires at least 2 fields to store the synchronization ID marker."
                )
            if existing.get("type", 0) != model_type:
                expected_str = "Cloze" if model_type == MODEL_CLOZE else "Standard (Basic)"
                raise ValueError(
                    f"Note type '{existing.get('name')}' (ID {existing.get('id')}) is not a {expected_str} note type."
                )
            model_ids[model_name] = int(existing["id"])
            continue

        if not create:
            continue

        # 3. Create new model if neither ID nor name found
        tpl = load_template(tpl_key)
        model = col.models.new(model_name)
        model["type"] = model_type
        model["css"] = tpl.css

        for field_name in expected_fields:
            col.models.add_field(model, col.models.new_field(field_name))

        template_name = "Card 1" if model_type == MODEL_STD else "Cloze"
        card_tpl = col.models.new_template(template_name)
        card_tpl["qfmt"] = tpl.qfmt
        card_tpl["afmt"] = tpl.afmt
        col.models.add_template(model, card_tpl)

        col.models.add_dict(model)
        created = col.models.by_name(model_name)
        if created is None:
            raise RuntimeError(f"Failed to create note type '{model_name}' in collection")
        model_ids[model_name] = int(created["id"])

    io_mid = ensure_image_occlusion_model(col, create=create)
    if io_mid is not None:
        model_ids[MODEL_NAME_IMAGE_OCCLUSION] = io_mid

    return model_ids


def build_existing_note_index(
    col: Collection, model_ids: dict[str, int]
) -> dict[str, ExistingNoteRecord]:
    """Single-pass O(M) SQL index extraction for all M2A cards in the collection."""
    mid_to_name: dict[int, str] = {}
    mid_to_fields: dict[int, tuple[str, ...]] = {}
    for name, mid in model_ids.items():
        m = col.models.get(NotetypeId(mid))
        if m is not None:
            mid_to_name[mid] = m["name"]
            mid_to_fields[mid] = tuple(f["name"] for f in m["flds"])
        else:
            mid_to_name[mid] = name
            mid_to_fields[mid] = BASIC_FIELDS if name == MODEL_NAME_BASIC else CLOZE_FIELDS

    mids = tuple(model_ids.values())
    if not mids:
        return {}

    placeholders = ",".join("?" * len(mids))

    note_rows = col.db.all(
        f"SELECT id, mid, tags, flds FROM notes WHERE mid IN ({placeholders})",
        *mids,
    )
    card_rows = col.db.all(
        f"SELECT nid, CASE WHEN odid != 0 THEN odid ELSE did END FROM cards WHERE nid IN "
        f"(SELECT id FROM notes WHERE mid IN ({placeholders}))",
        *mids,
    )

    nid_to_dids: dict[int, set[int]] = {}
    for nid, did in card_rows:
        nid_to_dids.setdefault(int(nid), set()).add(int(did))

    index: dict[str, ExistingNoteRecord] = {}
    for nid, mid, raw_tags, raw_flds in note_rows:
        match = M2A_MARKER_RE.search(raw_flds)
        if not match:
            continue
        card_id = match.group(1)
        if card_id in index:
            log_warning(
                f"Duplicate card ID <!--m2a:{card_id}--> detected in Anki: note {nid} conflicts with note {index[card_id].note_id}. Skipping duplicate note {nid}."
            )
            continue
        field_names = mid_to_fields.get(int(mid), BASIC_FIELDS)
        field_values = split_fields(raw_flds)
        fields_dict = dict(zip(field_names, field_values))
        tags_list = col.tags.split(raw_tags)

        index[card_id] = ExistingNoteRecord(
            note_id=int(nid),
            card_id=card_id,
            model_id=int(mid),
            model_name=mid_to_name.get(int(mid), MODEL_NAME_BASIC),
            fields=fields_dict,
            tags=tags_list,
            deck_ids=nid_to_dids.get(int(nid), set()),
        )
    return index


def import_media_and_rewrite_fields(
    col: Collection,
    fields: dict[str, str],
    media_refs: list[MediaItem],
    hash_media_enabled: bool = True,
) -> tuple[dict[str, str], int]:
    """Copies media files to collection.media and substitutes any collision-renamed filenames."""
    if not media_refs:
        return dict(fields), 0

    # 1. Pre-validate all files before mutating collection.media
    for item in media_refs:
        if not item.source_path.is_file():
            raise FileNotFoundError(f"Missing media file: {item.source_path}")

    updated_fields = dict(fields)
    added_count = 0
    try:
        media_dir = Path(col.media.dir())
    except Exception:
        media_dir = None

    for item in media_refs:
        # Content-hashed filenames are immutable; skip disk write if already present
        if hash_media_enabled and media_dir is not None and item.staged_filename != item.source_path.name:
            if (media_dir / item.staged_filename).is_file():
                continue

        stored_name = col.media.write_data(item.staged_filename, item.source_path.read_bytes())
        added_count += 1
        if stored_name != item.staged_filename:
            old_name_quoted = re.escape(item.staged_filename)
            old_name_url = re.escape(urllib.parse.quote(item.staged_filename))
            pat = re.compile(rf"src=(['\"])(?:{old_name_quoted}|{old_name_url})\1", re.IGNORECASE)
            for k, html_val in updated_fields.items():
                updated_fields[k] = pat.sub(f'src="{stored_name}"', html_val)
    return updated_fields, added_count


def apply_note_create(
    col: Collection,
    op: NoteCreateOp,
    model_ids: dict[str, int] | None = None,
    hash_media_enabled: bool = True,
) -> tuple[int, list[int]]:
    """Creates a new Anki note and places all generated cards in the target deck.

    Returns:
        tuple of (media_added_count, list_of_card_ids).
    """
    if model_ids is not None:
        target_mid = model_ids.get(op.model_name)
        if target_mid is None:
            target_mid = model_ids.get(
                MODEL_NAME_CLOZE if "Cloze" in op.model_name
                else (MODEL_NAME_IMAGE_OCCLUSION if "Occlusion" in op.model_name else MODEL_NAME_BASIC)
            )
    else:
        m = col.models.by_name(op.model_name)
        if m is not None:
            target_mid = int(m["id"])
        else:
            fallback = col.models.by_name(
                MODEL_NAME_CLOZE if "Cloze" in op.model_name
                else (MODEL_NAME_IMAGE_OCCLUSION if "Occlusion" in op.model_name else MODEL_NAME_BASIC)
            )
            target_mid = int(fallback["id"]) if fallback else None

    if target_mid is None:
        raise ValueError(f"Model ID for '{op.model_name}' not found")

    model = col.models.get(NotetypeId(target_mid))
    if model is None:
        raise ValueError(f"Model '{op.model_name}' (ID {target_mid}) not found in collection")

    note = col.new_note(model)
    final_fields, media_count = import_media_and_rewrite_fields(col, op.fields, op.media_refs, hash_media_enabled=hash_media_enabled)

    # Copy fields into the note type definition
    model_field_names = [f["name"] for f in model["flds"]]
    field_items = list(final_fields.items())

    for idx, (field_name, val) in enumerate(field_items):
        if field_name in note:
            note[field_name] = val
        elif idx < len(model_field_names):
            note[model_field_names[idx]] = val

    note.tags = col.tags.split(col.tags.join(op.tags))
    deck_id = col.decks.id(op.target_deck_name)
    col.add_note(note, deck_id)
    card_ids = [c.id for c in note.cards()]
    return media_count, card_ids


def apply_note_update(
    col: Collection,
    op: NoteUpdateOp,
    model_ids: dict[str, int] | None = None,
    hash_media_enabled: bool = True,
) -> tuple[int, bool]:
    """Updates note fields and tags, converts model if changed, and moves cards if target deck changed."""
    note = col.get_note(NoteId(op.note_id))

    # Resolve target model ID
    if model_ids is not None:
        target_mid = model_ids.get(op.model_name)
        if target_mid is None:
            target_mid = model_ids.get(
                MODEL_NAME_CLOZE if "Cloze" in op.model_name
                else (MODEL_NAME_IMAGE_OCCLUSION if "Occlusion" in op.model_name else MODEL_NAME_BASIC)
            )
    else:
        m = col.models.by_name(op.model_name)
        if m is not None:
            target_mid = int(m["id"])
        else:
            fallback = col.models.by_name(
                MODEL_NAME_CLOZE if "Cloze" in op.model_name
                else (MODEL_NAME_IMAGE_OCCLUSION if "Occlusion" in op.model_name else MODEL_NAME_BASIC)
            )
            target_mid = int(fallback["id"]) if fallback else None

    # Handle model conversion (e.g. Basic <-> Cloze)
    model_converted = False
    if target_mid is not None and note.mid != target_mid:
        old_model = note.note_type()
        new_model = col.models.get(NotetypeId(target_mid))
        if old_model is not None and new_model is not None and hasattr(col.models, "change"):
            fmap = {i: i if i < len(new_model.get("flds", [])) else None for i in range(len(old_model.get("flds", [])))}
            cmap = {i: i if i < len(new_model.get("tmpls", [])) else 0 for i in range(len(old_model.get("tmpls", [])))}
            try:
                col.models.change(old_model, [note.id], new_model, fmap=fmap, cmap=cmap)
                note = col.get_note(NoteId(op.note_id))
                model_converted = (note.mid == target_mid)
            except Exception as exc:
                raise RuntimeError(f"Failed to convert note {op.note_id} to model '{op.model_name}': {exc}") from exc

    # Skip media I/O and field updating if fields are untouched
    if op.fields_changed or model_converted:
        final_fields, media_count = import_media_and_rewrite_fields(col, op.new_fields, op.media_refs, hash_media_enabled=hash_media_enabled)

        model = note.note_type()
        model_field_names = [f["name"] for f in model["flds"]] if model else []
        field_items = list(final_fields.items())

        for idx, (field_name, val) in enumerate(field_items):
            if field_name in note:
                note[field_name] = val
            elif idx < len(model_field_names):
                note[model_field_names[idx]] = val
    else:
        media_count = 0

    note.tags = col.tags.split(col.tags.join(op.new_tags))
    col.update_note(note)

    deck_moved = False
    if op.deck_changed:
        target_deck_id = col.decks.id(op.target_deck_name)
        target_deck = col.decks.get(target_deck_id)
        is_filtered = (
            (hasattr(col.decks, "is_filtered") and col.decks.is_filtered(target_deck_id))
            or (target_deck is not None and target_deck.get("dyn", 0) != 0)
        )
        if is_filtered:
            raise ValueError(f"Target deck '{op.target_deck_name}' is a filtered deck and cannot hold regular cards.")

        cids_to_move: list[int] = []
        for card in note.cards():
            if card.odid != 0:
                # Card is in a filtered deck; update its home deck (odid)
                if card.odid != target_deck_id:
                    card.odid = target_deck_id
                    col.update_card(card)
                    deck_moved = True
            elif card.did != target_deck_id:
                cids_to_move.append(card.id)

        if cids_to_move:
            col.set_deck(cids_to_move, target_deck_id)
            deck_moved = True

    return media_count, deck_moved
