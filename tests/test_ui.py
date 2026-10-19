from __future__ import annotations

import hashlib
import tempfile
from pathlib import Path

from aqt.qt import QApplication
from src.contracts import FileSyncPlan, NoteUpdateOp, SyncPlan
from src.profile_config import AddonProfileSettings, ProfileConfigManager
from src.ui.config_window import ConfigWindow
from src.ui.main_window import MainWindow
from src.ui.diff_dialog import DiffDialog, _generate_diff_html
from src.ui.help_tooltips import TOOLTIPS

# Ensure a QApplication instance exists for GUI widget tests
app = QApplication.instance()
if app is None:
    app = QApplication([])


def test_help_tooltips_completeness():
    required_keys = [\
        "vault_path",\
        "occlusion_dir",\
        "default_deck",\
        "base_tag",\
        "pad_single_digit_tags",\
        "hash_media",\
        "cloze_notes",\
        "re_stage_occlusions",\
        "show_diff_before_update",\
        "sync_on_startup",\
        "auto_close_on_success",\
        "subdirectories",\
        "ignore",\
        "deck_routing",\
        "tag_aliases",\
        "decks",\
        "basic_model_id",\
        "cloze_model_id",\
    ]
    for key in required_keys:
        assert key in TOOLTIPS
        assert len(TOOLTIPS[key]) > 10


def test_diff_html_generation():
    op = NoteUpdateOp(
        note_id=1,
        card_id="k3f9qz",
        model_name="M2A Basic",
        target_deck_name="NewDeck",
        old_deck_names=["OldDeck"],
        old_fields={"Front": "What is backprop?", "Back": "Reverse autodiff."},
        new_fields={"Front": "What is backprop?", "Back": "Reverse automatic differentiation."},
        old_tags=["Draft"],
        new_tags=["Reviewed"],
        deck_changed=True,
        fields_changed=True,
        tags_changed=True,
        source_file=Path("/vault/dl.md"),
        source_line=42,
        media_refs=[],
    )
    diff_html = _generate_diff_html(op)
    assert "k3f9qz" in diff_html
    assert "NewDeck" in diff_html
    assert "OldDeck" in diff_html
    assert "+Reviewed" in diff_html
    assert "-Draft" in diff_html
    assert "automatic differentiation" in diff_html
    assert "<ins" in diff_html
    assert "<del" in diff_html


def test_diff_dialog_instantiation():
    op = NoteUpdateOp(
        note_id=1,
        card_id="k3f9qz",
        model_name="M2A Basic",
        target_deck_name="NewDeck",
        old_deck_names=["OldDeck"],
        old_fields={"Front": "Q", "Back": "A"},
        new_fields={"Front": "Q2", "Back": "A2"},
        old_tags=[],
        new_tags=[],
        deck_changed=False,
        fields_changed=True,
        tags_changed=False,
        source_file=Path("/vault/test.md"),
        source_line=1,
        media_refs=[],
    )
    fp = FileSyncPlan(
        file_path=Path("/vault/test.md"),
        mtime_ns=1,
        sha256_hex="123",
        creates=[],
        updates=[op],
        unchanged_count=0,
    )
    plan = SyncPlan(
        vault_path=Path("/vault"),
        default_deck="Default",
        file_plans=[fp],
    )

    dlg = DiffDialog(plan)
    assert dlg.list_widget.count() == 1
    assert "k3f9qz" in dlg.list_widget.item(0).text()

    # Toggle selection
    dlg.select_all_cb.setChecked(False)
    assert not op.approved
    dlg.select_all_cb.setChecked(True)
    assert op.approved
    dlg.close()


def test_config_window_instantiation(tmp_path: Path):
    cfg_file = tmp_path / "config.json"
    manager = ProfileConfigManager(config_path=cfg_file)

    dlg = ConfigWindow(manager, "TestProfile")
    assert dlg.tabs.count() == 6
    assert dlg.tabs.tabText(0) == "General && Sync"
    assert dlg.tabs.tabText(1) == "Deck Rules"
    assert dlg.tabs.tabText(2) == "Tag Rules"
    assert dlg.tabs.tabText(3) == "Card Types && Media"
    assert dlg.tabs.tabText(4) == "Deleted Cards"
    assert dlg.tabs.tabText(5) == "Shortcuts && Logs"
    assert dlg.vault_edit is not None
    assert dlg.deck_combo is not None
    assert dlg.basic_model_combo is not None
    assert dlg.cloze_model_combo is not None
    assert dlg.routing_table is not None
    assert dlg.alias_table is not None
    assert dlg.pad_single_digits_cb is not None
    assert dlg.orphans_status_label is not None
    assert dlg.view_orphans_btn is not None
    assert dlg.suspend_orphans_btn is not None
    assert dlg.delete_orphans_btn is not None
    assert dlg.missing_table is not None
    assert dlg.mark_deleted_btn is not None
    assert dlg.resync_missing_btn is not None
    assert dlg.tab_general.isAncestorOf(dlg.ignore_edit)
    assert dlg.routing_table.maximumHeight() == dlg.alias_table.maximumHeight()
    assert dlg.delete_orphans_btn.isEnabled() is False
    dlg.close()


def test_config_window_deck_routing_and_aliases_roundtrip(tmp_path: Path):
    vault_path = tmp_path / "vault"
    vault_path.mkdir()
    toml_path = vault_path / "m2a.toml"
    toml_path.write_text(
        'vault = "."\n[deck_routing]\n"TopicA" = "Domain::TopicA"\n[tag_aliases]\n"TopicA/SubtopicB" = "CustomTag"\n',
        encoding="utf-8",
    )

    cfg_file = tmp_path / "config.json"
    manager = ProfileConfigManager(config_path=cfg_file)
    manager.save(AddonProfileSettings(profile_name="Profile1", vault_path=vault_path))

    dlg = ConfigWindow(manager, "Profile1")
    assert dlg.routing_table.rowCount() == 1
    assert dlg.routing_table.item(0, 0).text() == "TopicA"
    assert dlg.routing_table.item(0, 1).text() == "Domain::TopicA"

    assert dlg.alias_table.rowCount() == 1
    assert dlg.alias_table.item(0, 0).text() == "TopicA/SubtopicB"
    assert dlg.alias_table.item(0, 1).text() == "CustomTag"
    assert dlg.pad_single_digits_cb.isChecked() is True

    # Add a routing rule
    dlg._add_routing_row()
    dlg.routing_table.item(1, 0).setText("TopicB")
    dlg.routing_table.item(1, 1).setText("Domain::TopicB")

    # Add an alias rule
    dlg._add_alias_row()
    dlg.alias_table.item(1, 0).setText("Notes")
    dlg.alias_table.item(1, 1).setText("")

    # Toggle digit padding
    dlg.pad_single_digits_cb.setChecked(False)

    dlg._on_save()
    dlg.close()

    saved_cfg = manager.load_vault(vault_path)
    assert saved_cfg.deck_routing == {"TopicA": "Domain::TopicA", "TopicB": "Domain::TopicB"}
    assert saved_cfg.tag_aliases == {"TopicA/SubtopicB": "CustomTag", "Notes": ""}
    assert saved_cfg.pad_single_digit_tags is False


def test_config_window_duplicate_path_validation(tmp_path: Path, monkeypatch):
    vault_path = tmp_path / "vault"
    vault_path.mkdir()
    (vault_path / "m2a.toml").write_text('vault = "."\n', encoding="utf-8")

    cfg_file = tmp_path / "config.json"
    manager = ProfileConfigManager(config_path=cfg_file)
    manager.save(AddonProfileSettings(profile_name="Profile1", vault_path=vault_path))

    warnings: list[str] = []
    from src.ui import config_window
    monkeypatch.setattr(config_window.QMessageBox, "warning", lambda parent, title, msg: warnings.append(title))

    dlg = ConfigWindow(manager, "Profile1")
    dlg._add_routing_row()
    dlg.routing_table.item(0, 0).setText("TopicA")
    dlg.routing_table.item(0, 1).setText("Deck1")

    dlg._add_routing_row()
    dlg.routing_table.item(1, 0).setText("TopicA/")  # Duplicate normalized key!
    dlg.routing_table.item(1, 1).setText("Deck2")

    # Save should detect duplicate, show warning, and reject without saving
    dlg._on_save()
    assert warnings == ["Duplicate Deck Rule"]

    # Check that m2a.toml was not updated with duplicate
    saved_cfg = manager.load_vault(vault_path)
    assert "TopicA" not in saved_cfg.deck_routing
    dlg.close()


def test_config_window_model_id_selection_roundtrip(tmp_path: Path):
    cfg_file = tmp_path / "config.json"
    manager = ProfileConfigManager(config_path=cfg_file)

    dlg = ConfigWindow(manager, "ProfileModelTest")
    dlg.basic_model_combo.addItem("My Basic", userData=1791787456189)
    dlg.basic_model_combo.setCurrentIndex(dlg.basic_model_combo.count() - 1)
    dlg.cloze_model_combo.addItem("My Cloze", userData=1791787737710)
    dlg.cloze_model_combo.setCurrentIndex(dlg.cloze_model_combo.count() - 1)
    dlg._on_save()
    dlg.close()

    loaded = manager.load("ProfileModelTest")
    assert loaded.basic_model_id == 1791787456189
    assert loaded.cloze_model_id == 1791787737710

    # Verify reload in ConfigWindow preserves selection
    dlg2 = ConfigWindow(manager, "ProfileModelTest")
    assert dlg2.basic_model_combo.currentData() == 1791787456189
    assert dlg2.cloze_model_combo.currentData() == 1791787737710
    dlg2.close()


def test_main_window_dashboard_layout(tmp_path: Path):
    cfg_file = tmp_path / "config.json"
    manager = ProfileConfigManager(config_path=cfg_file)

    win = MainWindow(manager, "TestDashboard")
    assert win.vault_label is not None
    assert "(Not Connected)" in win.vault_label.text() or "(Not Configured)" in win.vault_label.text()
    assert win.settings_btn.text() == "Settings"
    assert win.change_vault_btn.text() == "Settings"
    assert not hasattr(win, "orphans_btn")
    assert not hasattr(win, "auto_close_cb")
    assert not hasattr(win, "log_edit")
    assert win.status_label is not None
    assert win.status_dot is not None
    assert win.status_report_btn is not None
    assert win.new_cards_label is not None
    assert win.modified_cards_label is not None
    assert win.occlusions_label is not None
    assert win.orphans_label is not None
    assert win.title_status.text() == "Card Counts"
    assert win.title_actions.text() == "Actions"
    assert win.sync_btn.text() == "Sync"
    assert win.diff_btn.text() == "Preview Diff"
    assert win.rescan_btn.text() == "Rescan Vault"
    win.close()


def test_config_window_orphans_button_exists(tmp_path: Path):
    cfg_file = tmp_path / "config.json"
    manager = ProfileConfigManager(config_path=cfg_file)
    dlg = ConfigWindow(manager, "TestProfile")
    assert hasattr(dlg, "orphans_btn")
    assert dlg.orphans_btn.text() == "View in Anki Browser"
    dlg.close()


def test_config_window_logs_tab(tmp_path: Path):
    from src import logger
    log_file = tmp_path / "ui_test.log"
    logger.set_custom_log_path(log_file)
    try:
        logger.log_info("First test event")
        logger.log_warning("Second test warning")

        cfg_file = tmp_path / "config.json"
        manager = ProfileConfigManager(config_path=cfg_file)
        dlg = ConfigWindow(manager, "TestProfile", initial_tab=ConfigWindow.TAB_LOGS)
        assert dlg.tabs.currentIndex() == ConfigWindow.TAB_LOGS
        assert hasattr(dlg, "log_edit")
        text = dlg.log_edit.toPlainText()
        # Newest should be on top
        first_line = text.splitlines()[0]
        assert "Second test warning" in first_line
        dlg.close()

        # Test initial_tab by name
        dlg_named = ConfigWindow(manager, "TestProfile", initial_tab="Logs")
        assert dlg_named.tabs.currentIndex() == ConfigWindow.TAB_LOGS
        dlg_named.close()
    finally:
        logger.set_custom_log_path(None)


def test_sync_issues_dialog(tmp_path: Path):
    from src.contracts import SyncReport
    from src.ui.sync_report_dialog import SyncIssuesDialog

    report = SyncReport(
        created_count=2,
        updated_count=1,
        skipped_files=[(Path("/vault/bad.md"), "Syntax error on line 5")],
        warnings=["Missing media file image.png"],
    )

    dlg = SyncIssuesDialog(report)
    assert "bad.md" in dlg.issues_edit.toPlainText()
    assert "Syntax error on line 5" in dlg.issues_edit.toPlainText()
    assert "Missing media file image.png" in dlg.issues_edit.toPlainText()
    dlg.close()


def test_main_window_busy_state_disables_settings_and_actions(tmp_path: Path):
    cfg_file = tmp_path / "config.json"
    manager = ProfileConfigManager(config_path=cfg_file)
    win = MainWindow(manager, "TestBusy")

    # Initial state
    assert win.settings_btn.isEnabled() is True
    assert win.rescan_btn.isEnabled() is True

    # Set busy
    win._set_buttons_busy(True)
    assert win._is_busy is True
    assert win.settings_btn.isEnabled() is False
    assert win.rescan_btn.isEnabled() is False
    assert win.diff_btn.isEnabled() is False
    assert win.sync_btn.isEnabled() is False

    # Unset busy
    win._set_buttons_busy(False)
    assert win._is_busy is False
    assert win.settings_btn.isEnabled() is True
    assert win.rescan_btn.isEnabled() is True
    win.close()


def test_main_window_op_failure_resets_busy_and_reports_error(tmp_path: Path):
    cfg_file = tmp_path / "config.json"
    manager = ProfileConfigManager(config_path=cfg_file)
    win = MainWindow(manager, "TestFailure")

    win._set_buttons_busy(True)
    assert win._is_busy is True

    # Trigger failure callback
    err = RuntimeError("Simulated background database lock")
    win._on_op_failure(err)

    assert win._is_busy is False
    assert win.settings_btn.isEnabled() is True
    assert win.rescan_btn.isEnabled() is True
    assert "Operation failed: Simulated background database lock" in win.status_label.text()
    assert "#F44336" in win.status_dot.styleSheet()
    win.close()


def test_diff_dialog_is_file_externally_modified_fast_mtime(tmp_path: Path):
    file_path = tmp_path / "note.md"
    file_path.write_text("Hello World", encoding="utf-8")
    mtime_ns = file_path.stat().st_mtime_ns
    sha256_hex = hashlib.sha256(b"Hello World").hexdigest()

    op = NoteUpdateOp(
        note_id=1,
        card_id="test1",
        model_name="M2A Basic",
        target_deck_name="Default",
        old_deck_names=["Default"],
        old_fields={"Front": "Old", "Back": "Old"},
        new_fields={"Front": "New", "Back": "New"},
        old_tags=[],
        new_tags=[],
        deck_changed=False,
        fields_changed=True,
        tags_changed=False,
        source_file=file_path,
        source_line=1,
        media_refs=[],
    )
    fp = FileSyncPlan(
        file_path=file_path,
        mtime_ns=mtime_ns,
        sha256_hex=sha256_hex,
        creates=[],
        updates=[op],
        unchanged_count=0,
    )
    plan = SyncPlan(
        vault_path=tmp_path,
        default_deck="Default",
        file_plans=[fp],
    )

    dlg = DiffDialog(plan)
    assert file_path in dlg._file_plan_by_path
    # Unmodified: st_mtime_ns matches fp.mtime_ns -> False immediately
    assert dlg._is_file_externally_modified(op) is False

    # Simulate modification outside (with distinct mtime)
    file_path.write_text("Hello Modified World", encoding="utf-8")
    import os
    os.utime(file_path, ns=(mtime_ns + 100_000_000, mtime_ns + 100_000_000))
    assert dlg._is_file_externally_modified(op) is True
    dlg.close()


def test_config_window_surfaces_toml_parse_error(tmp_path: Path):
    cfg_file = tmp_path / "config.json"
    manager = ProfileConfigManager(config_path=cfg_file)

    vault_dir = tmp_path / "vault_broken"
    vault_dir.mkdir()
    (vault_dir / "m2a.toml").write_text("deck = [invalid toml syntax", encoding="utf-8")

    # Set profile to point to broken vault
    settings = manager.load("BrokenProfile")
    settings.vault_path = vault_dir
    manager.save(settings)

    dlg = ConfigWindow(manager, "BrokenProfile")
    assert hasattr(dlg, "error_banner")
    assert dlg.error_banner is not None
    assert "Warning: Failed to parse vault configuration" in dlg.error_banner.text()
    dlg.close()


def test_config_window_image_occlusion_and_suspension_settings_roundtrip(tmp_path: Path):
    vault_path = tmp_path / "vault"
    vault_path.mkdir()
    toml_path = vault_path / "m2a.toml"
    toml_path.write_text(
        'vault = "."\nimage_occlusion_mode = "native"\nsuspend_new_occlusions = true\nsuspend_new_clozes = false\nfreeze_image_occlusions = false\n',
        encoding="utf-8",
    )

    cfg_file = tmp_path / "config.json"
    manager = ProfileConfigManager(config_path=cfg_file)
    manager.save(AddonProfileSettings(profile_name="ProfileIO", vault_path=vault_path))

    dlg = ConfigWindow(manager, "ProfileIO")
    assert dlg.suspend_new_clozes_cb.isChecked() is False
    assert dlg.image_occlusion_mode_combo.currentData() == "native"
    assert dlg.suspend_new_occlusions_cb.isChecked() is True
    assert dlg.freeze_image_occlusions_cb.isChecked() is False
    assert dlg.staging_folder_row.isHidden() is True

    # Modify settings in UI
    dlg.suspend_new_clozes_cb.setChecked(True)
    dlg.image_occlusion_mode_combo.setCurrentIndex(1)  # "stage"
    assert dlg.staging_folder_row.isHidden() is False
    dlg.suspend_new_occlusions_cb.setChecked(False)
    dlg.freeze_image_occlusions_cb.setChecked(True)

    dlg._on_save()
    dlg.close()

    # Verify saved back to m2a.toml
    saved_cfg = manager.load_vault(vault_path)
    assert saved_cfg.suspend_new_clozes is True
    assert saved_cfg.image_occlusion_mode == "stage"
    assert saved_cfg.suspend_new_occlusions is False
    assert saved_cfg.freeze_image_occlusions is True


def test_main_window_occlusions_label_reflects_native_mode(tmp_path: Path):
    cfg_file = tmp_path / "config.json"
    manager = ProfileConfigManager(config_path=cfg_file)
    win = MainWindow(manager, "TestIO")
    assert win.occlusions_val_label is not None
    # Verify click handler does not crash when mw is headless / none
    win._view_unmasked_occlusions()
    win.close()


def test_main_window_card_counts_separation_and_unprocessed_occlusions(tmp_path: Path):
    from src.contracts import SyncPlan, FileSyncPlan, NoteCreateOp
    from markdown2anki.build import ImageOcclusion
    from markdown2anki.parser import Card

    cfg_file = tmp_path / "config.json"
    manager = ProfileConfigManager(config_path=cfg_file)
    win = MainWindow(manager, "TestCounts")

    dummy_card = Card(file=tmp_path / "note.md", line=1, question="Cloze", answer="foo", tag="")
    fp = FileSyncPlan(
        file_path=tmp_path / "note.md",
        mtime_ns=0,
        sha256_hex="abc",
        creates=[
            NoteCreateOp(
                card_id="cid1",
                model_name="M2A Basic",
                target_deck_name="Default",
                fields={"Front": "Q", "Back": "A"},
                tags=[],
                source_file=tmp_path / "note.md",
                source_line=1,
                media_refs=[],
            )
        ],
        updates=[],
        unchanged_count=0,
    )
    staged = ImageOcclusion(card=dummy_card, images=[tmp_path / "img.png"], tag="tag")
    plan = SyncPlan(
        vault_path=tmp_path,
        default_deck="Default",
        file_plans=[fp],
        occlusions_to_stage=[staged],
        orphans=[],
        diagnostics=[],
    )

    win._on_scan_success(plan)
    # Total new cards = 1 create + 1 staged occlusion = 2
    assert win.new_val_label.text() == "2"
    # Unprocessed occlusions label shows 0 since no notes in Anki collection
    assert win.occlusions_val_label.text() == "0"
    assert "No unprocessed image occlusions in Anki." in win.occlusions_val_label.toolTip()
    win.close()

def test_config_window_maintenance_tab_operations(tmp_path: Path):
    from src.contracts import MissingInAnkiRecord, OrphanNoteRecord
    from markdown2anki.parser import Card

    cfg_file = tmp_path / "config.json"
    manager = ProfileConfigManager(config_path=cfg_file)

    orphan = OrphanNoteRecord(
        note_id=101,
        card_id="orph101",
        model_name="M2A Basic",
        preview_question="Orphan Question",
    )
    fake_card = Card(file=tmp_path / "note.md", line=12, question="Missing Q", answer="Missing A", tag="T", added=True, card_id="miss202")
    missing = MissingInAnkiRecord(
        card_id="miss202",
        file_path=tmp_path / "note.md",
        line=12,
        preview_question="Missing Q",
        target_deck="Default",
        tags=["T"],
        card=fake_card,
    )
    plan = SyncPlan(
        vault_path=tmp_path,
        default_deck="Default",
        file_plans=[],
        orphans=[orphan],
        missing_in_anki=[missing],
    )

    dlg = ConfigWindow(manager, "TestProfile", sync_plan=plan, initial_tab=ConfigWindow.TAB_MAINTENANCE)
    assert dlg.tabs.currentIndex() == ConfigWindow.TAB_MAINTENANCE
    assert "1 orphaned note(s)" in dlg.orphans_status_label.text()
    assert dlg.view_orphans_btn.isEnabled() is True
    assert dlg.suspend_orphans_btn.isEnabled() is True
    assert dlg.delete_orphans_btn.isEnabled() is True

    # Missing table
    assert dlg.missing_table.rowCount() == 1
    assert dlg.missing_table.item(0, 1).text() == "note.md"
    assert dlg.missing_table.item(0, 2).text() == "12"
    assert dlg.missing_table.item(0, 3).text() == "Missing Q"
    assert dlg.mark_deleted_btn.isEnabled() is True
    assert dlg.resync_missing_btn.isEnabled() is True

    # Selection helpers
    selected = dlg._get_selected_missing_records()
    assert len(selected) == 1
    assert selected[0].card_id == "miss202"

    dlg._on_select_all_missing(False)
    assert len(dlg._get_selected_missing_records()) == 0

    dlg._on_select_all_missing(True)
    assert len(dlg._get_selected_missing_records()) == 1

    # Test sync lock protection for suspend and mark deleted
    from src.sync_lock import acquire_sync_lock, release_sync_lock
    acquire_sync_lock()
    try:
        dlg._on_suspend_orphans()
        dlg._on_mark_missing_deleted()
    finally:
        release_sync_lock()

    dlg.close()

def test_config_window_non_modal_and_view_orphans(tmp_path: Path, monkeypatch):
    from unittest.mock import MagicMock
    from aqt.qt import Qt
    from src.contracts import OrphanNoteRecord

    cfg_file = tmp_path / "config.json"
    manager = ProfileConfigManager(config_path=cfg_file)
    orphan = OrphanNoteRecord(
        note_id=42,
        card_id="cid42",
        model_name="M2A Basic",
        preview_question="What is Qt?",
    )
    plan = SyncPlan(
        vault_path=tmp_path,
        default_deck="Default",
        file_plans=[],
        orphans=[orphan],
    )

    dlg = ConfigWindow(manager, "TestNonModal", sync_plan=plan, initial_tab=ConfigWindow.TAB_MAINTENANCE)
    assert dlg.windowModality() == Qt.WindowModality.NonModal

    # Tab switching
    dlg.switch_to_tab("General")
    assert dlg.tabs.currentIndex() == ConfigWindow.TAB_GENERAL
    dlg.switch_to_tab(ConfigWindow.TAB_MAINTENANCE)
    assert dlg.tabs.currentIndex() == ConfigWindow.TAB_MAINTENANCE

    # Default deck fallback
    assert dlg._default_deck_name == "Default"

    # View orphans in browser triggers browser focus
    mock_browser = MagicMock()
    mock_dialogs = MagicMock()
    mock_dialogs.open.return_value = mock_browser
    import aqt
    monkeypatch.setattr(aqt, "dialogs", mock_dialogs)
    from src.ui import config_window
    monkeypatch.setattr(config_window, "dialogs", mock_dialogs)

    dlg._on_view_orphans()
    mock_dialogs.open.assert_called_once_with("Browser", aqt.mw)
    mock_browser.search_for.assert_called_once_with("nid:42")
    mock_browser.activateWindow.assert_called_once()
    mock_browser.raise_.assert_called_once()
    dlg.close()


def test_config_window_resync_missing_calls_sync_without_attribute_error(tmp_path: Path, monkeypatch):
    from unittest.mock import MagicMock
    from src.contracts import MissingInAnkiRecord, SyncReport
    from markdown2anki.parser import Card

    cfg_file = tmp_path / "config.json"
    manager = ProfileConfigManager(config_path=cfg_file)
    vault_dir = tmp_path / "vault"
    vault_dir.mkdir()
    settings = manager.load("TestResync")
    settings.vault_path = vault_dir
    manager.save(settings)

    fake_card = Card(file=vault_dir / "test.md", line=1, question="Q", answer="A", tag="", added=True, card_id="resync_id")
    missing = MissingInAnkiRecord(
        card_id="resync_id",
        file_path=vault_dir / "test.md",
        line=1,
        preview_question="Q",
        target_deck="Default",
        tags=[],
        card=fake_card,
    )
    plan = SyncPlan(
        vault_path=vault_dir,
        default_deck="Default",
        file_plans=[],
        missing_in_anki=[missing],
    )

    dlg = ConfigWindow(manager, "TestResync", sync_plan=plan, initial_tab=ConfigWindow.TAB_MAINTENANCE)

    mock_build_plan = MagicMock()
    mock_execute_plan = MagicMock(return_value=SyncReport(created_count=1, updated_count=0))
    from src.ui import config_window
    monkeypatch.setattr(config_window, "build_sync_plan", mock_build_plan)
    monkeypatch.setattr(config_window, "execute_sync_plan", mock_execute_plan)

    fake_mw = MagicMock()
    fake_mw.col = MagicMock()
    monkeypatch.setattr(config_window, "mw", fake_mw)

    # Select row and resync
    dlg._on_select_all_missing(True)
    dlg._on_resync_missing()

    assert mock_build_plan.call_count == 2
    # First call is resync with recreate_missing_ids
    first_call_kwargs = mock_build_plan.call_args_list[0].kwargs
    assert "default_deck" in first_call_kwargs
    assert first_call_kwargs["recreate_missing_ids"] == {"resync_id"}
    # Second call is refresh without recreate_missing_ids
    second_call_kwargs = mock_build_plan.call_args_list[1].kwargs
    assert "default_deck" in second_call_kwargs
    mock_execute_plan.assert_called_once()
    dlg.close()


def test_main_window_open_settings_modeless(tmp_path: Path):
    from aqt.qt import Qt

    cfg_file = tmp_path / "config.json"
    manager = ProfileConfigManager(config_path=cfg_file)
    win = MainWindow(manager, "TestModeless")

    win._open_settings(tab_index=ConfigWindow.TAB_MAINTENANCE)
    assert win._config_dlg is not None
    assert win._config_dlg.windowModality() == Qt.WindowModality.NonModal
    assert win._config_dlg.tabs.currentIndex() == ConfigWindow.TAB_MAINTENANCE

    # Calling again switches tab and raises existing instance
    first_dlg = win._config_dlg
    win._open_settings(tab_index=ConfigWindow.TAB_GENERAL)
    assert win._config_dlg is first_dlg
    assert win._config_dlg.tabs.currentIndex() == ConfigWindow.TAB_GENERAL

    win._config_dlg.close()
    win.close()

def test_find_shortcut_conflict_detection(monkeypatch):
    from aqt.qt import QAction, QMainWindow, QKeySequence
    from src.ui.shortcuts import find_shortcut_conflict
    import src.ui.shortcuts

    dummy_mw = QMainWindow()
    monkeypatch.setattr(src.ui.shortcuts, "mw", dummy_mw)

    # Empty or whitespace sequence
    assert find_shortcut_conflict("") is None
    assert find_shortcut_conflict("   ") is None

    # No conflict yet
    assert find_shortcut_conflict("Ctrl+Alt+V") is None

    # Register an action on mw
    existing_action = QAction("&Existing Command", dummy_mw)
    existing_action.setShortcut(QKeySequence("Ctrl+Alt+V"))
    dummy_mw.addAction(existing_action)

    # Detect conflict
    assert find_shortcut_conflict("Ctrl+Alt+V") == "Existing Command"

    # With allowed_actions containing the existing action -> no conflict
    assert find_shortcut_conflict("Ctrl+Alt+V", allowed_actions={existing_action}) is None

    # Non-conflicting shortcut
    assert find_shortcut_conflict("Ctrl+Alt+Z") is None


def test_apply_menu_shortcuts_auto_disables_conflicts(monkeypatch, tmp_path: Path):
    from aqt.qt import QAction, QMainWindow, QMenuBar, QKeySequence
    from src.profile_config import AddonProfileSettings, ProfileConfigManager
    import src.ui.menu as menu_mod
    import src.ui.shortcuts as shortcuts_mod

    dummy_mw = QMainWindow()
    class Form:
        pass
    dummy_mw.form = Form()
    dummy_mw.form.menubar = QMenuBar(dummy_mw)

    monkeypatch.setattr(menu_mod, "mw", dummy_mw)
    monkeypatch.setattr(shortcuts_mod, "mw", dummy_mw)

    # Simulate existing action on mw occupying Ctrl+Alt+S
    clash_action = QAction("Anki Quick Sync", dummy_mw)
    clash_action.setShortcut(QKeySequence("Ctrl+Alt+S"))
    dummy_mw.addAction(clash_action)

    menu_mod.on_main_window_did_init()

    actions = menu_mod.get_menu_actions()
    dash_act = actions["open_dashboard"]
    quick_act = actions["quick_sync"]
    full_act = actions["full_sync"]

    # Open dashboard and full sync should succeed
    assert dash_act.shortcut().toString() == "Ctrl+Alt+V"
    assert full_act.shortcut().toString() == "Ctrl+Alt+U"

    # Quick sync conflicted with clash_action -> automatically disabled (empty shortcut)
    assert quick_act.shortcut().isEmpty()

    # Test internal duplicates: both configured with Ctrl+Alt+V
    settings = AddonProfileSettings(
        profile_name="Test",
        shortcuts={
            "open_dashboard": "Ctrl+Alt+V",
            "quick_sync": "Ctrl+Alt+V",
            "full_sync": "Ctrl+Alt+U",
        },
    )
    # Remove clash_action for this check
    clash_action.setShortcut(QKeySequence())
    menu_mod.apply_menu_shortcuts(settings)

    assert dash_act.shortcut().toString() == "Ctrl+Alt+V"
    # quick_sync duplicates open_dashboard -> disabled
    assert quick_act.shortcut().isEmpty()
    assert full_act.shortcut().toString() == "Ctrl+Alt+U"


def test_config_window_shortcuts_configuration(monkeypatch, tmp_path: Path):
    from aqt.qt import QAction, QMainWindow, QKeySequence
    import src.ui.shortcuts as shortcuts_mod

    dummy_mw = QMainWindow()
    monkeypatch.setattr(shortcuts_mod, "mw", dummy_mw)

    cfg_file = tmp_path / "config.json"
    manager = ProfileConfigManager(config_path=cfg_file)

    dlg = ConfigWindow(manager, "TestShortcutProfile")

    # Verify default shortcuts loaded into GUI
    assert dlg.sc_dash_edit.maximumWidth() == 170
    assert dlg.sc_dash_edit.keySequence().toString() == "Ctrl+Alt+V"
    assert dlg.sc_quick_edit.keySequence().toString() == "Ctrl+Alt+S"
    assert dlg.sc_full_edit.keySequence().toString() == "Ctrl+Alt+U"
    assert dlg.sc_dash_warn.isHidden()
    assert dlg.sc_quick_warn.isHidden()
    assert dlg.sc_full_warn.isHidden()

    # 1. Test intra-dialog collision warning
    dlg.sc_quick_edit.setKeySequence(QKeySequence("Ctrl+Alt+V"))
    assert not dlg.sc_quick_warn.isHidden()
    assert "Conflicts with 'Open Dashboard'" in dlg.sc_quick_warn.text()

    # 2. Test Reset to Defaults button
    dlg._reset_shortcuts_to_defaults()
    assert dlg.sc_quick_edit.keySequence().toString() == "Ctrl+Alt+S"
    assert dlg.sc_quick_warn.isHidden()

    # 3. Test external collision warning
    ext_action = QAction("&Browse Cards", dummy_mw)
    ext_action.setShortcut(QKeySequence("Ctrl+Alt+U"))
    dummy_mw.addAction(ext_action)

    dlg._validate_shortcuts()
    assert not dlg.sc_full_warn.isHidden()
    assert "Already taken by 'Browse Cards'" in dlg.sc_full_warn.text()

    # 4. Save custom shortcut and verify persistence
    dlg.sc_dash_edit.setKeySequence(QKeySequence("Ctrl+Shift+D"))
    dlg._on_save()

    reloaded = manager.load("TestShortcutProfile")
    assert reloaded.shortcuts["open_dashboard"] == "Ctrl+Shift+D"

    dlg.close()

def test_sync_notification_popup(monkeypatch):
    from aqt.qt import QLabel, QMainWindow
    from src.contracts import SyncReport
    from src.ui.notification import SyncNotification, show_sync_notification
    import src.ui.notification as notif_mod

    dummy_mw = QMainWindow()
    dummy_mw.resize(1000, 700)
    monkeypatch.setattr(notif_mod, "mw", dummy_mw)

    report = SyncReport(
        created_count=5,
        created_image_cloze_count=2,
        created_text_cloze_count=3,
        created_basic_count=0,
        updated_count=2,
        media_added_count=4,
        warnings=["Test warning"],
    )

    notif = SyncNotification(report, parent=dummy_mw, include_updates=True)
    # Check 10-second timer
    assert notif._timer.interval() == 10000
    assert notif._timer.isSingleShot()

    # Verify positioning at bottom-left
    assert notif.x() == 24

    # Verify clean text content in labels without emojis
    all_texts = " ".join(lbl.text() for lbl in notif.findChildren(QLabel))
    assert "Added:</b> 5" in all_texts
    assert "Image Clozes: <b>2" in all_texts
    assert "Text Clozes: <b>3" in all_texts
    assert "Updated:</b> 2" in all_texts
    assert "Media:</b> 4" in all_texts
    assert "Warnings:</b> 1" in all_texts

    # Zero emojis
    for emoji in ["⚡", "➕", "🔄", "📁", "📦", "⚠️", "✔"]:
        assert emoji not in all_texts

    # Test dismissal
    notif.close()

    # Startup sync with no changes should be quiet (returns None)
    empty_report = SyncReport()
    res_startup = show_sync_notification(empty_report, parent=dummy_mw, is_startup=True)
    assert res_startup is None

    # Manual sync with no changes should display clean up to date message
    res_manual = show_sync_notification(empty_report, parent=dummy_mw, is_startup=False)
    assert res_manual is not None
    manual_texts = " ".join(lbl.text() for lbl in res_manual.findChildren(QLabel))
    assert "Up to date" in manual_texts
    assert "✔" not in manual_texts
    res_manual.close()

    # Test only categories > 0 are displayed (clean notification)
    basic_only_report = SyncReport(
        created_count=3,
        created_basic_count=3,
        created_image_cloze_count=0,
        created_text_cloze_count=0,
    )
    notif_basic = SyncNotification(basic_only_report, parent=dummy_mw, include_updates=False)
    basic_texts = " ".join(lbl.text() for lbl in notif_basic.findChildren(QLabel))
    assert "Basic: <b>3" in basic_texts
    assert "Image Clozes" not in basic_texts
    assert "Text Clozes" not in basic_texts

    # Test parent event filter closes notification on parent hide
    from aqt.qt import QEvent
    dummy_mw.hide()
    assert not notif_basic.isVisible()
    notif_basic.close()



def test_diff_dialog_transactional_rollback_on_cancel():
    from src.contracts import FileSyncPlan, NoteUpdateOp, SyncPlan
    from src.ui.diff_dialog import DiffDialog

    op = NoteUpdateOp(
        note_id=1,
        card_id="tx_rollback",
        model_name="M2A Basic",
        target_deck_name="Default",
        old_deck_names=["Default"],
        old_fields={"Front": "Q1", "Back": "A1"},
        new_fields={"Front": "Q2", "Back": "A2"},
        old_tags=[],
        new_tags=[],
        deck_changed=False,
        fields_changed=True,
        tags_changed=False,
        source_file=Path("/vault/tx.md"),
        source_line=1,
        media_refs=[],
    )
    assert op.approved is True
    fp = FileSyncPlan(file_path=Path("/vault/tx.md"), mtime_ns=1, sha256_hex="abc", creates=[], updates=[op], unchanged_count=0)
    plan = SyncPlan(vault_path=Path("/vault"), default_deck="Default", file_plans=[fp])

    dlg = DiffDialog(plan)
    # User deselects all
    dlg.select_all_cb.setChecked(False)
    assert op.approved is False

    # 1. User cancels (reject) -> rollback to original state
    dlg.reject()
    assert op.approved is True

    # 2. User deselects and accepts -> modification is applied
    dlg2 = DiffDialog(plan)
    dlg2.select_all_cb.setChecked(False)
    assert op.approved is False
    dlg2.accept()
    assert op.approved is False


def test_main_window_action_button_counts_sync_and_diff(tmp_path: Path):
    from src.contracts import FileSyncPlan, NoteCreateOp, NoteUpdateOp, SyncPlan
    from src.ui.main_window import MainWindow

    cfg_file = tmp_path / "config.json"
    manager = ProfileConfigManager(config_path=cfg_file)
    win = MainWindow(manager, "TestCounts")

    u1 = NoteUpdateOp(
        note_id=1, card_id="u1", model_name="M2A Basic", target_deck_name="D", old_deck_names=["D"],
        old_fields={"Front": "A"}, new_fields={"Front": "B"}, old_tags=[], new_tags=[],
        deck_changed=False, fields_changed=True, tags_changed=False, source_file=Path("a.md"), source_line=1, media_refs=[]
    )
    u2 = NoteUpdateOp(
        note_id=2, card_id="u2", model_name="M2A Basic", target_deck_name="D", old_deck_names=["D"],
        old_fields={"Front": "C"}, new_fields={"Front": "D"}, old_tags=[], new_tags=[],
        deck_changed=False, fields_changed=True, tags_changed=False, source_file=Path("a.md"), source_line=2, media_refs=[]
    )
    c1 = NoteCreateOp(
        card_id="c1", model_name="M2A Basic", target_deck_name="D", fields={"Front": "N"}, tags=[],
        media_refs=[], source_file=Path("a.md"), source_line=3
    )

    fp = FileSyncPlan(file_path=Path("a.md"), mtime_ns=1, sha256_hex="x", creates=[c1], updates=[u1, u2], unchanged_count=0)
    plan = SyncPlan(vault_path=Path("/v"), default_deck="D", file_plans=[fp])

    win._on_scan_success(plan, unmasked_in_anki=0)
    # 1 create + 2 approved updates = 3 total sync count
    assert win.sync_btn.text() == "Sync (3)"
    assert win.diff_btn.text() == "Preview Diff (2)"

    # Disapprove u1
    u1.approved = False
    win._update_action_button_counts()
    # 1 create + 1 approved update = 2 total sync count
    assert win.sync_btn.text() == "Sync (2)"
    assert win.diff_btn.text() == "Preview Diff (1)"
    win.close()


def test_profile_will_close_cleans_up_windows_and_lock():
    from src.sync_lock import acquire_sync_lock, is_sync_running, release_sync_lock
    import src.ui.menu as menu_mod

    acquire_sync_lock()
    assert is_sync_running() is True

    menu_mod.on_profile_will_close()
    assert is_sync_running() is False


def test_shortcut_config_widget_roundtrip_and_conflicts():
    from src.ui.shortcuts import ShortcutConfigWidget
    widget = ShortcutConfigWidget()

    # Initial state should have defaults
    scs = widget.get_shortcuts()
    assert scs["open_dashboard"] == "Ctrl+Alt+V"
    assert scs["quick_sync"] == "Ctrl+Alt+S"
    assert scs["full_sync"] == "Ctrl+Alt+U"
    assert widget.validate() is True

    # Set custom shortcuts
    widget.set_shortcuts({
        "open_dashboard": "Ctrl+Shift+1",
        "quick_sync": "Ctrl+Shift+2",
        "full_sync": "Ctrl+Shift+3",
    })
    scs2 = widget.get_shortcuts()
    assert scs2["open_dashboard"] == "Ctrl+Shift+1"
    assert scs2["quick_sync"] == "Ctrl+Shift+2"
    assert scs2["full_sync"] == "Ctrl+Shift+3"

    # Introduce a mutual conflict
    widget.set_shortcuts({
        "open_dashboard": "Ctrl+Shift+1",
        "quick_sync": "Ctrl+Shift+1",
        "full_sync": "Ctrl+Shift+3",
    })
    assert widget.validate() is False

    # Reset to defaults
    widget.reset_to_defaults()
    assert widget.validate() is True
    assert widget.get_shortcuts()["open_dashboard"] == "Ctrl+Alt+V"


def test_delete_orphans_warning_suspended_and_active(tmp_path: Path, monkeypatch):
    from unittest.mock import MagicMock
    from aqt.qt import QMessageBox
    from src.contracts import OrphanNoteRecord
    from src.ui import config_window

    cfg_file = tmp_path / "config.json"
    manager = ProfileConfigManager(config_path=cfg_file)
    orphan1 = OrphanNoteRecord(note_id=42, card_id="cid42", model_name="M2A Basic", preview_question="Q1")
    orphan2 = OrphanNoteRecord(note_id=43, card_id="cid43", model_name="M2A Basic", preview_question="Q2")
    plan = SyncPlan(
        vault_path=tmp_path,
        default_deck="Default",
        file_plans=[],
        orphans=[orphan1, orphan2],
    )

    mock_mw = MagicMock()
    mock_mw.col.db.all.return_value = [(42,)]  # Note 42 has a suspended card
    monkeypatch.setattr(config_window, "mw", mock_mw)

    dlg = ConfigWindow(manager, "TestProfile", sync_plan=plan, initial_tab=ConfigWindow.TAB_MAINTENANCE)
    assert "2 orphaned note(s) detected in Anki (1 suspended)" in dlg.orphans_status_label.text()

    # Test warning dialog when suspended cards exist
    recorded_warnings = []
    def fake_warning(parent, title, text, buttons, default_btn):
        recorded_warnings.append((title, text))
        return QMessageBox.StandardButton.Cancel

    monkeypatch.setattr(QMessageBox, "warning", fake_warning)
    dlg._on_delete_orphans()

    assert len(recorded_warnings) == 1
    assert "WARNING: 1 of 2 orphaned note(s) contain suspended cards in Anki." in recorded_warnings[0][1]
    assert "These cards may have been suspended to preserve review history" in recorded_warnings[0][1]
    assert "Are you sure you want to permanently delete all 2 orphaned note(s) (including 1 suspended) from Anki?" in recorded_warnings[0][1]

    # Now test when no suspended cards exist
    mock_mw.col.db.all.return_value = []
    dlg._on_delete_orphans()
    assert len(recorded_warnings) == 2
    assert "WARNING:" not in recorded_warnings[1][1]
    assert "Are you sure you want to permanently delete 2 orphaned note(s) from Anki?" in recorded_warnings[1][1]

    dlg.close()


def test_main_window_setup_view_quick_connect_and_manual(tmp_path: Path, monkeypatch):
    from src.vault_detector import DetectedVault
    import src.ui.main_window as mw_mod

    v1 = tmp_path / "DetectedNotes"
    v1.mkdir()
    cfg_file = tmp_path / "config.json"
    manager = ProfileConfigManager(config_path=cfg_file)

    detected = [DetectedVault(name="DetectedNotes", path=v1, is_open=True, ts=500)]
    monkeypatch.setattr(mw_mod, "detect_obsidian_vaults", lambda: detected)

    win = MainWindow(manager, "TestSetup")
    # Must start in setup view (index 0)
    assert win.main_stack.currentIndex() == 0
    assert not win.quick_connect_box.isHidden()
    assert "DetectedNotes" in win.lbl_qc_info.text()
    assert win.setup_vault_edit.text() == str(v1)

    # Click quick connect & scan
    win._on_quick_connect_clicked()

    # Settings must now have vault_path and onboarding_completed = True
    saved = manager.load("TestSetup")
    assert saved.vault_path == v1
    assert saved.onboarding_completed is True
    # Stack must switch to dashboard view (index 1)
    assert win.main_stack.currentIndex() == 1
    assert str(v1) in win.vault_label.text()
    win.close()


def test_format_tooltip_box_and_assets():
    from src.ui.help_tooltips import format_tooltip_box, TOOLTIPS
    from src.ui.config_window import _CHECKMARK_SVG_PATH

    html = format_tooltip_box("backup_before_sync")
    assert "Automatic Backup" in html
    assert TOOLTIPS["backup_before_sync"] in html
    assert "min-width: 220px" in html

    # Unknown key returns empty string
    assert format_tooltip_box("nonexistent_key_xyz") == ""

    # Checkmark asset exists and is valid SVG
    assert _CHECKMARK_SVG_PATH.exists()
    svg_content = _CHECKMARK_SVG_PATH.read_text(encoding="utf-8")
    assert "<svg" in svg_content
    assert "</svg>" in svg_content
    assert "polyline" in svg_content


def test_tab2_table_button_spacing(tmp_path: Path):
    from src.ui.config_window import ConfigWindow
    from src.profile_config import ProfileConfigManager
    import aqt.qt as qt
    cfg_file = tmp_path / "config.json"
    manager = ProfileConfigManager(config_path=cfg_file)
    dlg = ConfigWindow(manager, "ProfileSpacingTest")
    dlg.switch_to_tab(ConfigWindow.TAB_DECK_ROUTING)
    dlg.show()

    card_rules = dlg.routing_table.parent()
    btn_rule = [w for w in card_rules.findChildren(qt.QPushButton) if w.text() == "Add Rule"][0]
    gap_card1 = btn_rule.pos().y() - (dlg.routing_table.pos().y() + dlg.routing_table.size().height())
    assert gap_card1 >= 10, f"Gap in deck routing is too small: {gap_card1}"

    dlg.switch_to_tab(ConfigWindow.TAB_TAG_ROUTING)
    card_tag = dlg.alias_table.parent()
    btn_alias = [w for w in card_tag.findChildren(qt.QPushButton) if w.text() == "Add Tag Alias"][0]
    gap_card2 = btn_alias.pos().y() - (dlg.alias_table.pos().y() + dlg.alias_table.size().height())
    assert gap_card2 >= 10, f"Gap in tag routing is too small: {gap_card2}"

    dlg.close()


def test_load_vault_config_without_explicit_vault_key(tmp_path: Path):
    from markdown2anki.config import load_vault_config
    vault = tmp_path / "vault_no_key"
    vault.mkdir()
    (vault / "m2a.toml").write_text('deck = "CustomDeck"\n[deck_routing]\n"Sub" = "CustomDeck::Sub"\n', encoding="utf-8")
    cfg = load_vault_config(vault)
    assert cfg.deck == "CustomDeck"
    assert cfg.vault.resolve() == vault.resolve()
    assert cfg.deck_routing == {"Sub": "CustomDeck::Sub"}


def test_config_window_reactive_vault_edit_preserves_existing_config(tmp_path: Path):
    from src.ui.config_window import ConfigWindow
    from src.profile_config import ProfileConfigManager, AddonProfileSettings

    vault = tmp_path / "my_existing_vault"
    vault.mkdir()
    (vault / "m2a.toml").write_text(
        'deck = "Biology"\nbase_tag = "#Bio"\nignore = ["Drafts"]\n\n[deck_routing]\n"Anatomy" = "Bio::Anatomy"\n\n[tag_aliases]\n"Anatomy" = "HumanBio"\n',
        encoding="utf-8"
    )

    cfg_file = tmp_path / "config.json"
    manager = ProfileConfigManager(config_path=cfg_file)
    manager.save(AddonProfileSettings(profile_name="TestProfile"))

    dlg = ConfigWindow(manager, "TestProfile")
    # Initially empty/defaults
    assert dlg.routing_table.rowCount() == 0

    # Simulate user typing or pasting the vault path
    dlg.vault_edit.setText(str(vault))
    # textChanged should have populated the UI
    assert dlg.deck_combo.currentText() == "Biology"
    assert dlg.tag_edit.text() == "#Bio"
    assert dlg.routing_table.rowCount() == 1
    assert dlg.routing_table.item(0, 0).text() == "Anatomy"
    assert dlg.routing_table.item(0, 1).text() == "Bio::Anatomy"
    assert dlg.alias_table.rowCount() == 1
    assert dlg.alias_table.item(0, 0).text() == "Anatomy"
    assert dlg.alias_table.item(0, 1).text() == "HumanBio"
    assert "Drafts" in dlg.ignore_edit.toPlainText()

    # Save dialog
    dlg._on_save()
    dlg.close()

    # Verify m2a.toml still has all settings intact
    saved_cfg = manager.load_vault(vault)
    assert saved_cfg.deck == "Biology"
    assert saved_cfg.base_tag == "#Bio"
    assert saved_cfg.deck_routing == {"Anatomy": "Bio::Anatomy"}
    assert saved_cfg.tag_aliases == {"Anatomy": "HumanBio"}
    assert "Drafts" in saved_cfg.ignore


def test_config_window_prevents_saving_invalid_m2a_toml(tmp_path: Path, monkeypatch):
    from src.ui.config_window import ConfigWindow
    from src.profile_config import ProfileConfigManager, AddonProfileSettings

    vault = tmp_path / "broken_vault"
    vault.mkdir()
    broken_content = "deck = [invalid syntax"
    (vault / "m2a.toml").write_text(broken_content, encoding="utf-8")

    cfg_file = tmp_path / "config.json"
    manager = ProfileConfigManager(config_path=cfg_file)
    manager.save(AddonProfileSettings(profile_name="TestProfile"))

    dlg = ConfigWindow(manager, "TestProfile")
    dlg.vault_edit.setText(str(vault))
    dlg._load_vault_from_path(vault)

    assert dlg._vault_cfg_error is not None
    assert not dlg.error_banner.isHidden()

    warnings: list[str] = []
    from src.ui import config_window
    monkeypatch.setattr(config_window.QMessageBox, "warning", lambda parent, title, msg: warnings.append(title))

    # Trying to save should block saving and warn user
    dlg._on_save()
    assert "Invalid Vault Configuration" in warnings
    # Verify the broken file was not overwritten with defaults
    assert (vault / "m2a.toml").read_text(encoding="utf-8") == broken_content
    dlg.close()


def test_main_window_setup_vault_changed_preserves_existing_deck(tmp_path: Path):
    from src.ui.main_window import MainWindow
    from src.profile_config import ProfileConfigManager

    vault = tmp_path / "setup_vault"
    vault.mkdir()
    (vault / "m2a.toml").write_text('deck = "Chemistry"\n', encoding="utf-8")

    cfg_file = tmp_path / "config.json"
    manager = ProfileConfigManager(config_path=cfg_file)

    win = MainWindow(manager, "SetupProfile")
    # Simulate user selecting the vault folder
    win._on_setup_vault_changed(str(vault))
    assert win.setup_deck_combo.currentText() == "Chemistry"

    # User clicks Save and Scan Vault
    win.setup_vault_edit.setText(str(vault))
    win._on_save_and_scan_clicked()

    saved_cfg = manager.load_vault(vault)
    assert saved_cfg.deck == "Chemistry"
    win.close()


def test_deck_rules_and_tag_rules_customization(tmp_path: Path):
    from src.ui.config_window import ConfigWindow
    from src.profile_config import ProfileConfigManager, AddonProfileSettings
    import aqt.qt as qt

    vault = tmp_path / "custom_vault"
    vault.mkdir()
    (vault / "m2a.toml").write_text('vault = "."\ndeck = "DefaultDeck"\n', encoding="utf-8")

    cfg_file = tmp_path / "config.json"
    manager = ProfileConfigManager(config_path=cfg_file)
    manager.save(AddonProfileSettings(profile_name="RuleTest", vault_path=vault))

    dlg = ConfigWindow(manager, "RuleTest")

    # 1. Default deck combo lives in Deck Rules tab, not General tab
    assert dlg.tab_deck_rules.isAncestorOf(dlg.deck_combo)
    assert not dlg.tab_general.isAncestorOf(dlg.deck_combo)

    # 2. Browse Folder buttons have been removed
    deck_buttons = [b.text() for b in dlg.tab_deck_rules.findChildren(qt.QPushButton)]
    assert "Browse Folder..." not in deck_buttons
    assert "Add Rule" in deck_buttons
    assert "Remove Selected" in deck_buttons

    tag_buttons = [b.text() for b in dlg.tab_tag_rules.findChildren(qt.QPushButton)]
    assert "Browse Folder..." not in tag_buttons
    assert "Add Tag Alias" in tag_buttons
    assert "Remove Selected" in tag_buttons

    # 3. Adding a routing row provides a QComboBox in cell (row, 1)
    dlg._add_routing_row()
    assert dlg.routing_table.rowCount() == 1
    combo_widget = dlg.routing_table.cellWidget(0, 1)
    assert isinstance(combo_widget, qt.QComboBox)
    assert combo_widget.isEditable()

    # Change via combo box
    dlg.routing_table.item(0, 0).setText("Biology/Notes")
    combo_widget.setCurrentText("BioDeck::Notes")

    # Data extraction reflects combo box
    table_data = dlg._get_routing_table_data()
    assert table_data == [("Biology/Notes", "BioDeck::Notes")]

    # 4. Adding a tag alias row provides an editable QComboBox in cell (row, 1)
    dlg._add_alias_row()
    assert dlg.alias_table.rowCount() == 1
    tag_combo_widget = dlg.alias_table.cellWidget(0, 1)
    assert isinstance(tag_combo_widget, qt.QComboBox)
    assert tag_combo_widget.isEditable()

    # Change via tag combo box
    dlg.alias_table.item(0, 0).setText("Biology/Notes")
    tag_combo_widget.setCurrentText("custom_tag::notes")

    # Data extraction reflects combo box
    alias_table_data = dlg._get_alias_table_data()
    assert alias_table_data == [("Biology/Notes", "custom_tag::notes")]

    # Save to vault
    dlg._on_save()
    saved_cfg = manager.load_vault(vault)
    assert saved_cfg.deck_routing == {"Biology/Notes": "BioDeck::Notes"}
    assert saved_cfg.tag_aliases == {"Biology/Notes": "custom_tag::notes"}
    dlg.close()


def test_folder_dropdown_level_sorted_and_custom_entry(tmp_path: Path):
    from src.ui.config_window import ConfigWindow
    from src.profile_config import ProfileConfigManager, AddonProfileSettings
    import aqt.qt as qt

    vault = tmp_path / "sorted_vault"
    vault.mkdir()
    (vault / "Zebra").mkdir()
    (vault / "Alpha").mkdir()
    (vault / "Alpha" / "Beta").mkdir(parents=True)
    (vault / "Alpha" / "Beta" / "Gamma").mkdir(parents=True)
    (vault / "Alpha" / "Zeta").mkdir(parents=True)
    (vault / ".obsidian").mkdir()
    (vault / ".git").mkdir()
    (vault / "IgnoredDir").mkdir()

    (vault / "m2a.toml").write_text(
        'vault = "."\ndeck = "DefaultDeck"\nignore = ["IgnoredDir"]\n',
        encoding="utf-8",
    )

    cfg_file = tmp_path / "config.json"
    manager = ProfileConfigManager(config_path=cfg_file)
    manager.save(AddonProfileSettings(profile_name="FolderSortProfile", vault_path=vault))

    dlg = ConfigWindow(manager, "FolderSortProfile")

    # 1. Verify folder scanning and level sorting (0 slashes, 1 slash, 2 slashes; then alphabetical)
    folders = dlg._get_available_vault_folders()
    expected_order = ["Alpha", "Zebra", "Alpha/Beta", "Alpha/Zeta", "Alpha/Beta/Gamma"]
    assert folders == expected_order
    assert ".obsidian" not in folders
    assert ".git" not in folders
    assert "IgnoredDir" not in folders

    # 2. In Deck Rules, row 0 cell (r, 0) has editable QComboBox with these folders
    dlg._add_routing_row()
    deck_folder_combo = dlg.routing_table.cellWidget(0, 0)
    assert isinstance(deck_folder_combo, qt.QComboBox)
    assert deck_folder_combo.isEditable()
    assert [deck_folder_combo.itemText(i) for i in range(deck_folder_combo.count())] == expected_order

    # Select an item from the list
    deck_folder_combo.setCurrentText("Alpha/Beta")
    deck_target_combo = dlg.routing_table.cellWidget(0, 1)
    deck_target_combo.setCurrentText("MyDeck::AlphaBeta")

    # Check extracted data
    assert dlg._get_routing_table_data() == [("Alpha/Beta", "MyDeck::AlphaBeta")]

    # 3. In Tag Rules, row 0 cell (r, 0) has editable QComboBox and supports arbitrary custom text
    dlg._add_alias_row()
    tag_folder_combo = dlg.alias_table.cellWidget(0, 0)
    assert isinstance(tag_folder_combo, qt.QComboBox)
    assert tag_folder_combo.isEditable()
    assert [tag_folder_combo.itemText(i) for i in range(tag_folder_combo.count())] == expected_order

    # Custom text that is not in the dropdown list
    tag_folder_combo.setCurrentText("Custom/Nested/Folder")
    tag_alias_combo = dlg.alias_table.cellWidget(0, 1)
    tag_alias_combo.setCurrentText("custom_tag")

    assert dlg._get_alias_table_data() == [("Custom/Nested/Folder", "custom_tag")]

    # 4. Save and verify persistence to m2a.toml
    dlg._on_save()
    saved_cfg = manager.load_vault(vault)
    assert saved_cfg.deck_routing == {"Alpha/Beta": "MyDeck::AlphaBeta"}
    assert saved_cfg.tag_aliases == {"Custom/Nested/Folder": "custom_tag"}
    dlg.close()
