"""Global mutual exclusion lock for VaultSync background and UI operations."""
from __future__ import annotations

import threading
from contextlib import contextmanager
from typing import Generator

_lock = threading.Lock()
_is_running = False


def is_sync_running() -> bool:
    """Return True if a sync, scan, or maintenance operation is currently running."""
    with _lock:
        return _is_running


def acquire_sync_lock() -> bool:
    """Attempt to acquire the sync lock.

    Returns True if successfully acquired, False if another operation is already active.
    """
    global _is_running
    with _lock:
        if _is_running:
            return False
        _is_running = True
        return True


def release_sync_lock() -> None:
    """Release the sync lock."""
    global _is_running
    with _lock:
        _is_running = False


@contextmanager
def sync_lock_context() -> Generator[bool, None, None]:
    """Context manager attempting to acquire sync lock, releasing it on exit."""
    acquired = acquire_sync_lock()
    try:
        yield acquired
    finally:
        if acquired:
            release_sync_lock()
