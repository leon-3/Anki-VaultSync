from __future__ import annotations

from pathlib import Path
from src.contracts import (
    ExistingNoteRecord,
    FileSyncPlan,
    MediaItem,
    NoteCreateOp,
    NoteUpdateOp,
    OrphanNoteRecord,
    SyncPlan,
    SyncReport,
)


def test_sync_plan_properties():
    file_p = Path("/vault/notes.md")
    img_p = Path("/vault/img.png")

    create_op = NoteCreateOp(
        card_id="k3f9qz",
        model_name="M2A Basic",
        target_deck_name="TestDeck",
        fields={"Front": "Q", "Back": "A<!--m2a:k3f9qz-->"},
        tags=["Tag1"],
        source_file=file_p,
        source_line=10,
        media_refs=[MediaItem(img_p, "staged.png")],
    )

    update_op = NoteUpdateOp(
        note_id=12345,
        card_id="m9a2xp",
        model_name="M2A Basic",
        target_deck_name="NewDeck",
        old_deck_names=["OldDeck"],
        old_fields={"Front": "Old Q", "Back": "Old A"},
        new_fields={"Front": "New Q", "Back": "New A"},
        old_tags=["T1"],
        new_tags=["T2"],
        deck_changed=True,
        fields_changed=True,
        tags_changed=True,
        source_file=file_p,
        source_line=20,
        media_refs=[],
        approved=True,
    )

    file_plan = FileSyncPlan(
        file_path=file_p,
        mtime_ns=1000,
        sha256_hex="abcdef",
        creates=[create_op],
        updates=[update_op],
        unchanged_count=5,
    )

    orphan = OrphanNoteRecord(
        note_id=999,
        card_id="orphan1",
        model_name="M2A Basic",
        preview_question="Old question",
    )

    plan = SyncPlan(
        vault_path=Path("/vault"),
        default_deck="Default",
        file_plans=[file_plan],
        occlusions_to_stage=[Path("/vault/occ.png")],
        orphans=[orphan],
    )

    assert plan.total_files == 1
    assert plan.total_creates == 1
    assert plan.total_updates == 1
    assert plan.approved_updates == 1
    assert plan.total_unchanged == 5

    # Report counters
    report = SyncReport(created_count=1, updated_count=1)
    assert report.created_count == 1
    assert report.updated_count == 1


def test_custom_model_name_contract_supports_arbitrary_string():
    record = ExistingNoteRecord(
        note_id=999,
        card_id="cst999",
        model_id=123,
        model_name="Custom Medical Cloze Model",
        fields={"Text": "test"},
        tags=["med"],
        deck_ids={1},
    )
    assert record.model_name == "Custom Medical Cloze Model"
