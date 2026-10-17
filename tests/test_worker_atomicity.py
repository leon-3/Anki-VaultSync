from __future__ import annotations

import tempfile
from pathlib import Path

from anki.collection import Collection
from anki.consts import MODEL_CLOZE
from src.bridge import ensure_models
from src.worker import build_sync_plan, execute_sync_plan


def test_cloze_model_type_is_cloze():
    with tempfile.TemporaryDirectory() as tmp_col:
        col = Collection(str(Path(tmp_col) / "col.anki2"))
        try:
            model_ids = ensure_models(col)
            cloze_model = col.models.by_name("M2A Cloze")
            assert cloze_model is not None
            assert cloze_model["type"] == MODEL_CLOZE
        finally:
            col.close()


def test_end_to_end_sync_and_flag_before_insert():
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = Path(tmp_dir)
        vault_path = tmp_path / "vault"
        vault_path.mkdir()

        # Create vault config with empty subdirectories to enable recursive traversal
        (vault_path / "m2a.toml").write_text(
            'vault = "."\ndeck = "Uni::Bio"\ncloze_notes = true\n[subdirectories]\n',
            encoding="utf-8",
        )

        note_dir = vault_path / "Biology"
        note_dir.mkdir()
        note_file = note_dir / "genetics.md"
        note_file.write_text(
            "# Genetics\n---\nWhat is DNA?\nDeoxyribonucleic acid.\n",
            encoding="utf-8",
        )

        col_path = tmp_path / "col.anki2"
        col = Collection(str(col_path))
        try:
            # 1. Stage 1: Build Sync Plan
            plan = build_sync_plan(col, vault_path, default_deck="Uni::Bio")
            assert plan.total_files == 1
            assert plan.total_creates == 1
            assert plan.total_updates == 0
            assert plan.total_unchanged == 0

            # 2. Stage 3: Execute Sync Plan
            report = execute_sync_plan(col, plan, include_updates=True)
            assert report.created_count == 1
            assert len(report.skipped_files) == 0

            # Check that markdown file was flagged with ADDED and %%id%%
            flagged_content = note_file.read_text(encoding="utf-8")
            assert "ADDED: What is DNA? %%" in flagged_content

            # Check that card was added to Anki with matching marker
            notes = col.find_notes("deck:Uni::Bio")
            assert len(notes) == 1
            added_note = col.get_note(notes[0])
            assert added_note["Front"] == "What is DNA?"
            assert "<!--m2a:" in added_note["Back"]

            # 3. Subsequent Stage 1: should now be UNCHANGED
            plan2 = build_sync_plan(col, vault_path, default_deck="Uni::Bio")
            assert plan2.total_creates == 0
            assert plan2.total_updates == 0
            assert plan2.total_unchanged == 1

        finally:
            col.close()


def test_concurrent_edit_skips_modified_file():
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = Path(tmp_dir)
        vault_path = tmp_path / "vault"
        vault_path.mkdir()

        (vault_path / "m2a.toml").write_text(
            'vault = "."\ndeck = "Uni"\n[subdirectories]\n',
            encoding="utf-8",
        )

        note_dir = vault_path / "Topic"
        note_dir.mkdir()
        note_file = note_dir / "note.md"
        note_file.write_text(
            "# Chapter\n---\nQuestion Original\nAnswer Original\n",
            encoding="utf-8",
        )

        col = Collection(str(tmp_path / "col.anki2"))
        try:
            plan = build_sync_plan(col, vault_path)
            assert plan.total_creates == 1

            # Simulate external modification in Obsidian right after scan
            note_file.write_text(
                "# Chapter\n---\nQuestion EDITED OUTSIDE\nAnswer EDITED\n",
                encoding="utf-8",
            )

            report = execute_sync_plan(col, plan)
            assert report.created_count == 0
            assert len(report.skipped_files) == 1
            assert "modified externally" in report.skipped_files[0][1]

            # Zero notes added to Anki
            assert len(col.find_notes("deck:Uni")) == 0
        finally:
            col.close()


def test_flag_write_failure_aborts_note_creation():
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = Path(tmp_dir)
        vault_path = tmp_path / "vault"
        vault_path.mkdir()

        (vault_path / "m2a.toml").write_text(
            'vault = "."\ndeck = "Uni"\n[subdirectories]\n',
            encoding="utf-8",
        )

        note_dir = vault_path / "Topic"
        note_dir.mkdir()
        note_file = note_dir / "readonly.md"
        note_file.write_text(
            "# Chapter\n---\nReadOnly Question\nAnswer\n",
            encoding="utf-8",
        )

        col = Collection(str(tmp_path / "col.anki2"))
        try:
            plan = build_sync_plan(col, vault_path)
            assert plan.total_creates == 1

            # Make file read-only to simulate flag write failure
            note_file.chmod(0o444)

            report = execute_sync_plan(col, plan)
            assert report.created_count == 0
            assert len(report.skipped_files) == 1

            # Guarantee zero notes created in Anki
            assert len(col.find_notes("deck:Uni")) == 0
        finally:
            note_file.chmod(0o666)
            col.close()


def test_bulk_sync_undo_merge_limit_does_not_fail():
    """Verify that syncing 50+ cards does not overflow Anki's undo stack ring buffer."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = Path(tmp_dir)
        vault_path = tmp_path / "vault"
        vault_path.mkdir()

        (vault_path / "m2a.toml").write_text(
            'vault = "."\ndeck = "BulkDeck"\n[subdirectories]\n',
            encoding="utf-8",
        )

        note_dir = vault_path / "Topic"
        note_dir.mkdir()
        note_file = note_dir / "bulk.md"

        card_blocks = ["# Bulk Notes\n"]
        for i in range(50):
            card_blocks.append(f"---\nQuestion Number {i}\nAnswer Number {i}\n")
        note_file.write_text("\n".join(card_blocks), encoding="utf-8")

        col = Collection(str(tmp_path / "col.anki2"))
        try:
            plan = build_sync_plan(col, vault_path)
            assert plan.total_creates == 50

            # Must not raise 'target undo op not found'
            report = execute_sync_plan(col, plan)
            assert report.created_count == 50
            assert len(report.skipped_files) == 0
            assert len(col.find_notes("deck:BulkDeck")) == 50
        finally:
            col.close()


def test_image_hash_normalization_prevents_false_updates():
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = Path(tmp_dir)
        vault_path = tmp_path / "vault"
        vault_path.mkdir()

        (vault_path / "m2a.toml").write_text(
            'vault = "."\ndeck = "Uni"\nhash_media = true\n[subdirectories]\n',
            encoding="utf-8",
        )

        note_dir = vault_path / "Topic"
        note_dir.mkdir()
        img_file = note_dir / "pasted image 123.png"
        img_file.write_bytes(b"fake_png_data")

        note_file = note_dir / "note.md"
        note_file.write_text(
            "# Chapter\n---\nQuestion with image\nAnswer with ![[pasted image 123.png]]\n",
            encoding="utf-8",
        )

        col = Collection(str(tmp_path / "col.anki2"))
        try:
            # 1. First sync creates the note
            plan = build_sync_plan(col, vault_path)
            assert plan.total_creates == 1
            staged_name = plan.file_plans[0].creates[0].media_refs[0].staged_filename
            assert staged_name != "pasted image 123.png"

            report = execute_sync_plan(col, plan)
            assert report.created_count == 1

            # 2. Simulate note in Anki holding the raw unhashed filename (or URL-encoded)
            note_ids = col.find_notes("deck:Uni")
            assert len(note_ids) == 1
            anki_note = col.get_note(note_ids[0])
            assert staged_name in anki_note["Back"]
            anki_note["Back"] = anki_note["Back"].replace(staged_name, "pasted%20image%20123.png")
            col.update_note(anki_note)

            # 3. Subsequent build_sync_plan: normalization should identify it as UNCHANGED
            plan2 = build_sync_plan(col, vault_path)
            assert plan2.total_creates == 0
            assert plan2.total_updates == 0
            assert plan2.total_unchanged == 1

            # 4. If actual text changes in markdown, an update IS detected
            note_file.write_text(
                note_file.read_text(encoding="utf-8").replace("Answer with", "Updated Answer with"),
                encoding="utf-8",
            )
            plan3 = build_sync_plan(col, vault_path)
            assert plan3.total_creates == 0
            assert plan3.total_updates == 1
            assert plan3.total_unchanged == 0
        finally:
            col.close()


def test_occlusions_filtering_respects_added_and_restage():
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = Path(tmp_dir)
        vault_path = tmp_path / "vault"
        vault_path.mkdir()
        occ_dir = tmp_path / "occlusions"
        occ_dir.mkdir()

        (vault_path / "m2a.toml").write_text(
            'vault = "."\ndeck = "Anatomy"\nimage_occlusion_mode = "stage"\nre_stage_occlusions = false\n[subdirectories]\n',
            encoding="utf-8",
        )

        note_dir = vault_path / "Topic"
        note_dir.mkdir()
        img_file = note_dir / "diagram.png"
        img_file.write_bytes(b"fake_png_data")

        note_file = note_dir / "occlusion.md"
        note_file.write_text(
            "# Chapter\n---\nImage Cloze\n![[diagram.png]]\n",
            encoding="utf-8",
        )

        col = Collection(str(tmp_path / "col.anki2"))
        try:
            # 1. Initial scan: occlusion should be ready to stage
            plan1 = build_sync_plan(col, vault_path)
            assert len(plan1.occlusions_to_stage) == 1

            # 2. Stage occlusions via execute_sync_plan
            report = execute_sync_plan(col, plan1, occlusion_dir=occ_dir)
            assert report.occlusions_staged_count == 1
            # Note file should have received the ADDED flag
            content = note_file.read_text(encoding="utf-8")
            assert "ADDED" in content

            # Verify index.md was created in occlusions subfolder
            index_path = occ_dir / "occlusions" / "index.md"
            assert index_path.is_file()
            assert "Tag:" in index_path.read_text(encoding="utf-8")
            assert len(report.staged_occlusions_by_tag) > 0

            # 3. Subsequent scan: already-added occlusion should NOT be listed
            plan2 = build_sync_plan(col, vault_path)
            assert len(plan2.occlusions_to_stage) == 0

            # 4. If re_stage_occlusions is explicitly enabled in config:
            (vault_path / "m2a.toml").write_text(
                'vault = "."\ndeck = "Anatomy"\nimage_occlusion_mode = "stage"\nre_stage_occlusions = true\n[subdirectories]\n',
                encoding="utf-8",
            )
            plan3 = build_sync_plan(col, vault_path)
            assert len(plan3.occlusions_to_stage) == 1
        finally:
            col.close()


def test_orphan_detection_in_sync_plan():
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = Path(tmp_dir)
        vault_path = tmp_path / "vault"
        vault_path.mkdir()

        (vault_path / "m2a.toml").write_text(
            'vault = "."\ndeck = "Uni"\n[subdirectories]\n',
            encoding="utf-8",
        )

        note_file = vault_path / "orphan_test.md"
        note_file.write_text(
            "# Chapter\n---\nQuestion to be deleted\nAnswer to be deleted\n",
            encoding="utf-8",
        )

        col = Collection(str(tmp_path / "col.anki2"))
        try:
            # 1. Sync note to Anki
            plan = build_sync_plan(col, vault_path)
            assert plan.total_creates == 1
            report = execute_sync_plan(col, plan)
            assert report.created_count == 1

            # 2. Delete the note from vault to make it an orphan in Anki
            note_file.unlink()

            # 3. Scan should detect the orphan without TypeError
            plan2 = build_sync_plan(col, vault_path)
            assert len(plan2.orphans) == 1
            orphan = plan2.orphans[0]
            assert orphan.note_id > 0
            assert orphan.model_name == "M2A Basic"
            assert "Uni" in orphan.deck_names
            assert orphan.preview_question != ""
        finally:
            col.close()


def test_write_occlusion_index_appends_batches(tmp_path):
    from src.worker import _write_occlusion_index
    out_dir = tmp_path / "occlusions"
    out_dir.mkdir()

    # Batch 1
    _write_occlusion_index(out_dir, {"Tag1": ["img1.png", "img2.png"]})
    text1 = (out_dir / "index.md").read_text("utf-8")
    assert "Tag1" in text1
    assert "img1.png" in text1

    # Batch 2
    _write_occlusion_index(out_dir, {"Tag2": ["img3.png"]})
    text2 = (out_dir / "index.md").read_text("utf-8")
    assert "Tag1" in text2
    assert "Tag2" in text2
    assert "img1.png" in text2
    assert "img3.png" in text2


def test_stage1_scan_is_strictly_read_only():
    """Verify that Stage 1 build_sync_plan does NOT mutate Anki (no new decks, no new models)."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = Path(tmp_dir)
        vault_path = tmp_path / "vault"
        vault_path.mkdir()

        (vault_path / "m2a.toml").write_text(
            'vault = "."\ndeck = "NonExistentDeck::SubDeck"\n[subdirectories]\n',
            encoding="utf-8",
        )

        note_dir = vault_path / "Topic"
        note_dir.mkdir()
        note_file = note_dir / "card.md"
        note_file.write_text(
            "# Chapter\n---\nNew Question\nNew Answer\n",
            encoding="utf-8",
        )

        col = Collection(str(tmp_path / "col.anki2"))
        try:
            assert col.decks.by_name("NonExistentDeck::SubDeck") is None
            assert col.models.by_name("M2A Basic") is None
            assert col.models.by_name("M2A Cloze") is None

            plan = build_sync_plan(col, vault_path, default_deck="NonExistentDeck::SubDeck")
            assert plan.total_creates == 1

            # Ensure neither the target deck nor models were created during scan
            assert col.decks.by_name("NonExistentDeck::SubDeck") is None
            assert col.models.by_name("M2A Basic") is None
            assert col.models.by_name("M2A Cloze") is None
        finally:
            col.close()


def test_duplicate_markdown_card_id_is_skipped_and_diagnosed():
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = Path(tmp_dir)
        vault_path = tmp_path / "vault"
        vault_path.mkdir()

        (vault_path / "m2a.toml").write_text(
            'vault = "."\ndeck = "Uni"\n[subdirectories]\n',
            encoding="utf-8",
        )

        note_dir = vault_path / "Topic"
        note_dir.mkdir()
        note_file = note_dir / "dups.md"
        # Two cards with identical %%dup999%% card ID
        note_file.write_text(
            "# Chapter\n---\nADDED: First Card %%dup999%%\nFirst Answer\n---\nADDED: Second Duplicate Card %%dup999%%\nSecond Answer\n",
            encoding="utf-8",
        )

        col = Collection(str(tmp_path / "col.anki2"))
        try:
            diagnostics: list[str] = []
            plan = build_sync_plan(col, vault_path, trace_fn=lambda msg: diagnostics.append(msg))

            # By default, cards marked ADDED but missing in Anki are not recreated (avoid zombie cards)
            assert plan.total_creates == 0
            assert len(plan.missing_in_anki) == 1
            assert any("Duplicate card ID 'dup999'" in d for d in diagnostics)

            # When explicit recreation is requested, the first card is planned for creation
            rec_plan = build_sync_plan(col, vault_path, recreate_missing=True)
            assert rec_plan.total_creates == 1
        finally:
            col.close()


def test_native_image_occlusion_creation_and_suspension():
    """Verify native Image Occlusion creation, media copy, Comments ID marker, and card suspension."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = Path(tmp_dir)
        vault_path = tmp_path / "vault"
        vault_path.mkdir()

        (vault_path / "m2a.toml").write_text(
            'vault = "."\ndeck = "Anatomy"\nimage_occlusion_mode = "native"\nsuspend_new_occlusions = true\n',
            encoding="utf-8",
        )

        note_dir = vault_path / "Topic"
        note_dir.mkdir()
        img_file = note_dir / "brain.png"
        img_file.write_bytes(b"fake_png_data")

        note_file = note_dir / "brain_note.md"
        note_file.write_text(
            "# Neuroanatomy\n---\nImage Cloze\n![[brain.png]]\n",
            encoding="utf-8",
        )

        col = Collection(str(tmp_path / "col.anki2"))
        try:
            plan = build_sync_plan(col, vault_path)
            assert plan.total_creates == 1
            create_op = plan.file_plans[0].creates[0]
            assert create_op.model_name == "Image Occlusion"
            assert create_op.target_deck_name == "Anatomy"
            assert "TODO_PROCESS_OCCLUSION" in create_op.tags
            assert "<!--m2a:" in create_op.fields["Comments"]
            assert create_op.fields["Occlusion"] == ""
            assert "<img src=" in create_op.fields["Image"]

            report = execute_sync_plan(col, plan)
            assert report.created_count == 1
            assert report.media_added_count == 1
            assert report.suspended_count == 1

            # Verify note in Anki
            nids = col.find_notes("deck:Anatomy")
            assert len(nids) == 1
            note = col.get_note(nids[0])
            assert note.note_type()["name"] == "Image Occlusion"
            assert "TODO_PROCESS_OCCLUSION" in note.tags
            assert "<!--m2a:" in note["Comments"]

            # Verify card is suspended
            cards = note.cards()
            assert len(cards) == 1
            assert cards[0].queue == -1

            # Verify media exists in collection.media
            media_files = list(Path(col.media.dir()).glob("*.png"))
            assert len(media_files) == 1

            # Verify markdown file was flagged with ADDED
            assert "ADDED: Image Cloze %%" in note_file.read_text(encoding="utf-8")
        finally:
            col.close()


def test_native_image_occlusion_selective_field_freeze_and_tag_safety():
    """Verify that existing Occlusion SVG masks are never overwritten and tag is not re-added."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = Path(tmp_dir)
        vault_path = tmp_path / "vault"
        vault_path.mkdir()

        (vault_path / "m2a.toml").write_text(
            'vault = "."\ndeck = "Anatomy"\nimage_occlusion_mode = "native"\n',
            encoding="utf-8",
        )

        note_dir = vault_path / "Topic"
        note_dir.mkdir()
        img_file = note_dir / "diagram.png"
        img_file.write_bytes(b"fake_png_data")

        note_file = note_dir / "diagram.md"
        note_file.write_text(
            "# Chapter\n---\nImage Cloze\n![[diagram.png]]\n",
            encoding="utf-8",
        )

        col = Collection(str(tmp_path / "col.anki2"))
        try:
            # 1. Initial creation
            plan1 = build_sync_plan(col, vault_path)
            execute_sync_plan(col, plan1)

            nids = col.find_notes("deck:Anatomy")
            note = col.get_note(nids[0])

            # 2. Simulate user drawing masks in Anki and removing TODO_PROCESS_OCCLUSION tag
            user_mask_svg = "<svg><rect x=10 y=10 width=50 height=50/></svg>{{c1::}}"
            note["Occlusion"] = user_mask_svg
            note.tags = [t for t in note.tags if t != "TODO_PROCESS_OCCLUSION"]
            col.update_note(note)

            # 3. Rescan without changes in Obsidian: must be a pure No-Op
            plan2 = build_sync_plan(col, vault_path)
            assert plan2.total_creates == 0
            assert plan2.total_updates == 0
            assert plan2.total_unchanged == 1

            # 4. User updates note Header / question in Obsidian
            content = note_file.read_text(encoding="utf-8")
            updated_content = content.replace("Image Cloze", "Image Cloze - Anatomy Question")
            note_file.write_text(updated_content, encoding="utf-8")

            plan3 = build_sync_plan(col, vault_path)
            assert plan3.total_updates == 1
            update_op = plan3.file_plans[0].updates[0]
            # Occlusion field in new_fields must match the Anki user mask
            assert update_op.new_fields["Occlusion"] == user_mask_svg
            # TODO_PROCESS_OCCLUSION must NOT be re-added
            assert "TODO_PROCESS_OCCLUSION" not in update_op.new_tags

            report3 = execute_sync_plan(col, plan3)
            assert report3.updated_count == 1

            # 5. Verify Anki note fields after update
            reloaded_note = col.get_note(nids[0])
            assert reloaded_note["Occlusion"] == user_mask_svg
            assert "TODO_PROCESS_OCCLUSION" not in reloaded_note.tags
            assert "Anatomy Question" in reloaded_note["Header"]
        finally:
            col.close()


def test_freeze_image_occlusions_setting():
    """Verify that freeze_image_occlusions=true skips updates entirely."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = Path(tmp_dir)
        vault_path = tmp_path / "vault"
        vault_path.mkdir()

        (vault_path / "m2a.toml").write_text(
            'vault = "."\ndeck = "Anatomy"\nimage_occlusion_mode = "native"\nfreeze_image_occlusions = true\n',
            encoding="utf-8",
        )

        note_dir = vault_path / "Topic"
        note_dir.mkdir()
        img_file = note_dir / "diagram.png"
        img_file.write_bytes(b"fake_png_data")

        note_file = note_dir / "diagram.md"
        note_file.write_text(
            "# Chapter\n---\nImage Cloze\n![[diagram.png]]\n",
            encoding="utf-8",
        )

        col = Collection(str(tmp_path / "col.anki2"))
        try:
            plan1 = build_sync_plan(col, vault_path)
            execute_sync_plan(col, plan1)

            # Modify Markdown
            content = note_file.read_text(encoding="utf-8")
            note_file.write_text(content.replace("# Chapter", "# Modified"), encoding="utf-8")

            # Scan should treat as unchanged because freeze_image_occlusions is True
            plan2 = build_sync_plan(col, vault_path)
            assert plan2.total_creates == 0
            assert plan2.total_updates == 0
            assert plan2.total_unchanged == 1
        finally:
            col.close()


def test_batch_suspension_text_clozes():
    """Verify suspend_new_clozes suspends cloze cards, while basic cards remain active."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = Path(tmp_dir)
        vault_path = tmp_path / "vault"
        vault_path.mkdir()

        (vault_path / "m2a.toml").write_text(
            'vault = "."\ndeck = "TestDeck"\ncloze_notes = true\nsuspend_new_clozes = true\n',
            encoding="utf-8",
        )

        note_file = vault_path / "notes.md"
        note_file.write_text(
            "# Chapter\n---\nBasic Q\nBasic A\n---\nCloze\n{{c1::alpha}} and {{c2::beta}}\n",
            encoding="utf-8",
        )

        col = Collection(str(tmp_path / "col.anki2"))
        try:
            plan = build_sync_plan(col, vault_path)
            assert plan.total_creates == 2
            report = execute_sync_plan(col, plan)
            assert report.created_count == 2
            # The cloze note generated 2 cards (c1, c2), both suspended
            assert report.suspended_count == 2

            # Basic note card should be active (queue == 0)
            all_deck_cards = [col.get_card(cid) for cid in col.find_cards("deck:TestDeck")]
            basic_cards = [c for c in all_deck_cards if c.note().note_type()["name"] == "M2A Basic"]
            assert len(basic_cards) == 1
            assert basic_cards[0].queue == 0

            # Cloze note cards should be suspended (queue == -1)
            cloze_cards = [c for c in all_deck_cards if c.note().note_type()["name"] == "M2A Cloze"]
            assert len(cloze_cards) == 2
            assert all(c.queue == -1 for c in cloze_cards)
        finally:
            col.close()




def test_batch_suspension_text_clozes_effective_when_cloze_notes_omitted():
    """Verify suspend_new_clozes = true enables effective cloze notes and suspends cards."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = Path(tmp_dir)
        vault_path = tmp_path / "vault"
        vault_path.mkdir()

        # Notice cloze_notes is NOT specified here (defaults to False)
        (vault_path / "m2a.toml").write_text(
            """vault = "."
deck = "TestDeck"
suspend_new_clozes = true
""",
            encoding="utf-8",
        )

        note_file = vault_path / "notes.md"
        note_file.write_text(
            """# Chapter
---
Basic Q
Basic A
---
Cloze
{{c1::alpha}} and {{c2::beta}}
""",
            encoding="utf-8",
        )

        col = Collection(str(tmp_path / "col.anki2"))
        try:
            plan = build_sync_plan(col, vault_path)
            assert plan.total_creates == 2
            report = execute_sync_plan(col, plan)
            assert report.created_count == 2
            assert report.suspended_count == 2

            all_deck_cards = [col.get_card(cid) for cid in col.find_cards("deck:TestDeck")]
            basic_cards = [c for c in all_deck_cards if c.note().note_type()["name"] == "M2A Basic"]
            assert len(basic_cards) == 1
            assert basic_cards[0].queue == 0

            cloze_cards = [c for c in all_deck_cards if c.note().note_type()["name"] == "M2A Cloze"]
            assert len(cloze_cards) == 2
            assert all(c.queue == -1 for c in cloze_cards)
        finally:
            col.close()


def test_native_image_occlusion_folder_relocation_updates_deck_without_touching_masks():
    """Verify moving note in Obsidian updates Anki deck while preserving Occlusion SVG mask."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = Path(tmp_dir)
        vault_path = tmp_path / "vault"
        vault_path.mkdir()

        (vault_path / "m2a.toml").write_text(
            'vault = "."\ndeck = "RootDeck"\nimage_occlusion_mode = "native"\n'
            '[deck_routing]\n"FolderA" = "Deck::FolderA"\n"FolderB" = "Deck::FolderB"\n',
            encoding="utf-8",
        )

        dir_a = vault_path / "FolderA"
        dir_a.mkdir()
        img_file = dir_a / "diagram.png"
        img_file.write_bytes(b"fake_png_data")

        note_file_a = dir_a / "diagram.md"
        note_file_a.write_text(
            "# Chapter\n---\nImage Cloze\n![[diagram.png]]\n",
            encoding="utf-8",
        )

        col = Collection(str(tmp_path / "col.anki2"))
        try:
            # 1. Initial sync to FolderA -> Deck::FolderA
            plan1 = build_sync_plan(col, vault_path)
            execute_sync_plan(col, plan1)

            nids = col.find_notes("deck:Deck::FolderA")
            assert len(nids) == 1
            note = col.get_note(nids[0])

            # 2. User draws mask in Anki
            user_mask_svg = "<svg><circle cx=20 cy=20 r=15/></svg>{{c1::}}"
            note["Occlusion"] = user_mask_svg
            col.update_note(note)

            # 3. User moves file to FolderB in Obsidian
            dir_b = vault_path / "FolderB"
            dir_b.mkdir()
            # Move image and markdown
            (dir_b / "diagram.png").write_bytes(img_file.read_bytes())
            img_file.unlink()
            note_content = note_file_a.read_text(encoding="utf-8")
            note_file_a.unlink()
            (dir_b / "diagram.md").write_text(note_content, encoding="utf-8")

            # 4. Sync should detect deck move
            plan2 = build_sync_plan(col, vault_path)
            assert plan2.total_creates == 0
            assert plan2.total_updates == 1
            update_op = plan2.file_plans[0].updates[0]
            assert update_op.deck_changed is True
            assert update_op.target_deck_name == "Deck::FolderB"
            assert update_op.new_fields["Occlusion"] == user_mask_svg

            report2 = execute_sync_plan(col, plan2)
            assert report2.updated_count == 1
            assert report2.deck_moved_count == 1

            # 5. Verify card is now in Deck::FolderB with mask intact
            nids_old = col.find_notes("deck:Deck::FolderA")
            assert len(nids_old) == 0
            nids_new = col.find_notes("deck:Deck::FolderB")
            assert len(nids_new) == 1
            reloaded_note = col.get_note(nids_new[0])
            assert reloaded_note["Occlusion"] == user_mask_svg
        finally:
            col.close()


def test_orphan_detection_flags_deleted_image_occlusions():
    """Verify orphan detection correctly flags deleted Image Occlusion cards in Obsidian."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = Path(tmp_dir)
        vault_path = tmp_path / "vault"
        vault_path.mkdir()

        (vault_path / "m2a.toml").write_text(
            'vault = "."\ndeck = "Anatomy"\nimage_occlusion_mode = "native"\n',
            encoding="utf-8",
        )

        dir_topic = vault_path / "Topic"
        dir_topic.mkdir()
        img = dir_topic / "kidney.png"
        img.write_bytes(b"fake_kidney_bytes")

        note_file = dir_topic / "kidney.md"
        note_file.write_text(
            "# Chapter\n---\nImage Cloze: Kidney Anatomy\n![[kidney.png]]\n",
            encoding="utf-8",
        )

        col = Collection(str(tmp_path / "col.anki2"))
        try:
            plan = build_sync_plan(col, vault_path)
            assert plan.total_creates == 1
            report = execute_sync_plan(col, plan)
            assert report.created_count == 1

            # Delete the markdown file in Obsidian
            note_file.unlink()

            # Rescan
            plan2 = build_sync_plan(col, vault_path)
            assert len(plan2.orphans) == 1
            orphan = plan2.orphans[0]
            assert orphan.model_name == "Image Occlusion"
            assert "Kidney Anatomy" in orphan.preview_question
            assert "Anatomy" in orphan.deck_names
        finally:
            col.close()

def test_card_deleted_in_anki_lifecycle_and_deletion_flag():
    """Verify that deleting a note in Anki does NOT resurrect it on sync,
    is reported in missing_in_anki, can be flagged as DELETED in Obsidian,
    and can optionally be recreated if requested."""
    from markdown2anki.flags import write_deleted_flags

    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = Path(tmp_dir)
        vault_path = tmp_path / "vault"
        vault_path.mkdir()

        (vault_path / "m2a.toml").write_text(
            'vault = "."\ndeck = "TestDeck"\n[subdirectories]\n',
            encoding="utf-8",
        )

        note_file = vault_path / "lifecycle.md"
        note_file.write_text(
            "# Science\n---\nWhat is gravity?\nA fundamental interaction.\n",
            encoding="utf-8",
        )

        col = Collection(str(tmp_path / "col.anki2"))
        try:
            # 1. Sync card initially
            plan1 = build_sync_plan(col, vault_path)
            assert plan1.total_creates == 1
            execute_sync_plan(col, plan1)

            # Card is now flagged in Obsidian
            content1 = note_file.read_text(encoding="utf-8")
            assert "ADDED: What is gravity? %%" in content1

            # 2. User deletes card in Anki
            nids = col.find_notes("deck:TestDeck")
            assert len(nids) == 1
            col.remove_notes(nids)
            assert len(col.find_notes("deck:TestDeck")) == 0

            # 3. Next scan: card MUST NOT be recreated automatically (no zombie cards)
            plan2 = build_sync_plan(col, vault_path)
            assert plan2.total_creates == 0
            assert plan2.total_updates == 0
            assert len(plan2.missing_in_anki) == 1
            missing_rec = plan2.missing_in_anki[0]
            assert "gravity" in missing_rec.preview_question
            assert missing_rec.target_deck == "TestDeck"
            assert missing_rec.card is not None

            # 4. If explicit recreation requested: planned into creates
            rec_plan = build_sync_plan(col, vault_path, recreate_missing=True)
            assert rec_plan.total_creates == 1
            assert len(rec_plan.missing_in_anki) == 0

            # 5. User marks missing card as DELETED in Obsidian
            flagged, problems = write_deleted_flags([missing_rec.card])
            assert flagged == 1 and not problems
            content2 = note_file.read_text(encoding="utf-8")
            assert "DELETED: What is gravity? %%" in content2

            # 6. Subsequent scan: card marked DELETED is completely skipped
            plan3 = build_sync_plan(col, vault_path)
            assert plan3.total_creates == 0
            assert plan3.total_updates == 0
            assert len(plan3.missing_in_anki) == 0
            assert len(plan3.orphans) == 0
        finally:
            col.close()
