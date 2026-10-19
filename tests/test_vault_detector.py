from __future__ import annotations

import json
from pathlib import Path

from src.vault_detector import DetectedVault, detect_obsidian_vaults


def test_detect_obsidian_vaults_parses_and_sorts_correctly(tmp_path: Path):
    v1 = tmp_path / "VaultOne"
    v2 = tmp_path / "VaultTwo"
    v3 = tmp_path / "VaultThree"
    v1.mkdir()
    v2.mkdir()
    v3.mkdir()

    cfg_file = tmp_path / "obsidian.json"
    cfg_data = {
        "vaults": {
            "v1_id": {"path": str(v1), "ts": 1000},
            "v2_id": {"path": str(v2), "ts": 2000, "open": True},
            "v3_id": {"path": str(v3), "ts": 1500},
            "missing_id": {"path": str(tmp_path / "DoesNotExist"), "ts": 9999},
        }
    }
    cfg_file.write_text(json.dumps(cfg_data), encoding="utf-8")

    detected = detect_obsidian_vaults([cfg_file])
    assert len(detected) == 3
    # Active/open vault must come first
    assert detected[0].name == "VaultTwo"
    assert detected[0].is_open is True
    assert detected[0].path == v2

    # Remaining sorted by ts descending
    assert detected[1].name == "VaultThree"
    assert detected[1].ts == 1500
    assert detected[2].name == "VaultOne"
    assert detected[2].ts == 1000


def test_detect_obsidian_vaults_handles_malformed_and_missing(tmp_path: Path):
    # Non-existent file
    assert detect_obsidian_vaults([tmp_path / "missing.json"]) == []

    # Corrupted json
    bad_json = tmp_path / "bad.json"
    bad_json.write_text("{bad json syntax", encoding="utf-8")
    assert detect_obsidian_vaults([bad_json]) == []

    # Invalid structure (not dict)
    weird_json = tmp_path / "weird.json"
    weird_json.write_text(json.dumps(["not", "a", "dict"]), encoding="utf-8")
    assert detect_obsidian_vaults([weird_json]) == []


def test_detect_obsidian_vaults_deduplicates_across_config_paths(tmp_path: Path):
    v1 = tmp_path / "SharedVault"
    v1.mkdir()

    cfg1 = tmp_path / "cfg1.json"
    cfg2 = tmp_path / "cfg2.json"
    cfg1.write_text(json.dumps({"vaults": {"id1": {"path": str(v1), "ts": 100}}}), encoding="utf-8")
    cfg2.write_text(json.dumps({"vaults": {"id2": {"path": str(v1), "ts": 200, "open": True}}}), encoding="utf-8")

    detected = detect_obsidian_vaults([cfg1, cfg2])
    assert len(detected) == 1
    assert detected[0].name == "SharedVault"
    assert detected[0].is_open is True
    assert detected[0].ts == 200
