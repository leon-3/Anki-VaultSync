"""Obsidian Vault auto-detection across Linux (native, Flatpak, Snap), macOS, and Windows."""
from __future__ import annotations

from dataclasses import dataclass
import json
import logging
import os
from pathlib import Path

logger = logging.getLogger("anki_vaultsync")


@dataclass
class DetectedVault:
    name: str
    path: Path
    is_open: bool = False
    ts: int = 0


def get_obsidian_config_paths() -> list[Path]:
    """Return potential locations of obsidian.json across platforms and packaging types."""
    paths: list[Path] = []
    home = Path.home()

    # 1. Linux Flatpak
    paths.append(home / ".var/app/md.obsidian.Obsidian/config/obsidian/obsidian.json")

    # 2. Linux native / XDG
    xdg_config = os.environ.get("XDG_CONFIG_HOME")
    if xdg_config:
        paths.append(Path(xdg_config) / "obsidian/obsidian.json")
    paths.append(home / ".config/obsidian/obsidian.json")

    # 3. Linux Snap
    paths.append(home / "snap/obsidian/current/.config/obsidian/obsidian.json")

    # 4. macOS
    paths.append(home / "Library/Application Support/obsidian/obsidian.json")

    # 5. Windows
    appdata = os.environ.get("APPDATA")
    if appdata:
        paths.append(Path(appdata) / "obsidian/obsidian.json")
    paths.append(home / "AppData/Roaming/obsidian/obsidian.json")

    return paths


def detect_obsidian_vaults(config_paths: list[Path] | None = None) -> list[DetectedVault]:
    """Scan obsidian.json files and return all detected valid vault directories.

    Results are deduplicated and sorted with currently open/active vaults first,
    followed by most recently used (highest ts).
    """
    targets = config_paths if config_paths is not None else get_obsidian_config_paths()
    vaults: dict[Path, DetectedVault] = {}

    for config_file in targets:
        if not config_file.is_file():
            continue
        try:
            content = config_file.read_text(encoding="utf-8")
            data = json.loads(content)
            raw_vaults = data.get("vaults", {})
            if not isinstance(raw_vaults, dict):
                continue

            for _, entry in raw_vaults.items():
                if not isinstance(entry, dict):
                    continue
                raw_path = entry.get("path")
                if not raw_path:
                    continue
                p = Path(raw_path).expanduser().resolve()
                if not p.is_dir():
                    continue

                is_open = bool(entry.get("open", False))
                ts = int(entry.get("ts", 0))
                name = p.name

                if p not in vaults:
                    vaults[p] = DetectedVault(name=name, path=p, is_open=is_open, ts=ts)
                else:
                    existing = vaults[p]
                    if is_open and not existing.is_open:
                        existing.is_open = True
                    if ts > existing.ts:
                        existing.ts = ts

        except Exception as exc:
            logger.debug("Failed reading obsidian config at %s: %s", config_file, exc)

    return sorted(vaults.values(), key=lambda v: (v.is_open, v.ts), reverse=True)
