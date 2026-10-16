from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from markdown2anki.config import Config, load_vault_config, save_vault_config

try:
    from aqt import mw
except ImportError:
    mw = None  # Running headless or in unit tests without Anki runtime


@dataclass
class AddonProfileSettings:
    profile_name: str
    vault_path: Path | None = None
    occlusion_dir: Path | None = None
    auto_close_on_success: bool = False
    show_diff_before_update: bool = True
    basic_model_id: int | None = None
    cloze_model_id: int | None = None


class ProfileConfigManager:
    """Manages profile-scoped add-on settings in config.json and vault sync rules in m2a.toml."""

    def __init__(self, config_path: Path | None = None, addon_name: str | None = None) -> None:
        self._config_path = config_path
        self._addon_name = addon_name or (__name__.split(".")[0] if "." in __name__ else "anki_vaultsync")

    def _read_raw_config(self) -> dict[str, Any]:
        if self._config_path is not None and self._config_path.is_file():
            try:
                return json.loads(self._config_path.read_text(encoding="utf-8"))
            except Exception:
                return {}

        if mw is not None and hasattr(mw, "addonManager") and mw.addonManager is not None:
            config = mw.addonManager.getConfig(self._addon_name)
            if isinstance(config, dict):
                return config

        default_file = Path(__file__).resolve().parent.parent / "config.json"
        if default_file.is_file():
            try:
                return json.loads(default_file.read_text(encoding="utf-8"))
            except Exception:
                return {}
        return {}

    def _write_raw_config(self, data: dict[str, Any]) -> None:
        if self._config_path is not None:
            self._config_path.parent.mkdir(parents=True, exist_ok=True)
            self._config_path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
            return

        if mw is not None and hasattr(mw, "addonManager") and mw.addonManager is not None:
            mw.addonManager.writeConfig(self._addon_name, data)
            return

        default_file = Path(__file__).resolve().parent.parent / "config.json"
        if default_file.is_file():
            default_file.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")

    def load(self, profile_name: str) -> AddonProfileSettings:
        raw = self._read_raw_config()
        auto_close = bool(raw.get("auto_close_on_success", False))
        show_diff = bool(raw.get("show_diff_before_update", True))

        profiles: dict[str, Any] = raw.get("profiles", {})
        prof_data = profiles.get(profile_name)
        if not isinstance(prof_data, dict):
            prof_data = profiles.get("Default", {})
        if not isinstance(prof_data, dict):
            prof_data = {}

        raw_vault = prof_data.get("vault_path")
        raw_occlusion = prof_data.get("occlusion_dir")

        vault_path = Path(raw_vault).expanduser().resolve() if raw_vault else None
        occlusion_dir = Path(raw_occlusion).expanduser().resolve() if raw_occlusion else None

        raw_b_id = prof_data.get("basic_model_id")
        raw_c_id = prof_data.get("cloze_model_id")
        basic_model_id = int(raw_b_id) if raw_b_id is not None and str(raw_b_id).isdigit() else None
        cloze_model_id = int(raw_c_id) if raw_c_id is not None and str(raw_c_id).isdigit() else None

        return AddonProfileSettings(
            profile_name=profile_name,
            vault_path=vault_path,
            occlusion_dir=occlusion_dir,
            auto_close_on_success=auto_close,
            show_diff_before_update=show_diff,
            basic_model_id=basic_model_id,
            cloze_model_id=cloze_model_id,
        )

    def save(self, settings: AddonProfileSettings) -> None:
        raw = self._read_raw_config()
        raw["auto_close_on_success"] = settings.auto_close_on_success
        raw["show_diff_before_update"] = settings.show_diff_before_update

        profiles: dict[str, Any] = raw.setdefault("profiles", {})
        prof_data: dict[str, Any] = profiles.setdefault(settings.profile_name, {})

        prof_data["vault_path"] = str(settings.vault_path) if settings.vault_path else ""
        prof_data["occlusion_dir"] = str(settings.occlusion_dir) if settings.occlusion_dir else ""
        prof_data["basic_model_id"] = settings.basic_model_id
        prof_data["cloze_model_id"] = settings.cloze_model_id

        self._write_raw_config(raw)

    @staticmethod
    def load_vault(vault_path: Path) -> Config:
        return load_vault_config(vault_path)

    @staticmethod
    def save_vault(vault_path: Path, config: Config) -> None:
        save_vault_config(vault_path, config)
