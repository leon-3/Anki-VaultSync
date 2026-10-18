from __future__ import annotations

from src.sync_lock import (
    acquire_sync_lock,
    is_sync_running,
    release_sync_lock,
    sync_lock_context,
)


def test_sync_lock_acquire_and_release():
    release_sync_lock()
    assert not is_sync_running()

    assert acquire_sync_lock() is True
    assert is_sync_running() is True

    # Re-acquiring while locked must fail
    assert acquire_sync_lock() is False
    assert is_sync_running() is True

    release_sync_lock()
    assert is_sync_running() is False

    # Can acquire again after release
    assert acquire_sync_lock() is True
    release_sync_lock()
    assert is_sync_running() is False


def test_sync_lock_context_manager():
    release_sync_lock()
    assert not is_sync_running()

    with sync_lock_context() as acquired:
        assert acquired is True
        assert is_sync_running() is True
        # Nested acquire fails
        assert acquire_sync_lock() is False

    # Automatically released on context exit
    assert is_sync_running() is False
