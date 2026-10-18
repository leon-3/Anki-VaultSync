# anki-vaultsync/__init__.py
from __future__ import annotations

import sys
from pathlib import Path

ADDON_DIR = Path(__file__).resolve().parent
VENDOR_DIR = str(ADDON_DIR / "vendor")
if VENDOR_DIR not in sys.path:
    sys.path.insert(0, VENDOR_DIR)

try:
    import aqt
    from aqt import gui_hooks
except ImportError:
    # Running outside Anki (e.g. headless unit testing)
    gui_hooks = None

if gui_hooks is not None:
    try:
        if __package__:
            from .src.ui.menu import (
                on_main_window_did_init,
                on_profile_did_open,
                on_profile_will_close,
            )
            from .src.undo import on_state_did_undo
        else:
            from src.ui.menu import (
                on_main_window_did_init,
                on_profile_did_open,
                on_profile_will_close,
            )
            from src.undo import on_state_did_undo

        gui_hooks.main_window_did_init.append(on_main_window_did_init)
        gui_hooks.profile_did_open.append(on_profile_did_open)
        gui_hooks.profile_will_close.append(on_profile_will_close)
        gui_hooks.state_did_undo.append(on_state_did_undo)

        # In case the add-on is loaded/reloaded after main window already initialized
        mw = getattr(aqt, "mw", None)
        if mw is not None and hasattr(mw, "form") and hasattr(mw.form, "menubar"):
            on_main_window_did_init()
    except Exception as exc:
        import traceback
        traceback.print_exc()
