from __future__ import annotations

import re
from typing import TYPE_CHECKING

from anki.collection import Collection
from anki.consts import MODEL_CLOZE, MODEL_STD
from anki.models import NotetypeId
from anki.notes import NoteId
from anki.utils import split_fields
from markdown2anki.models import (
    BASIC_FIELDS,
    CLOZE_FIELDS,
    KIND_BASIC,
    KIND_CLOZE,
    MODEL_NAME_BASIC,
    MODEL_NAME_CLOZE,
    load_template,
)

from .contracts import ExistingNoteRecord, MediaItem, NoteCreateOp, NoteUpdateOp

M2A_MARKER_RE = re.compile(r"<!--m2a:([a-zA-Z0-9_-]+)-->")


def ensure_models(
    col: Collection,
    repair_templates: bool = False,
    basic_model_id: int | None = None,
    cloze_model_id: int | None = None,
) -> dict[str, int]:
    """Ensures Basic and Cloze note types exist and returns {model_name: mid}."""
    model_ids: dict[str, int] = {}
    specs = [
        (MODEL_NAME_BASIC, BASIC_FIELDS, MODEL_STD, KIND_BASIC, basic_model_id),
        (MODEL_NAME_CLOZE, CLOZE_FIELDS, MODEL_CLOZE, KIND_CLOZE, cloze_model_id),
    ]

    for model_name, expected_fields, model_type, tpl_key, preferred_id in specs:
        existing = None
        # 1. Prioritize lookup by specified ID
        if preferred_id is not None:
            existing = col.models.get(NotetypeId(preferred_id))

        # 2. Fall back to lookup by name
        if existing is None:
            existing = col.models.by_name(model_name)

        tpl = load_template(tpl_key)

        if existing is not None:
            # Use the existing note type definition and styling
            if repair_templates:
                existing["type"] = model_type
                existing["css"] = tpl.css
                if existing["tmpls"]:
                    existing["tmpls"][0]["qfmt"] = tpl.qfmt
                    existing["tmpls"][0]["afmt"] = tpl.afmt
                col.models.update_dict(existing)
            model_ids[model_name] = int(existing["id"])
            continue

        # 3. Create new model if neither ID nor name found
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
        assert created is not None
        model_ids[model_name] = int(created["id"])

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
        f"SELECT nid, did FROM cards WHERE nid IN "
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
) -> tuple[dict[str, str], int]:
    """Copies media files to collection.media and substitutes any collision-renamed filenames."""
    updated_fields = dict(fields)
    added_count = 0
    for item in media_refs:
        if not item.source_path.is_file():
            raise FileNotFoundError(f"Missing media file: {item.source_path}")
        stored_name = col.media.write_data(item.staged_filename, item.source_path.read_bytes())
        added_count += 1
        if stored_name != item.staged_filename:
            for k, html_val in updated_fields.items():
                updated_fields[k] = html_val.replace(
                    f'src="{item.staged_filename}"',
                    f'src="{stored_name}"',
                )
    return updated_fields, added_count


def apply_note_create(col: Collection, op: NoteCreateOp, model_ids: dict[str, int]) -> int:
    """Creates a new note in Anki, ensuring the target deck exists."""
    target_mid = model_ids.get(op.model_name)
    if target_mid is None:
        target_mid = model_ids.get(MODEL_NAME_CLOZE if "Cloze" in op.model_name else MODEL_NAME_BASIC)
    assert target_mid is not None, f"Model ID for {op.model_name} not found"

    model = col.models.get(NotetypeId(target_mid))
    assert model is not None, f"Model {op.model_name} (ID {target_mid}) not found"

    note = col.new_note(model)
    final_fields, media_count = import_media_and_rewrite_fields(col, op.fields, op.media_refs)

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
    return media_count


def apply_note_update(col: Collection, op: NoteUpdateOp) -> tuple[int, bool]:
    """Updates note fields and tags, and moves cards if target deck changed."""
    note = col.get_note(NoteId(op.note_id))
    final_fields, media_count = import_media_and_rewrite_fields(col, op.new_fields, op.media_refs)

    model = note.note_type()
    model_field_names = [f["name"] for f in model["flds"]] if model else []
    field_items = list(final_fields.items())

    for idx, (field_name, val) in enumerate(field_items):
        if field_name in note:
            note[field_name] = val
        elif idx < len(model_field_names):
            note[model_field_names[idx]] = val

    note.tags = col.tags.split(col.tags.join(op.new_tags))
    col.update_note(note)

    deck_moved = False
    if op.deck_changed:
        target_deck_id = col.decks.id(op.target_deck_name)
        cids_to_move = [c.id for c in note.cards() if c.did != target_deck_id]
        if cids_to_move:
            col.set_deck(cids_to_move, target_deck_id)
            deck_moved = True

    return media_count, deck_moved
