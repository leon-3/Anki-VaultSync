from __future__ import annotations

import tempfile
from pathlib import Path

from anki.collection import Collection
from anki.consts import MODEL_CLOZE, MODEL_STD
from anki.models import NotetypeId
from src.bridge import (
    apply_note_create,
    apply_note_update,
    build_existing_note_index,
    ensure_models,
)
from src.contracts import MediaItem, NoteCreateOp, NoteUpdateOp


def test_ensure_models_creates_correct_types():
    with tempfile.TemporaryDirectory() as tmp_dir:
        col_path = Path(tmp_dir) / "collection.anki2"
        col = Collection(str(col_path))
        try:
            model_ids = ensure_models(col)
            assert "M2A Basic" in model_ids
            assert "M2A Cloze" in model_ids

            basic_model = col.models.by_name("M2A Basic")
            assert basic_model is not None
            assert basic_model["type"] == MODEL_STD
            fields_basic = [f["name"] for f in basic_model["flds"]]
            assert fields_basic == ["Front", "Back"]

            cloze_model = col.models.by_name("M2A Cloze")
            assert cloze_model is not None
            assert cloze_model["type"] == MODEL_CLOZE
            fields_cloze = [f["name"] for f in cloze_model["flds"]]
            assert fields_cloze == ["Text", "Back Extra"]
        finally:
            col.close()


def test_custom_notetype_id_and_field_copying():
    """Verify that specifying custom existing note type IDs uses existing styling and extra fields."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        col_path = Path(tmp_dir) / "collection.anki2"
        col = Collection(str(col_path))
        try:
            # 1. Create a custom note type with an extra 'Title' field
            custom_model = col.models.new("Custom Basic With Title")
            custom_model["type"] = MODEL_STD
            col.models.add_field(custom_model, col.models.new_field("Front"))
            col.models.add_field(custom_model, col.models.new_field("Back"))
            col.models.add_field(custom_model, col.models.new_field("Title"))
            card_tpl = col.models.new_template("Card 1")
            card_tpl["qfmt"] = "{{Front}}"
            card_tpl["afmt"] = "{{FrontSide}}<hr id=answer>{{Back}}"
            col.models.add_template(custom_model, card_tpl)
            col.models.add_dict(custom_model)

            created_model = col.models.by_name("Custom Basic With Title")
            assert created_model is not None
            custom_mid = int(created_model["id"])

            # 2. Call ensure_models with basic_model_id pointing to our custom note type
            model_ids = ensure_models(col, basic_model_id=custom_mid)
            assert model_ids["M2A Basic"] == custom_mid

            # 3. Create a note and verify fields are copied into the existing note type definition
            create_op = NoteCreateOp(
                card_id="cst123",
                model_name="M2A Basic",
                target_deck_name="TestDeck",
                fields={
                    "Front": "Custom Question?",
                    "Back": "Custom Answer.<!--m2a:cst123-->",
                },
                tags=["custom"],
                source_file=Path("/vault/test.md"),
                source_line=1,
                media_refs=[],
            )
            apply_note_create(col, create_op, model_ids)

            # 4. Verify note in collection
            note_ids = col.find_notes("deck:TestDeck")
            assert len(note_ids) == 1
            note = col.get_note(note_ids[0])
            assert note.mid == custom_mid
            assert note["Front"] == "Custom Question?"
            assert "Custom Answer." in note["Back"]

            # 5. Build index and check fields
            index = build_existing_note_index(col, model_ids)
            assert "cst123" in index
            assert index["cst123"].fields["Front"] == "Custom Question?"
            assert "Custom Answer." in index["cst123"].fields["Back"]
        finally:
            col.close()


def test_note_lifecycle_and_single_pass_index():
    with tempfile.TemporaryDirectory() as tmp_dir:
        col_path = Path(tmp_dir) / "collection.anki2"
        col = Collection(str(col_path))
        try:
            model_ids = ensure_models(col)

            # 1. Create note
            create_op = NoteCreateOp(
                card_id="k3f9qz",
                model_name="M2A Basic",
                target_deck_name="Course::Algorithms",
                fields={
                    "Front": "What is Dijkstra's algorithm?",
                    "Back": "Shortest path algorithm.<!--m2a:k3f9qz-->",
                },
                tags=["CompSci", "Algorithms"],
                source_file=Path("/vault/algo.md"),
                source_line=15,
                media_refs=[],
            )
            media_count = apply_note_create(col, create_op, model_ids)
            assert media_count == 0

            # Verify deck was created
            deck_id = col.decks.id("Course::Algorithms")
            assert deck_id > 0

            # 2. Build single-pass index
            index = build_existing_note_index(col, model_ids)
            assert "k3f9qz" in index
            record = index["k3f9qz"]
            assert record.model_name == "M2A Basic"
            assert record.fields["Front"] == "What is Dijkstra's algorithm?"
            assert "Shortest path algorithm." in record.fields["Back"]
            assert "CompSci" in record.tags
            assert "Algorithms" in record.tags
            assert deck_id in record.deck_ids

            # 3. Update note and change deck
            update_op = NoteUpdateOp(
                note_id=record.note_id,
                card_id="k3f9qz",
                model_name="M2A Basic",
                target_deck_name="Course::AdvancedGraphTheory",
                old_deck_names=["Course::Algorithms"],
                old_fields=record.fields,
                new_fields={
                    "Front": "What is Dijkstra's algorithm? (Updated)",
                    "Back": "Single-source shortest path algorithm.<!--m2a:k3f9qz-->",
                },
                old_tags=record.tags,
                new_tags=["CompSci", "Graphs"],
                deck_changed=True,
                fields_changed=True,
                tags_changed=True,
                source_file=Path("/vault/algo.md"),
                source_line=15,
                media_refs=[],
                approved=True,
            )
            _, deck_moved = apply_note_update(col, update_op)
            assert deck_moved is True

            # Verify card moved to new deck
            new_deck_id = col.decks.id("Course::AdvancedGraphTheory")
            note = col.get_note(record.note_id)
            assert note["Front"] == "What is Dijkstra's algorithm? (Updated)"
            assert "Graphs" in note.tags
            assert "Algorithms" not in note.tags
            for card in note.cards():
                assert card.did == new_deck_id

        finally:
            col.close()


def test_filtered_deck_movement():
    """Verify that cards in a filtered deck have odid updated rather than corrupting did."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        col_path = Path(tmp_dir) / "collection.anki2"
        col = Collection(str(col_path))
        try:
            model_ids = ensure_models(col)
            initial_deck_id = col.decks.id("OriginalDeck")
            filtered_deck_id = col.decks.id("FilteredDeck")

            create_op = NoteCreateOp(
                card_id="flt101",
                model_name="M2A Basic",
                target_deck_name="OriginalDeck",
                fields={
                    "Front": "Question 1",
                    "Back": "Answer 1<!--m2a:flt101-->",
                },
                tags=["tag1"],
                source_file=Path("/vault/note.md"),
                source_line=1,
                media_refs=[],
            )
            apply_note_create(col, create_op, model_ids)

            note_id = col.find_notes("deck:OriginalDeck")[0]
            note = col.get_note(note_id)
            card = note.cards()[0]

            # Simulate card being in a filtered deck
            card.did = filtered_deck_id
            card.odid = initial_deck_id
            col.update_card(card)

            # Update note with target deck changed to TargetDeck
            update_op = NoteUpdateOp(
                note_id=note_id,
                card_id="flt101",
                model_name="M2A Basic",
                target_deck_name="TargetDeck",
                old_deck_names=["OriginalDeck"],
                old_fields={"Front": "Question 1", "Back": "Answer 1<!--m2a:flt101-->"},
                new_fields={"Front": "Question 1", "Back": "Answer 1<!--m2a:flt101-->"},
                old_tags=["tag1"],
                new_tags=["tag1"],
                deck_changed=True,
                fields_changed=False,
                tags_changed=False,
                source_file=Path("/vault/note.md"),
                source_line=1,
                media_refs=[],
                approved=True,
            )

            _, deck_moved = apply_note_update(col, update_op)
            assert deck_moved is True

            target_deck_id = col.decks.id("TargetDeck")
            reloaded_card = col.get_card(card.id)
            # The card should remain in the filtered deck (did unchanged),
            # but its home deck (odid) must be updated to target_deck_id!
            assert reloaded_card.did == filtered_deck_id
            assert reloaded_card.odid == target_deck_id
        finally:
            col.close()
