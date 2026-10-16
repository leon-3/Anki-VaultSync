from __future__ import annotations

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
    required_keys = [
        "vault_path",
        "occlusion_dir",
        "default_deck",
        "base_tag",
        "hash_media",
        "cloze_notes",
        "re_stage_occlusions",
        "show_diff_before_update",
        "auto_close_on_success",
        "repair_templates",
        "subdirectories",
        "ignore",
        "decks",
        "basic_model_id",
        "cloze_model_id",
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
    assert "Reverse automatic differentiation." in diff_html


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
    assert dlg.tabs.count() == 5
    assert dlg.tabs.tabText(4) == "Logs"
    assert dlg.vault_edit is not None
    assert dlg.deck_combo is not None
    assert dlg.basic_model_combo is not None
    assert dlg.cloze_model_combo is not None
    dlg.close()


def test_config_window_deck_routing_roundtrip(tmp_path: Path):
    vault_path = tmp_path / "vault"
    vault_path.mkdir()
    toml_path = vault_path / "m2a.toml"
    toml_path.write_text(
        'vault = "."\n[decks]\n"everyday" = "01. Everyday"\n[subdirectories]\n',
        encoding="utf-8",
    )

    cfg_file = tmp_path / "config.json"
    manager = ProfileConfigManager(config_path=cfg_file)
    manager.save(AddonProfileSettings(profile_name="Profile1", vault_path=vault_path))

    dlg = ConfigWindow(manager, "Profile1")
    assert dlg.routing_table.rowCount() == 1
    assert dlg.routing_table.item(0, 0).text() == "everyday"
    assert dlg.routing_table.item(0, 1).text() == "01. Everyday"

    # Add a rule
    dlg._add_routing_row()
    dlg.routing_table.item(1, 0).setText("work")
    dlg.routing_table.item(1, 1).setText("02. Work")

    dlg._on_save()
    dlg.close()

    saved_cfg = manager.load_vault(vault_path)
    assert saved_cfg.decks == {"everyday": "01. Everyday", "work": "02. Work"}
    assert saved_cfg.subdirectories == {}


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
    assert "(Not Configured)" in win.vault_label.text()
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
    assert win.sync_btn.text() == "Sync"
    assert win.diff_btn.text() == "Preview Diff"
    assert win.rescan_btn.text() == "Rescan Vault"
    win.close()


def test_config_window_orphans_button_exists(tmp_path: Path):
    cfg_file = tmp_path / "config.json"
    manager = ProfileConfigManager(config_path=cfg_file)
    dlg = ConfigWindow(manager, "TestProfile")
    assert hasattr(dlg, "orphans_btn")
    assert dlg.orphans_btn.text() == "Find & View Orphaned Cards in Browser"
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
        dlg = ConfigWindow(manager, "TestProfile", initial_tab=4)
        assert dlg.tabs.currentIndex() == 4
        assert hasattr(dlg, "log_edit")
        text = dlg.log_edit.toPlainText()
        # Newest should be on top
        first_line = text.splitlines()[0]
        assert "Second test warning" in first_line
        dlg.close()
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


def test_config_window_buttons_not_default(tmp_path: Path):
    cfg_file = tmp_path / "config.json"
    manager = ProfileConfigManager(config_path=cfg_file)
    dlg = ConfigWindow(manager, "TestProfile")
    save_btn = dlg.button_box.button(dlg.button_box.StandardButton.Save)
    assert not save_btn.isDefault()
    assert not save_btn.autoDefault()
    dlg.close()


def test_main_window_buttons_not_default(tmp_path: Path):
    cfg_file = tmp_path / "config.json"
    manager = ProfileConfigManager(config_path=cfg_file)
    win = MainWindow(manager, "TestDashboard")
    assert not win.settings_btn.isDefault()
    assert not win.settings_btn.autoDefault()
    win.close()


def test_config_window_unsaved_changes_dirty_check(tmp_path: Path):
    cfg_file = tmp_path / "config.json"
    manager = ProfileConfigManager(config_path=cfg_file)
    dlg = ConfigWindow(manager, "TestProfile")

    # Initially, no changes
    assert not dlg._has_unsaved_changes()

    # Make a change
    dlg.vault_edit.setText("/some/new/vault/path")
    assert dlg._has_unsaved_changes()

    # Revert change
    dlg.vault_edit.setText("")
    assert not dlg._has_unsaved_changes()

    # Make another change and save
    dlg.tag_edit.setText("new_tag")
    assert dlg._has_unsaved_changes()
    dlg._on_save()
    assert not dlg._has_unsaved_changes()
    dlg.close()
