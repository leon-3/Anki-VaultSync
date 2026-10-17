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
        "repair_templates",\
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
    assert dlg.tabs.tabText(0) == "General"
    assert dlg.tabs.tabText(1) == "Deck Routing"
    assert dlg.tabs.tabText(2) == "Tag Aliasing"
    assert dlg.tabs.tabText(3) == "Card Rules and Media"
    assert dlg.tabs.tabText(4) == "Advanced"
    assert dlg.tabs.tabText(5) == "Logs"
    assert dlg.vault_edit is not None
    assert dlg.deck_combo is not None
    assert dlg.basic_model_combo is not None
    assert dlg.cloze_model_combo is not None
    assert dlg.routing_table is not None
    assert dlg.alias_table is not None
    assert dlg.pad_single_digits_cb is not None
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
    assert warnings == ["Duplicate Deck Routing Rule"]

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
        dlg = ConfigWindow(manager, "TestProfile", initial_tab=5)
        assert dlg.tabs.currentIndex() == 5
        assert hasattr(dlg, "log_edit")
        text = dlg.log_edit.toPlainText()
        # Newest should be on top
        first_line = text.splitlines()[0]
        assert "Second test warning" in first_line
        dlg.close()

        # Test initial_tab by name
        dlg_named = ConfigWindow(manager, "TestProfile", initial_tab="Logs")
        assert dlg_named.tabs.currentIndex() == 5
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

    # Modify settings in UI
    dlg.suspend_new_clozes_cb.setChecked(True)
    dlg.image_occlusion_mode_combo.setCurrentIndex(1)  # "stage"
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
