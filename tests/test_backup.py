from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock
import pytest

from src.backup import create_safety_backup
from src.contracts import FileSyncPlan, NoteCreateOp, SyncPlan
from src.profile_config import AddonProfileSettings, ProfileConfigManager


def test_sync_plan_has_changes(tmp_path: Path):
    empty_plan = SyncPlan(
        vault_path=tmp_path,
        default_deck="Default",
        file_plans=[],
    )
    assert empty_plan.has_changes is False

    file_p = tmp_path / "test.md"
    create_op = NoteCreateOp(
        card_id="c123",
        model_name="M2A Basic",
        target_deck_name="Default",
        fields={"Front": "Q", "Back": "A"},
        tags=["tag1"],
        source_file=file_p,
        source_line=10,
        media_refs=[],
    )
    fp = FileSyncPlan(file_path=file_p, mtime_ns=0, sha256_hex="abc", unchanged_count=0, creates=[create_op], updates=[])
    plan_with_creates = SyncPlan(
        vault_path=tmp_path,
        default_deck="Default",
        file_plans=[fp],
    )
    assert plan_with_creates.has_changes is True


def test_create_safety_backup_via_mw():
    mock_mw = MagicMock()
    mock_mw.create_backup_now = MagicMock()

    result = create_safety_backup(live_mw=mock_mw, reason="sync")
    assert result is True
    mock_mw.create_backup_now.assert_called_once()


def test_create_safety_backup_via_col_fallback():
    mock_mw = MagicMock(spec=["pm"])
    mock_mw.pm = MagicMock()
    mock_mw.pm.backupFolder.return_value = "/mock/backups"

    mock_col = MagicMock()
    mock_col.create_backup = MagicMock(return_value=True)

    result = create_safety_backup(live_mw=mock_mw, col=mock_col, reason="sync")
    assert result is True
    mock_col.create_backup.assert_called_once_with(
        backup_folder="/mock/backups",
        force=True,
        wait_for_completion=True,
    )


def test_create_safety_backup_handles_exception_gracefully():
    mock_mw = MagicMock(spec=["create_backup_now"])
    mock_mw.create_backup_now.side_effect = RuntimeError("Disk full")

    # When create_backup_now fails and no fallback col is available, should return False without crashing
    result = create_safety_backup(live_mw=mock_mw, reason="sync")
    assert result is False


def test_profile_config_backup_before_sync_persistence(tmp_path: Path):
    cfg_file = tmp_path / "config.json"
    manager = ProfileConfigManager(config_path=cfg_file)

    # Defaults to True
    settings = manager.load("TestProfile")
    assert settings.backup_before_sync is True

    # Save as False
    settings.backup_before_sync = False
    manager.save(settings)

    # Reload and verify
    reloaded = manager.load("TestProfile")
    assert reloaded.backup_before_sync is False
