"""Undo support for VaultSync operations in Anki."""
from __future__ import annotations

import threading
from typing import Any

from markdown2anki.flags import clear_flags
from markdown2anki.parser import Card

from .logger import log_error, log_info

_lock = threading.Lock()
_sync_undo_stack: list[list[Card]] = []


def register_sync_added_cards(cards: list[Card]) -> None:
    """Record newly added/flagged cards for the latest VaultSync operation."""
    with _lock:
        _sync_undo_stack.append(list(cards))


def clear_sync_undo_stack() -> None:
    """Clear all recorded undo entries (e.g. on profile close or reload)."""
    with _lock:
        _sync_undo_stack.clear()


def pop_last_sync_cards() -> list[Card] | None:
    """Pop and return the most recent sync's flagged cards, or None if stack is empty."""
    with _lock:
        if _sync_undo_stack:
            return _sync_undo_stack.pop()
        return None


def get_undo_stack_depth() -> int:
    """Return the current depth of the undo stack (useful for inspection/tests)."""
    with _lock:
        return len(_sync_undo_stack)


def on_state_did_undo(changes: Any) -> None:
    """Hook invoked by Anki's gui_hooks.state_did_undo after a backend undo."""
    op = getattr(changes, "operation", None)
    if op != "VaultSync":
        return

    cards = pop_last_sync_cards()
    if not cards:
        return

    try:
        cleared, problems = clear_flags(cards)
        log_info(
            f"Undo VaultSync: cleared ADDED flags for {cleared} card(s) in Obsidian."
            + (f" Problems: {'; '.join(problems)}" if problems else "")
        )
        try:
            from aqt import mw
            from aqt.utils import tooltip
            if mw is not None and cleared > 0:
                tooltip(f"VaultSync: Cleared flags for {cleared} card(s) in Obsidian.", parent=mw, period=3000)
        except Exception:
            pass
    except Exception as exc:
        log_error(f"Undo VaultSync failed while clearing flags: {exc}")
