from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from ..profile_config import DEFAULT_SHORTCUTS


@dataclass
class OnboardingState:
    """Central state object passed between OnboardingDialog pages."""

    vault_path: Path | None = None
    has_existing_toml: bool = False
    toml_error: str | None = None
    target_deck: str = "Default"
    cloze_notes: bool = True
    image_occlusion_mode: str = "native"
    occlusion_dir: Path | None = None
    suspend_new_occlusions: bool = True
    suspend_new_clozes: bool = False
    sync_on_startup: bool = False
    shortcuts: dict[str, str] = field(default_factory=lambda: dict(DEFAULT_SHORTCUTS))
    run_initial_sync: bool = False
