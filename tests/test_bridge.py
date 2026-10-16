from __future__ import annotations

import tempfile
from pathlib import Path

from anki.collection import Collection
from anki.consts import MODEL_CLOZE, MODEL_STD
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
