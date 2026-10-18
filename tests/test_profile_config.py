from __future__ import annotations

import json
from pathlib import Path

from markdown2anki.config import Config
from src.profile_config import AddonProfileSettings, DEFAULT_SHORTCUTS, ProfileConfigManager


def test_profile_config_isolation(tmp_path: Path):
    config_file = tmp_path / "config.json"
    manager = ProfileConfigManager(config_path=config_file)

    # Initially empty/default
    settings_a = manager.load("University")
    assert settings_a.profile_name == "University"
    assert settings_a.vault_path is None

    # Save settings for University profile
    vault_a = tmp_path / "VaultUni"
    vault_a.mkdir()
    manager.save(
        AddonProfileSettings(
            profile_name="University",
            vault_path=vault_a,
            occlusion_dir=vault_a / "occlusions",
            auto_close_on_success=True,
            show_diff_before_update=True,
        )
    )

    # Save settings for Languages profile
    vault_b = tmp_path / "VaultLang"
    vault_b.mkdir()
    manager.save(
        AddonProfileSettings(
            profile_name="Languages",
            vault_path=vault_b,
            occlusion_dir=vault_b / "occlusions",
            auto_close_on_success=False,
            show_diff_before_update=False,
        )
    )

    # Reload from disk and verify strict isolation
    reloaded_a = manager.load("University")
    assert reloaded_a.vault_path == vault_a
    assert reloaded_a.occlusion_dir == vault_a / "occlusions"

    reloaded_b = manager.load("Languages")
    assert reloaded_b.vault_path == vault_b
    assert reloaded_b.occlusion_dir == vault_b / "occlusions"

    # Verify JSON file structure
    raw = json.loads(config_file.read_text(encoding="utf-8"))
    assert "University" in raw["profiles"]
    assert "Languages" in raw["profiles"]
    assert raw["profiles"]["University"]["vault_path"] == str(vault_a)
    assert raw["profiles"]["Languages"]["vault_path"] == str(vault_b)


def test_vault_toml_comment_preservation(tmp_path: Path):
    vault_dir = tmp_path / "MyVault"
    vault_dir.mkdir()
    toml_path = vault_dir / "m2a.toml"

    # Write initial toml with comments
    initial_toml = (
        "# User custom comment for deck\n"
        'vault = "."\n'
        'deck = "CustomDeck"\n'
        "\n"
        "# Another comment\n"
        'base_tag = "MyBase"\n'
    )
    toml_path.write_text(initial_toml, encoding="utf-8")

    # Load vault config
    cfg = ProfileConfigManager.load_vault(vault_dir)
    assert cfg.deck == "CustomDeck"
    assert cfg.base_tag == "MyBase"

    # Modify deck and save back
    cfg.deck = "NewDeckName"
    ProfileConfigManager.save_vault(vault_dir, cfg)

    # Check that comments survived
    new_text = toml_path.read_text(encoding="utf-8")
    assert "# User custom comment for deck" in new_text
    assert "# Another comment" in new_text
    assert 'deck = "NewDeckName"' in new_text


def test_profile_config_sync_on_startup_roundtrip(tmp_path: Path):
    config_file = tmp_path / "config.json"
    manager = ProfileConfigManager(config_path=config_file)

    # Defaults to False
    settings = manager.load("Default")
    assert settings.sync_on_startup is False

    # Enable sync_on_startup
    settings.sync_on_startup = True
    manager.save(settings)

    reloaded = manager.load("Default")
    assert reloaded.sync_on_startup is True


def test_profile_config_shortcuts_roundtrip(tmp_path: Path):
    config_file = tmp_path / "config.json"
    manager = ProfileConfigManager(config_path=config_file)

    # Loads defaults initially
    settings = manager.load("Default")
    assert settings.shortcuts == DEFAULT_SHORTCUTS

    # Customize shortcuts
    custom_shortcuts = {
        "open_dashboard": "Ctrl+Shift+D",
        "quick_sync": "Ctrl+Shift+Q",
        "full_sync": "Ctrl+Shift+F",
    }
    settings.shortcuts = custom_shortcuts
    manager.save(settings)

    # Reload from disk
    reloaded = manager.load("Default")
    assert reloaded.shortcuts == custom_shortcuts


def test_unconfigured_profile_does_not_inherit_default_profile(tmp_path: Path):
    config_file = tmp_path / "config.json"
    manager = ProfileConfigManager(config_path=config_file)

    # Configure Default profile with specific values
    manager.save(
        AddonProfileSettings(
            profile_name="Default",
            vault_path=tmp_path / "DefaultVault",
            occlusion_dir=tmp_path / "DefaultOcclusions",
            sync_on_startup=True,
            auto_close_on_success=True,
            show_diff_before_update=True,
        )
    )

    # Load a previously unseen/unconfigured profile
    unconfigured = manager.load("GuestProfile")
    assert unconfigured.profile_name == "GuestProfile"
    assert unconfigured.vault_path is None
    assert unconfigured.occlusion_dir is None
    assert unconfigured.sync_on_startup is False
    assert unconfigured.auto_close_on_success is False
    assert unconfigured.show_diff_before_update is True  # default setting


def test_write_raw_config_creates_missing_file_and_dirs(tmp_path: Path):
    nested_config = tmp_path / "nested" / "subfolder" / "config.json"
    assert not nested_config.parent.exists()
    assert not nested_config.exists()

    manager = ProfileConfigManager(config_path=nested_config)
    manager.save(
        AddonProfileSettings(
            profile_name="NewProfile",
            vault_path=tmp_path / "Vault",
        )
    )

    assert nested_config.is_file()
    loaded = manager.load("NewProfile")
    assert loaded.vault_path == tmp_path / "Vault"
