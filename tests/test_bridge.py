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
    ensure_image_occlusion_model,
    ensure_models,
    import_media_and_rewrite_fields,
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
            media_count, card_ids = apply_note_create(col, create_op, model_ids)
            assert media_count == 0
            assert len(card_ids) == 1

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


def test_filtered_deck_indexing_uses_odid():
    """Verify that build_existing_note_index indexes odid (home deck) when card is in filtered deck."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        col_path = Path(tmp_dir) / "collection.anki2"
        col = Collection(str(col_path))
        try:
            model_ids = ensure_models(col)
            initial_deck_id = col.decks.id("RealHomeDeck")
            filtered_deck_id = col.decks.id("FilteredDeck")

            create_op = NoteCreateOp(
                card_id="odid999",
                model_name="M2A Basic",
                target_deck_name="RealHomeDeck",
                fields={
                    "Front": "Question Filtered",
                    "Back": "Answer Filtered<!--m2a:odid999-->",
                },
                tags=["test"],
                source_file=Path("/vault/test.md"),
                source_line=1,
                media_refs=[],
            )
            apply_note_create(col, create_op, model_ids)

            note_id = col.find_notes("deck:RealHomeDeck")[0]
            note = col.get_note(note_id)
            card = note.cards()[0]

            # Put card in filtered deck
            card.did = filtered_deck_id
            card.odid = initial_deck_id
            col.update_card(card)

            # Build index and verify deck_ids has initial_deck_id, not filtered_deck_id
            index = build_existing_note_index(col, model_ids)
            assert "odid999" in index
            rec = index["odid999"]
            assert initial_deck_id in rec.deck_ids
            assert filtered_deck_id not in rec.deck_ids
        finally:
            col.close()


def test_ensure_models_read_only():
    """Verify that ensure_models(create=False) does not modify the collection."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        col_path = Path(tmp_dir) / "collection.anki2"
        col = Collection(str(col_path))
        try:
            models_before = col.models.all_names_and_ids()
            model_ids = ensure_models(col, create=False)
            assert "M2A Basic" not in model_ids
            assert "M2A Cloze" not in model_ids
            models_after = col.models.all_names_and_ids()
            assert len(models_before) == len(models_after)
        finally:
            col.close()


def test_apply_note_update_model_conversion():
    """Verify that apply_note_update converts a note between Basic and Cloze models."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        col_path = Path(tmp_dir) / "collection.anki2"
        col = Collection(str(col_path))
        try:
            model_ids = ensure_models(col)
            basic_mid = model_ids["M2A Basic"]
            cloze_mid = model_ids["M2A Cloze"]

            create_op = NoteCreateOp(
                card_id="conv100",
                model_name="M2A Basic",
                target_deck_name="TestDeck",
                fields={
                    "Front": "Original Question",
                    "Back": "Original Answer<!--m2a:conv100-->",
                },
                tags=["original"],
                source_file=Path("/vault/test.md"),
                source_line=1,
                media_refs=[],
            )
            apply_note_create(col, create_op, model_ids)

            note_id = col.find_notes("deck:TestDeck")[0]
            note = col.get_note(note_id)
            assert note.mid == basic_mid

            update_op = NoteUpdateOp(
                note_id=note_id,
                card_id="conv100",
                model_name="M2A Cloze",
                target_deck_name="TestDeck",
                old_deck_names=["TestDeck"],
                old_fields={"Front": "Original Question", "Back": "Original Answer<!--m2a:conv100-->"},
                new_fields={
                    "Text": "This is {{c1::cloze}} text<!--m2a:conv100-->",
                    "Back Extra": "Some extra notes",
                },
                old_tags=["original"],
                new_tags=["converted"],
                deck_changed=False,
                fields_changed=True,
                tags_changed=True,
                source_file=Path("/vault/test.md"),
                source_line=1,
                media_refs=[],
                approved=True,
            )
            apply_note_update(col, update_op, model_ids)

            reloaded_note = col.get_note(note_id)
            assert reloaded_note.mid == cloze_mid
            assert "This is {{c1::cloze}}" in reloaded_note["Text"]
            assert reloaded_note["Back Extra"] == "Some extra notes"
        finally:
            col.close()


def test_import_media_skips_existing_hashed_files():
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = Path(tmp_dir)
        col_path = tmp_path / "collection.anki2"
        col = Collection(str(col_path))
        try:
            img_file = tmp_path / "original.png"
            img_file.write_bytes(b"image_content_123")

            media_item_hashed = MediaItem(
                source_path=img_file,
                staged_filename="hashed_12345.png",
            )

            # 1. First import writes the file
            fields = {"Front": "Look at <img src=\"hashed_12345.png\">"}
            updated_fields, added_count = import_media_and_rewrite_fields(
                col, fields, [media_item_hashed], hash_media_enabled=True
            )
            assert added_count == 1
            assert (Path(col.media.dir()) / "hashed_12345.png").is_file()

            # 2. Second import of the same hashed file skips write_data
            updated_fields2, added_count2 = import_media_and_rewrite_fields(
                col, fields, [media_item_hashed], hash_media_enabled=True
            )
            assert added_count2 == 0
            assert updated_fields2 == fields

            # 3. If unhashed (filename matches source_path.name), it is not skipped
            media_item_unhashed = MediaItem(
                source_path=img_file,
                staged_filename="original.png",
            )
            _, added_unhashed1 = import_media_and_rewrite_fields(
                col, {"Front": "img"}, [media_item_unhashed], hash_media_enabled=True
            )
            assert added_unhashed1 == 1

            _, added_unhashed2 = import_media_and_rewrite_fields(
                col, {"Front": "img"}, [media_item_unhashed], hash_media_enabled=True
            )
            assert added_unhashed2 == 1
        finally:
            col.close()


def test_import_media_pre_validates_missing_files_atomically():
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = Path(tmp_dir)
        col_path = tmp_path / "collection.anki2"
        col = Collection(str(col_path))
        try:
            valid_img = tmp_path / "valid.png"
            valid_img.write_bytes(b"valid_bytes")

            missing_img = tmp_path / "does_not_exist.png"

            item_valid = MediaItem(source_path=valid_img, staged_filename="val_hash.png")
            item_missing = MediaItem(source_path=missing_img, staged_filename="miss_hash.png")

            # Calling import_media_and_rewrite_fields should raise FileNotFoundError
            # and NOT write val_hash.png to col.media.dir()
            import pytest
            with pytest.raises(FileNotFoundError) as exc_info:
                import_media_and_rewrite_fields(col, {"Front": "x"}, [item_valid, item_missing])

            assert "Missing media file" in str(exc_info.value)
            assert not (Path(col.media.dir()) / "val_hash.png").exists()
        finally:
            col.close()


def test_apply_note_update_skips_media_io_when_fields_unchanged():
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = Path(tmp_dir)
        col_path = tmp_path / "collection.anki2"
        col = Collection(str(col_path))
        try:
            model_ids = ensure_models(col)

            img_file = tmp_path / "card_img.png"
            img_file.write_bytes(b"card_img")

            item = MediaItem(source_path=img_file, staged_filename="card_img_hash.png")

            create_op = NoteCreateOp(
                card_id="skipmed1",
                model_name="M2A Basic",
                target_deck_name="TestDeck",
                fields={"Front": "Q <img src=\"card_img_hash.png\">", "Back": "A<!--m2a:skipmed1-->"},
                tags=["tag1"],
                source_file=Path("/vault/test.md"),
                source_line=1,
                media_refs=[item],
            )
            apply_note_create(col, create_op, model_ids)

            note_id = col.find_notes("deck:TestDeck")[0]

            # Now provide a broken media_ref whose source_path does NOT exist
            missing_item = MediaItem(source_path=tmp_path / "gone.png", staged_filename="card_img_hash.png")

            # Update ONLY tags (fields_changed=False)
            update_op = NoteUpdateOp(
                note_id=note_id,
                card_id="skipmed1",
                model_name="M2A Basic",
                target_deck_name="TestDeck",
                old_deck_names=["TestDeck"],
                old_fields={"Front": "Q <img src=\"card_img_hash.png\">", "Back": "A<!--m2a:skipmed1-->"},
                new_fields={"Front": "Q <img src=\"card_img_hash.png\">", "Back": "A<!--m2a:skipmed1-->"},
                old_tags=["tag1"],
                new_tags=["tag1", "tag2"],
                deck_changed=False,
                fields_changed=False,
                tags_changed=True,
                source_file=Path("/vault/test.md"),
                source_line=1,
                media_refs=[missing_item],
                approved=True,
            )

            # Must NOT raise FileNotFoundError because fields_changed=False skips media processing
            media_count, deck_moved = apply_note_update(col, update_op, model_ids)
            assert media_count == 0
            assert deck_moved is False

            reloaded_note = col.get_note(note_id)
            assert "tag2" in reloaded_note.tags
        finally:
            col.close()


def test_custom_note_type_with_less_than_2_fields_raises_error():
    with tempfile.TemporaryDirectory() as tmp_dir:
        col = Collection(str(Path(tmp_dir) / "collection.anki2"))
        try:
            # Create a 1-field model with 1 template
            one_field_model = col.models.new("OneFieldModel")
            col.models.add_field(one_field_model, col.models.new_field("OnlyField"))
            card_tpl = col.models.new_template("Card 1")
            card_tpl["qfmt"] = "{{OnlyField}}"
            card_tpl["afmt"] = "{{FrontSide}}"
            col.models.add_template(one_field_model, card_tpl)
            col.models.add_dict(one_field_model)
            mid = int(col.models.by_name("OneFieldModel")["id"])

            import pytest
            with pytest.raises(ValueError) as exc_info:
                ensure_models(col, basic_model_id=mid)

            assert "has only 1 field" in str(exc_info.value)
            assert "requires at least 2 fields" in str(exc_info.value)
        finally:
            col.close()


def test_ensure_image_occlusion_model_resolution_and_fallback():
    """Verify standard 'Image Occlusion' model resolution and backend restoration if deleted."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        col = Collection(str(Path(tmp_dir) / "collection.anki2"))
        try:
            # 1. Standard resolution on fresh collection
            io_mid = ensure_image_occlusion_model(col, create=False)
            assert io_mid is not None
            model = col.models.get(NotetypeId(io_mid))
            assert model is not None
            assert model["name"] == "Image Occlusion"
            assert [f["name"] for f in model["flds"]] == [
                "Occlusion", "Image", "Header", "Back Extra", "Comments"
            ]

            # 2. ensure_models registers Image Occlusion in returned dictionary
            model_ids = ensure_models(col, create=False)
            assert "Image Occlusion" in model_ids
            assert model_ids["Image Occlusion"] == io_mid

            # 3. If model is removed from collection, create=False returns None
            col.models.remove(NotetypeId(io_mid))
            assert ensure_image_occlusion_model(col, create=False) is None

            # 4. create=True restores / recreates standard stock Image Occlusion notetype
            restored_mid = ensure_image_occlusion_model(col, create=True)
            assert restored_mid is not None
            restored = col.models.get(NotetypeId(restored_mid))
            assert restored is not None
            assert restored["name"] == "Image Occlusion"
        finally:
            col.close()


def test_apply_note_update_rejects_filtered_deck_target():
    """Verify moving cards directly to a filtered/dynamic deck raises ValueError."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        col_path = Path(tmp_dir) / "collection.anki2"
        col = Collection(str(col_path))
        try:
            model_ids = ensure_models(col)
            create_op = NoteCreateOp(
                card_id="flt_target_test",
                model_name="M2A Basic",
                target_deck_name="NormalDeck",
                fields={
                    "Front": "Question Filtered Target",
                    "Back": "Answer<!--m2a:flt_target_test-->",
                },
                tags=["test"],
                source_file=Path("/vault/test.md"),
                source_line=1,
                media_refs=[],
            )
            apply_note_create(col, create_op, model_ids)
            note_id = col.find_notes("deck:NormalDeck")[0]

            # Create a dynamic/filtered deck
            col.decks.new_filtered("FilteredTargetDeck")

            update_op = NoteUpdateOp(
                note_id=note_id,
                card_id="flt_target_test",
                model_name="M2A Basic",
                target_deck_name="FilteredTargetDeck",
                old_deck_names=["NormalDeck"],
                old_fields={"Front": "Question Filtered Target", "Back": "Answer<!--m2a:flt_target_test-->"},
                new_fields={"Front": "Question Filtered Target", "Back": "Answer<!--m2a:flt_target_test-->"},
                old_tags=["test"],
                new_tags=["test"],
                deck_changed=True,
                fields_changed=False,
                tags_changed=False,
                source_file=Path("/vault/test.md"),
                source_line=1,
                media_refs=[],
                approved=True,
            )

            import pytest
            with pytest.raises(ValueError, match="is a filtered deck"):
                apply_note_update(col, update_op, model_ids)
        finally:
            col.close()
