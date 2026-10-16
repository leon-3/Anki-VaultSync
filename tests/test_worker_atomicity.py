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
