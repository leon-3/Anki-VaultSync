# anki-vaultsync/__init__.py
from __future__ import annotations

import sys
from pathlib import Path

ADDON_DIR = Path(__file__).resolve().parent
VENDOR_DIR = str(ADDON_DIR / "vendor")
if VENDOR_DIR not in sys.path:
    sys.path.insert(0, VENDOR_DIR)

try:
    from aqt import gui_hooks
    from .src.ui.menu import (
        on_main_window_did_init,
        on_profile_did_open,
        on_profile_will_close,
    )
    from .src.undo import on_state_did_undo

    gui_hooks.main_window_did_init.append(on_main_window_did_init)
    gui_hooks.profile_did_open.append(on_profile_did_open)
    gui_hooks.profile_will_close.append(on_profile_will_close)
    gui_hooks.state_did_undo.append(on_state_did_undo)
except ImportError:
    # Running outside Anki (e.g. headless unit testing)
    pass
