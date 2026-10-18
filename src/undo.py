"""Undo and Redo support for VaultSync operations in Anki."""
from __future__ import annotations

import threading
from typing import Any, TYPE_CHECKING

from markdown2anki.flags import clear_flags, write_flags
from markdown2anki.parser import Card

from .logger import log_error, log_info

if TYPE_CHECKING:
    from anki.collection import Collection

_lock = threading.Lock()
_sync_undo_stack: list[list[Card]] = []
_sync_redo_stack: list[list[Card]] = []


def _normalize_cards(cards: list[Any]) -> list[Card]:
    """Normalize a list of Card or (Card, card_id) tuples to Card objects with card_id set."""
    normalized: list[Card] = []
    for item in cards:
        if isinstance(item, tuple):
            c = item[0]
            if len(item) > 1 and item[1]:
                c.card_id = item[1]
            normalized.append(c)
        else:
            normalized.append(item)
    return normalized


def register_sync_added_cards(cards: list[Any]) -> None:
    """Record newly added/flagged cards for the latest VaultSync operation.

    Only non-empty lists are recorded. A new sync clears the redo stack.
    """
    if not cards:
        return

    with _lock:
        _sync_redo_stack.clear()
        _sync_undo_stack.append(list(_normalize_cards(cards)))


def clear_sync_undo_stack() -> None:
    """Clear all recorded undo and redo entries (e.g. on profile close or reload)."""
    with _lock:
        _sync_undo_stack.clear()
        _sync_redo_stack.clear()


def pop_last_sync_cards() -> list[Card] | None:
    """Pop and return the most recent sync's flagged cards, or None if stack is empty."""
    with _lock:
        if _sync_undo_stack:
            return _sync_undo_stack.pop()
        return None


def pop_last_sync_redo_cards() -> list[Card] | None:
    """Pop and return the most recent sync's undone cards for redo, or None if redo stack is empty."""
    with _lock:
        if _sync_redo_stack:
            return _sync_redo_stack.pop()
        return None


def get_undo_stack_depth() -> int:
    """Return the current depth of the undo stack (useful for inspection/tests)."""
    with _lock:
        return len(_sync_undo_stack)


def get_redo_stack_depth() -> int:
    """Return the current depth of the redo stack (useful for inspection/tests)."""
    with _lock:
        return len(_sync_redo_stack)


def on_state_did_undo(changes: Any) -> None:
    """Hook invoked by Anki's gui_hooks.state_did_undo after a backend undo."""
    op = getattr(changes, "operation", None)
    if op != "VaultSync":
        return

    raw_cards = pop_last_sync_cards()
    if not raw_cards:
        return

    cards = _normalize_cards(raw_cards)
    with _lock:
        _sync_redo_stack.append(cards)

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


def on_state_did_redo(changes: Any = None) -> None:
    """Hook invoked when a VaultSync operation is redone in Anki."""
    if changes is not None:
        op = getattr(changes, "operation", None)
        if op is not None and op != "VaultSync":
            return

    raw_cards = pop_last_sync_redo_cards()
    if not raw_cards:
        return

    cards = _normalize_cards(raw_cards)
    with _lock:
        _sync_undo_stack.append(cards)

    try:
        pairs = [(c, getattr(c, "card_id", None)) for c in cards if getattr(c, "card_id", None)]
        if pairs:
            flagged, problems = write_flags(pairs)
        else:
            flagged, problems = 0, []

        log_info(
            f"Redo VaultSync: restored ADDED flags for {flagged} card(s) in Obsidian."
            + (f" Problems: {'; '.join(problems)}" if problems else "")
        )
        try:
            from aqt import mw
            from aqt.utils import tooltip
            if mw is not None and flagged > 0:
                tooltip(f"VaultSync: Restored flags for {flagged} card(s) in Obsidian.", parent=mw, period=3000)
        except Exception:
            pass
    except Exception as exc:
        log_error(f"Redo VaultSync failed while restoring flags: {exc}")


def attach_redo_hook(col: Collection) -> None:
    """Wrap collection.redo to trigger on_state_did_redo when VaultSync is redone."""
    if not hasattr(col, "redo"):
        return
    if getattr(col.redo, "_vaultsync_hooked", False):
        return

    orig_redo = col.redo

    def _wrapped_redo(*args: Any, **kwargs: Any) -> Any:
        res = orig_redo(*args, **kwargs)
        if getattr(res, "operation", None) == "VaultSync":
            on_state_did_redo(res)
        return res

    _wrapped_redo._vaultsync_hooked = True  # type: ignore[attr-defined]
    col.redo = _wrapped_redo  # type: ignore[method-assign]
