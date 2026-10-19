from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

from aqt.qt import QApplication
from src.contracts import SyncPlan, SyncReport
from src.profile_config import AddonProfileSettings, ProfileConfigManager
from src.ui.status_bar import (
    COLOR_ERROR,
    COLOR_IDLE,
    COLOR_PENDING,
    COLOR_SUCCESS,
    COLOR_SYNCING,
    COLOR_UNCONFIGURED,
    VaultSyncStatusBarWidget,
    get_status_bar,
    init_status_bar,
    notify_scan_result,
    notify_sync_finished,
    notify_sync_started,
    update_status_bar_visibility,
)

# Ensure QApplication exists for GUI tests
app = QApplication.instance()
if app is None:
    app = QApplication([])


def test_profile_config_show_status_bar(tmp_path: Path):
    cfg_file = tmp_path / "config.json"
    manager = ProfileConfigManager(config_path=cfg_file)

    # Default load
    settings = manager.load("TestProfile")
    assert settings.show_status_bar is True

    # Save as False
    settings.show_status_bar = False
    manager.save(settings)

    loaded = manager.load("TestProfile")
    assert loaded.show_status_bar is False

    # Save as True
    loaded.show_status_bar = True
    manager.save(loaded)

    reloaded = manager.load("TestProfile")
    assert reloaded.show_status_bar is True


def test_status_bar_widget_states():
    widget = VaultSyncStatusBarWidget()

    # 1. Idle state (default)
    widget.set_idle()
    assert widget._state == "idle"
    assert widget._label.text() == ""  # Smart-compact: empty in idle
    assert widget._label.isHidden()
    assert COLOR_IDLE in widget._dot.styleSheet()
    assert "Up to date" in widget.toolTip()
    assert "No pending changes" in widget.toolTip()

    # 2. Unconfigured state
    widget.set_unconfigured()
    assert widget._state == "unconfigured"
    assert widget._label.text() == "No vault"
    assert not widget._label.isHidden()
    assert COLOR_UNCONFIGURED in widget._dot.styleSheet()
    assert "No vault configured" in widget.toolTip()

    # 3. Pending cards state
    widget.set_pending(creates=3, updates=2)
    assert widget._state == "pending"
    assert widget._label.text() == "5 pending"
    assert not widget._label.isHidden()
    assert COLOR_PENDING in widget._dot.styleSheet()
    assert "5 card(s) available to sync" in widget.toolTip()
    assert "3 new card(s)" in widget.toolTip()
    assert "2 modified note(s)" in widget.toolTip()

    # If pending is called with 0, it falls back to idle
    widget.set_pending(creates=0, updates=0)
    assert widget._state == "idle"
    assert widget._label.text() == ""

    # 4. Syncing state
    widget.set_syncing()
    assert widget._state == "syncing"
    assert widget._label.text() == "Syncing..."
    assert not widget._label.isHidden()
    assert COLOR_SYNCING in widget._dot.styleSheet()
    assert "Synchronization in progress" in widget.toolTip()

    # 5. Success state
    report = SyncReport(created_count=4, updated_count=1)
    widget.set_success(report)
    assert widget._state == "success"
    assert widget._label.text() == "5 synced"
    assert not widget._label.isHidden()
    assert COLOR_SUCCESS in widget._dot.styleSheet()
    assert "Sync completed successfully" in widget.toolTip()
    assert "4 added, 1 updated" in widget.toolTip()

    # Revert timeout transitions back to idle
    widget._on_revert_timeout()
    assert widget._state == "idle"
    assert widget._label.text() == ""

    # 6. Error state
    widget.set_error("Vault folder inaccessible")
    assert widget._state == "error"
    assert widget._label.text() == "Sync error"
    assert not widget._label.isHidden()
    assert COLOR_ERROR in widget._dot.styleSheet()
    assert "Vault folder inaccessible" in widget.toolTip()


def test_status_bar_no_emojis():
    """Verify that all strings, labels, and tooltips are strictly emoji-free."""
    widget = VaultSyncStatusBarWidget()

    # Check across all states
    widget.set_unconfigured()
    for s in (widget._label.text(), widget.toolTip()):
        assert not any(ord(c) > 0x2000 for c in s), f"Unexpected symbol/emoji in: {s}"

    widget.set_idle()
    for s in (widget._label.text(), widget.toolTip()):
        assert not any(ord(c) > 0x2000 for c in s), f"Unexpected symbol/emoji in: {s}"

    widget.set_pending(creates=2, updates=1)
    for s in (widget._label.text(), widget.toolTip()):
        assert not any(ord(c) > 0x2000 for c in s), f"Unexpected symbol/emoji in: {s}"

    widget.set_syncing()
    for s in (widget._label.text(), widget.toolTip()):
        assert not any(ord(c) > 0x2000 for c in s), f"Unexpected symbol/emoji in: {s}"

    widget.set_success(SyncReport(created_count=2, updated_count=0))
    for s in (widget._label.text(), widget.toolTip()):
        assert not any(ord(c) > 0x2000 for c in s), f"Unexpected symbol/emoji in: {s}"

    widget.set_error("Disk full")
    for s in (widget._label.text(), widget.toolTip()):
        assert not any(ord(c) > 0x2000 for c in s), f"Unexpected symbol/emoji in: {s}"


def test_status_bar_initialization_and_notifications():
    mock_status_bar = MagicMock()
    mock_mw = MagicMock()
    mock_mw.statusBar.return_value = mock_status_bar
    mock_mw.pm.name = "Default"

    # Init
    widget = init_status_bar(mock_mw)
    assert widget is not None
    mock_status_bar.addPermanentWidget.assert_called_with(widget)

    # Visibility toggle
    update_status_bar_visibility(False)
    assert not widget.isVisible()

    update_status_bar_visibility(True)
    assert widget.isVisible()

    # Global notifications
    notify_sync_started()
    assert widget._state == "syncing"

    rep = SyncReport(created_count=3, updated_count=0)
    notify_sync_finished(report=rep)
    assert widget._state == "success"
    assert widget._label.text() == "3 synced"

    notify_sync_finished(error="Test error")
    assert widget._state == "error"

    mock_plan = MagicMock()
    mock_plan.total_creates = 4
    mock_plan.total_updates = 2
    notify_scan_result(mock_plan)
    assert widget._state == "pending"
    assert widget._label.text() == "6 pending"
