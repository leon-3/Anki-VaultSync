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






def test_custom_notetype_dynamic_fields_no_false_updates():
    """Verify that custom notetypes with non-standard field names and >2 fields
    do not trigger 100% false updates on subsequent scans, and preserve extra fields."""
    from anki.consts import MODEL_STD
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = Path(tmp_dir)
        vault_path = tmp_path / "vault"
        vault_path.mkdir()

        (vault_path / "m2a.toml").write_text(
            'vault = "."\ndeck = "GermanDeck"\n[subdirectories]\n',
            encoding="utf-8",
        )

        note_file = vault_path / "german.md"
        note_file.write_text(
            "# Deutsch\n---\nDeutsche Frage\nDeutsche Antwort\n",
            encoding="utf-8",
        )

        col = Collection(str(tmp_path / "col.anki2"))
        try:
            # 1. Create a custom note type with German field names and a 3rd extra field 'Notizen'
            custom_model = col.models.new("Deutsches Modell")
            custom_model["type"] = MODEL_STD
            col.models.add_field(custom_model, col.models.new_field("Vorderseite"))
            col.models.add_field(custom_model, col.models.new_field("Rückseite"))
            col.models.add_field(custom_model, col.models.new_field("Notizen"))
            card_tpl = col.models.new_template("Karte 1")
            card_tpl["qfmt"] = "{{Vorderseite}}"
            card_tpl["afmt"] = "{{FrontSide}}<hr id=answer>{{Rückseite}}<br>{{Notizen}}"
            col.models.add_template(custom_model, card_tpl)
            col.models.add_dict(custom_model)
            custom_mid = int(col.models.by_name("Deutsches Modell")["id"])

            # 2. First scan and sync using basic_model_id=custom_mid
            plan1 = build_sync_plan(col, vault_path, basic_model_id=custom_mid)
            assert plan1.total_creates == 1
            report1 = execute_sync_plan(col, plan1, basic_model_id=custom_mid)
            assert report1.created_count == 1

            nids = col.find_notes("deck:GermanDeck")
            assert len(nids) == 1
            note = col.get_note(nids[0])
            assert note.mid == custom_mid
            assert note["Vorderseite"] == "Deutsche Frage"
            assert "Deutsche Antwort" in note["Rückseite"]

            # Populate extra field in Anki directly
            note["Notizen"] = "Wichtige Quelle: Lehrbuch S. 42"
            col.update_note(note)

            # 3. Subsequent scan: MUST be completely UNCHANGED (0 false updates!)
            plan2 = build_sync_plan(col, vault_path, basic_model_id=custom_mid)
            assert plan2.total_creates == 0
            assert plan2.total_updates == 0
            assert plan2.total_unchanged == 1

            # 4. Update the note in Markdown
            content = note_file.read_text(encoding="utf-8")
            note_file.write_text(content.replace("Deutsche Frage", "Deutsche Frage (Aktualisiert)"), encoding="utf-8")

            plan3 = build_sync_plan(col, vault_path, basic_model_id=custom_mid)
            assert plan3.total_updates == 1
            up_op = plan3.file_plans[0].updates[0]
            assert up_op.new_fields["Vorderseite"] == "Deutsche Frage (Aktualisiert)"
            assert up_op.new_fields["Notizen"] == "Wichtige Quelle: Lehrbuch S. 42"

            report3 = execute_sync_plan(col, plan3, basic_model_id=custom_mid)
            assert report3.updated_count == 1

            # Verify extra field is preserved in Anki DB after update
            reloaded_note = col.get_note(nids[0])
            assert reloaded_note["Vorderseite"] == "Deutsche Frage (Aktualisiert)"
            assert reloaded_note["Notizen"] == "Wichtige Quelle: Lehrbuch S. 42"
        finally:
            col.close()


def test_missing_media_prevalidation_skips_flagging():
    """Verify that a card referencing non-existent media is not flagged with ADDED in Markdown."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = Path(tmp_dir)
        vault_path = tmp_path / "vault"
        vault_path.mkdir()

        (vault_path / "m2a.toml").write_text(
            """vault = "."
deck = "MediaDeck"
[subdirectories]
""",
            encoding="utf-8",
        )

        img_file = vault_path / "temporary_image.png"
        img_file.write_bytes(b"temp_image_data")

        note_file = vault_path / "missing_media.md"
        note_file.write_text(
            """# Bio
---
Cell structure
Look at ![[temporary_image.png]]
""",
            encoding="utf-8",
        )

        col = Collection(str(tmp_path / "col.anki2"))
        try:
            plan = build_sync_plan(col, vault_path)
            assert plan.total_creates == 1

            # Delete the media file right before execution to simulate missing media
            img_file.unlink()

            report = execute_sync_plan(col, plan)
            assert report.created_count == 0
            assert len(report.warnings) == 1
            assert "Missing media file" in report.warnings[0]

            # Markdown file must NOT have received ADDED flag
            content = note_file.read_text(encoding="utf-8")
            assert "ADDED" not in content
            assert len(col.find_notes("deck:MediaDeck")) == 0
        finally:
            col.close()


def test_insert_failure_rolls_back_flags(monkeypatch):
    """Verify that if apply_note_create fails, the ADDED flag is rolled back in Markdown."""
    from src import worker

    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = Path(tmp_dir)
        vault_path = tmp_path / "vault"
        vault_path.mkdir()

        (vault_path / "m2a.toml").write_text(
            'vault = "."\ndeck = "FailDeck"\n[subdirectories]\n',
            encoding="utf-8",
        )

        note_file = vault_path / "fail_note.md"
        note_file.write_text(
            "# Topic\n---\nFail Question\nFail Answer\n",
            encoding="utf-8",
        )

        col = Collection(str(tmp_path / "col.anki2"))
        try:
            plan = build_sync_plan(col, vault_path)
            assert plan.total_creates == 1

            def mock_fail(*args, **kwargs):
                raise RuntimeError("Simulated Anki DB error during insert")

            monkeypatch.setattr(worker, "apply_note_create", mock_fail)

            report = execute_sync_plan(col, plan)
            assert report.created_count == 0
            assert len(report.warnings) == 1
            assert "Simulated Anki DB error" in report.warnings[0]

            # Verify that ADDED flag was rolled back!
            content = note_file.read_text(encoding="utf-8")
            assert "ADDED" not in content
            assert "Fail Question" in content
            # Not in flagged_cards
            assert len(report.flagged_cards) == 0
        finally:
            col.close()


def test_unflag_completed_occlusions_lifecycle():
    """Verify get_occlusion_status and unflag_completed_occlusions work end-to-end,
    correctly unsuspending cards and removing TODO_PROCESS_OCCLUSION only from masked notes."""
    from src.worker import get_occlusion_status, unflag_completed_occlusions
    from markdown2anki.constants import OCCLUSION_TAG

    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = Path(tmp_dir)
        vault_path = tmp_path / "vault"
        vault_path.mkdir()

        (vault_path / "m2a.toml").write_text(
            'vault = "."\ndeck = "Anatomy"\nimage_occlusion_mode = "native"\nsuspend_new_occlusions = true\n',
            encoding="utf-8",
        )
        img1 = vault_path / "skull.png"
        img1.write_bytes(b"skull_img_data")
        img2 = vault_path / "brain.png"
        img2.write_bytes(b"brain_img_data")

        note_file = vault_path / "anatomy.md"
        note_file.write_text(
            """# Anatomy
---
Image Cloze
![[skull.png]]
---
Image Cloze
![[brain.png]]
""",
            encoding="utf-8",
        )

        col = Collection(str(tmp_path / "col.anki2"))
        try:
            # 1. Sync creates both Image Occlusions, suspended and tagged with TODO_PROCESS_OCCLUSION
            plan = build_sync_plan(col, vault_path)
            assert plan.total_creates == 2
            report = execute_sync_plan(col, plan)
            assert report.created_count == 2
            assert report.suspended_count == 2

            # Check status before user draws masks
            status1 = get_occlusion_status(col)
            assert status1.total == 2
            assert status1.awaiting_masks == 2
            assert status1.ready_to_unflag == 0

            # 2. Simulate user drawing a mask on note 1 in Anki
            nids = col.find_notes(f"tag:{OCCLUSION_TAG}")
            assert len(nids) == 2
            note1 = col.get_note(nids[0])
            note1["Occlusion"] = "<svg><rect x='10' y='10' width='50' height='20' data-cloze='1'/></svg>"
            col.update_note(note1)

            # Check status after user drew mask on note 1
            status2 = get_occlusion_status(col)
            assert status2.total == 2
            assert status2.awaiting_masks == 1
            assert status2.ready_to_unflag == 1

            # 3. Dry run unflag
            dry_rep = unflag_completed_occlusions(col, dry_run=True)
            assert dry_rep.total_checked == 2
            assert dry_rep.awaiting_masks_count == 1
            assert dry_rep.unflagged_notes_count == 1
            assert dry_rep.unsuspended_cards_count == len(note1.cards())
            # Ensure dry run did not mutate
            reloaded_note1 = col.get_note(nids[0])
            assert OCCLUSION_TAG in reloaded_note1.tags
            assert all(c.queue == -1 for c in reloaded_note1.cards())

            # 4. Real execution of unflag_completed_occlusions
            real_rep = unflag_completed_occlusions(col, dry_run=False)
            assert real_rep.unflagged_notes_count == 1
            assert real_rep.unsuspended_cards_count == len(note1.cards())

            # Verify note 1 is untagged and unsuspended
            reloaded_note1 = col.get_note(nids[0])
            assert OCCLUSION_TAG not in reloaded_note1.tags
            assert all(c.queue == 0 for c in reloaded_note1.cards())

            # Verify note 2 remains awaiting masks and suspended
            reloaded_note2 = col.get_note(nids[1])
            assert OCCLUSION_TAG in reloaded_note2.tags
            assert all(c.queue == -1 for c in reloaded_note2.cards())

            # Check final status
            status3 = get_occlusion_status(col)
            assert status3.total == 1
            assert status3.awaiting_masks == 1
            assert status3.ready_to_unflag == 0
        finally:
            col.close()


def test_cloze_model_stability_and_diff_tolerance():
    """Verify that a Cloze card without markers retains Basic (Cloze) model,
    does not trigger a model-change diff, and preserves clozes added in Anki."""
    from src.worker import build_sync_plan, execute_sync_plan, get_occlusion_status, unflag_completed_occlusions
    from anki.collection import Collection

    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = Path(tmp_dir)
        vault_path = tmp_path / "vault"
        vault_path.mkdir()

        (vault_path / "m2a.toml").write_text(
            """vault = "."
deck = "General"
cloze_notes = true
suspend_new_clozes = true
""",
            encoding="utf-8",
        )
        note_file = vault_path / "test.md"
        note_file.write_text(
            """# Test
---
Cloze
das ist ein test fuer eine cloze
""",
            encoding="utf-8",
        )

        col = Collection(str(tmp_path / "col.anki2"))
        try:
            # 1. Initial sync creates the note as Cloze model with tag TODO_PROCESS_CLOZES
            plan1 = build_sync_plan(col, vault_path)
            assert plan1.total_creates == 1
            create_op = plan1.file_plans[0].creates[0]
            from markdown2anki.models import MODEL_NAME_CLOZE
            assert create_op.model_name == MODEL_NAME_CLOZE
            assert "TODO_PROCESS_CLOZES" in create_op.tags
            assert "Text" in create_op.fields
            assert "Front" not in create_op.fields

            report1 = execute_sync_plan(col, plan1)
            assert report1.created_count == 1
            assert report1.created_text_cloze_count == 1
            assert report1.suspended_count == 1

            # 2. Rescan without editing Anki -> should be completely unchanged (NO diff, NO model conversion!)
            plan2 = build_sync_plan(col, vault_path)
            assert plan2.total_creates == 0
            assert plan2.total_updates == 0
            assert plan2.file_plans[0].unchanged_count == 1

            # 3. Simulate user adding cloze in Anki
            nid = col.find_notes("tag:TODO_PROCESS_CLOZES")[0]
            note = col.get_note(nid)
            note["Text"] = "<p>das ist ein {{c1::test}} fuer eine cloze</p>"
            col.update_note(note)

            # 4. Rescan with cloze added in Anki -> cloze diff tolerance means NO false diff!
            plan3 = build_sync_plan(col, vault_path)
            assert plan3.total_updates == 0
            assert plan3.file_plans[0].unchanged_count == 1

            # 5. Unflag routine recognizes completed text cloze and unflags/unsuspends it
            status = get_occlusion_status(col)
            assert status.total == 1
            assert status.ready_to_unflag == 1

            unflag_rep = unflag_completed_occlusions(col, dry_run=False)
            assert unflag_rep.unflagged_notes_count == 1
            assert unflag_rep.unsuspended_cards_count == 1

            reloaded = col.get_note(nid)
            assert "TODO_PROCESS_CLOZES" not in reloaded.tags
            assert all(c.queue == 0 for c in reloaded.cards())
        finally:
            col.close()
