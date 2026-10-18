from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from aqt.qt import QApplication, QDialog, QMovie, QPixmap, Qt

app = QApplication.instance()
if app is None:
    app = QApplication([])

from markdown2anki.config import Config, load_vault_config, save_vault_config

from src.profile_config import AddonProfileSettings, ProfileConfigManager
from src.ui.onboarding_dialog import (
    OnboardingDialog,
    OnboardingPageBase,
    PageCardBehavior,
    PageCompletion,
    PageVaultDeck,
    PageWelcome,
    PageWorkflow,
    StepHeaderWidget,
)
from src.ui.onboarding_state import OnboardingState


def test_step_header_widget_bounds_and_steps():
    header = StepHeaderWidget()
    assert header._current_step == 0

    header.set_current_step(2)
    assert header._current_step == 2

    # Negative bounds
    header.set_current_step(-5)
    assert header._current_step == 0

    # Over bounds
    header.set_current_step(10)
    assert header._current_step == 4


def test_media_container_asset_loading_and_visibility(tmp_path: Path):
    page = OnboardingPageBase()
    assert page.media_container.isHidden() is True

    # 1. Invalid or missing asset
    page.set_media_asset(tmp_path / "nonexistent.png")
    assert page.media_container.isHidden() is True

    # 2. Valid static asset
    img_path = tmp_path / "banner.png"
    pix = QPixmap(100, 100)
    pix.fill(Qt.GlobalColor.blue)
    pix.save(str(img_path))

    page.set_media_asset(img_path)
    assert not page.media_container.isHidden()
    assert page._movie is None

    # 3. Animated GIF asset lifecycle
    gif_path = tmp_path / "anim.gif"
    gif_path.write_bytes(bytes.fromhex("47494638396101000100800000ffffff00000021f90401000000002c00000000010001000002024401003b"))

    page.set_media_asset(gif_path, is_animated=True)
    assert not page.media_container.isHidden()
    assert page._movie is not None
    assert isinstance(page._movie, QMovie)

    # Simulate show/hide events
    page.hide()
    page.show()


def test_page2_loads_existing_m2a_toml_and_preserves_routing(tmp_path: Path):
    vault_dir = tmp_path / "MyObsidianVault"
    vault_dir.mkdir()

    # Pre-existing m2a.toml with custom routing and tags
    initial_cfg = Config(
        vault=vault_dir,
        deck="InitialDeck",
        cloze_notes=True,
        image_occlusion_mode="stage",
        suspend_new_occlusions=False,
        suspend_new_clozes=True,
        deck_routing={"Math": "MathematicsDeck"},
        tag_aliases={"Bio": "BiologyTag"},
    )
    save_vault_config(vault_dir, initial_cfg)

    cfg_file = tmp_path / "config.json"
    manager = ProfileConfigManager(config_path=cfg_file)

    dlg = OnboardingDialog(manager, "TestProfile")
    # Move to Page 2
    dlg.stack.setCurrentIndex(1)
    page2: PageVaultDeck = dlg.page_vault_deck
    page2.vault_edit.setText(str(vault_dir))
    page2._check_vault_path_async()

    assert not page2.status_badge.isHidden()
    assert "Valid vault found" in page2.status_badge.text()
    assert page2.btn_reset_toml.isHidden() is True

    # Simulate leaving Page 2 -> updates state
    page2.on_leave(dlg.state)
    assert dlg.state.has_existing_toml is True
    assert dlg.state.target_deck == "InitialDeck"
    assert dlg.state.image_occlusion_mode == "stage"

    # Simulate committing onboarding
    dlg._commit_onboarding()

    # Verify m2a.toml: settings updated, deck_routing and tag_aliases PRESERVED
    saved_cfg = load_vault_config(vault_dir)
    assert saved_cfg.deck == "InitialDeck"
    assert saved_cfg.deck_routing == {"Math": "MathematicsDeck"}
    assert saved_cfg.tag_aliases == {"Bio": "BiologyTag"}
    assert saved_cfg.image_occlusion_mode == "stage"

    # Profile config must be marked complete
    profile_settings = manager.load("TestProfile")
    assert profile_settings.onboarding_completed is True
    assert profile_settings.vault_path == vault_dir
    dlg.close()


def test_page2_handles_corrupted_m2a_toml_gracefully(tmp_path: Path):
    vault_dir = tmp_path / "BrokenVault"
    vault_dir.mkdir()
    toml_file = vault_dir / "m2a.toml"
    toml_file.write_text("deck = [invalid syntax", encoding="utf-8")

    cfg_file = tmp_path / "config.json"
    manager = ProfileConfigManager(config_path=cfg_file)

    dlg = OnboardingDialog(manager, "TestProfile")
    page2: PageVaultDeck = dlg.page_vault_deck
    page2.vault_edit.setText(str(vault_dir))
    page2._check_vault_path_async()

    assert not page2.status_badge.isHidden()
    assert "invalid" in page2.status_badge.text().lower()
    assert not page2.btn_reset_toml.isHidden()

    # Reset corrupt toml
    page2._reset_corrupt_toml()
    assert "Valid vault found" in page2.status_badge.text()
    assert page2.btn_reset_toml.isHidden() is True

    # Check file is now valid TOML
    loaded = load_vault_config(vault_dir)
    assert loaded.deck == "Default"
    dlg.close()


def test_cancel_or_escape_writes_nothing_to_disk(tmp_path: Path):
    vault_dir = tmp_path / "UnfinishedVault"
    vault_dir.mkdir()

    cfg_file = tmp_path / "config.json"
    manager = ProfileConfigManager(config_path=cfg_file)

    dlg = OnboardingDialog(manager, "TestProfile")
    dlg.page_vault_deck.vault_edit.setText(str(vault_dir))
    dlg.page_vault_deck.on_leave(dlg.state)

    # Reject / Set Up Later
    dlg.reject()

    # Nothing should be written to vault
    assert not (vault_dir / "m2a.toml").exists()
    assert not (vault_dir / "VaultSync Welcome.md").exists()

    # Profile must remain not onboarded
    settings = manager.load("TestProfile")
    assert settings.onboarding_completed is False
    assert settings.needs_onboarding() is True


def test_wizard_navigation_forward_and_back(tmp_path: Path):
    vault_dir = tmp_path / "NavVault"
    vault_dir.mkdir()

    cfg_file = tmp_path / "config.json"
    manager = ProfileConfigManager(config_path=cfg_file)

    dlg = OnboardingDialog(manager, "TestProfile")
    assert dlg.stack.currentIndex() == 0
    assert dlg.btn_back.isEnabled() is False
    assert dlg.btn_next.text() == "Next"

    # Advance to Page 2
    dlg._go_next()
    assert dlg.stack.currentIndex() == 1
    assert dlg.btn_back.isEnabled() is True

    # Enter valid vault
    dlg.page_vault_deck.vault_edit.setText(str(vault_dir))
    dlg.page_vault_deck._check_vault_path_async()
    assert dlg.page_vault_deck.is_valid() is True

    # Advance to Page 3 (Card Behavior)
    dlg._go_next()
    assert dlg.stack.currentIndex() == 2

    # Step back to Page 2
    dlg._go_back()
    assert dlg.stack.currentIndex() == 1

    # Step back to Page 1
    dlg._go_back()
    assert dlg.stack.currentIndex() == 0
    assert dlg.btn_back.isEnabled() is False

    dlg.close()


def test_page3_staging_mode_toggles_occlusion_dir_picker():
    page = PageCardBehavior()
    assert page.rb_native.isChecked() is True
    assert page.staging_box.isHidden() is True
    assert page.cb_suspend_occ.isEnabled() is True

    # Toggle Staging mode
    page.rb_stage.setChecked(True)
    assert not page.staging_box.isHidden()
    assert page.cb_suspend_occ.isEnabled() is False

    # Switch back to Native
    page.rb_native.setChecked(True)
    assert page.staging_box.isHidden() is True
    assert page.cb_suspend_occ.isEnabled() is True


def test_profile_will_close_cancels_pending_timer_and_closes_dialog(monkeypatch):
    from aqt.qt import QTimer
    from src.ui import menu

    timer = QTimer()
    timer.start(1000)
    menu._onboarding_timer = timer

    dummy_dlg = SimpleNamespace(
        close=lambda: setattr(dummy_dlg, "closed", True),
        closed=False,
    )
    menu._onboarding_dialog_instance = dummy_dlg  # type: ignore

    # Trigger profile will close
    menu.on_profile_will_close()

    assert menu._onboarding_timer is None
    assert timer.isActive() is False
    assert menu._onboarding_dialog_instance is None
    assert dummy_dlg.closed is True


def test_page5_close_button_finishes_without_sync(tmp_path: Path):
    vault_dir = tmp_path / "FinishVault"
    vault_dir.mkdir()

    cfg_file = tmp_path / "config.json"
    manager = ProfileConfigManager(config_path=cfg_file)

    dlg = OnboardingDialog(manager, "TestProfile")
    dlg.state.vault_path = vault_dir

    # Move to page 5
    dlg.stack.setCurrentIndex(4)
    dlg._update_footer_buttons()

    # Footer Next & Cancel should be hidden on page 5
    assert dlg.btn_next.isVisible() is False
    assert dlg.btn_cancel.isVisible() is False

    # Page 5 buttons exist
    assert hasattr(dlg.page_completion, "btn_close")
    assert hasattr(dlg.page_completion, "btn_sync")
    assert dlg.page_completion.btn_close.text() == "Close"
    assert "Sync Vault Directly" in dlg.page_completion.btn_sync.text()

    # Click Close
    dlg.page_completion.btn_close.click()

    assert dlg.state.run_initial_sync is False
    settings = manager.load("TestProfile")
    assert settings.onboarding_completed is True
    # Sample note must NOT be created
    assert not (vault_dir / "VaultSync Welcome.md").exists()


def test_page5_sync_directly_button_triggers_sync(tmp_path: Path, monkeypatch):
    vault_dir = tmp_path / "SyncDirectVault"
    vault_dir.mkdir()

    cfg_file = tmp_path / "config.json"
    manager = ProfileConfigManager(config_path=cfg_file)

    dlg = OnboardingDialog(manager, "TestProfile")
    dlg.state.vault_path = vault_dir

    sync_called = []
    import src.ui.menu as menu_mod
    monkeypatch.setattr(menu_mod, "_run_menu_sync", lambda **kw: sync_called.append(kw))

    dlg.stack.setCurrentIndex(4)
    dlg._update_footer_buttons()

    # Click Sync Vault Directly
    dlg.page_completion.btn_sync.click()

    assert dlg.state.run_initial_sync is True
    assert len(sync_called) == 1
    assert sync_called[0] == {"include_updates": True}
    settings = manager.load("TestProfile")
    assert settings.onboarding_completed is True
    assert not (vault_dir / "VaultSync Welcome.md").exists()


def test_page2_deck_hint_mentions_deck_routing_in_settings():
    page = PageVaultDeck()
    # Find all QLabel texts in page
    from aqt.qt import QLabel
    labels = page.findChildren(QLabel)
    hint_found = any("routing rules" in lbl.text().lower() and "settings" in lbl.text().lower() for lbl in labels)
    assert hint_found is True
