"""Anki collection backup utilities for Anki-VaultSync."""
from __future__ import annotations

from typing import Any
from . import logger


def create_safety_backup(live_mw: Any = None, col: Any = None, reason: str = "sync") -> bool:
    """Create an immediate Anki collection backup before modifying or deleting cards.

    Uses Anki's native create_backup_now() (recommended by Anki for long-running
    CollectionOp/QueryOp routines) or col.create_backup() as fallback.
    Returns True if backup was successfully created or triggered.
    """
    import aqt
    target_mw = live_mw if live_mw is not None else getattr(aqt, "mw", None)

    # Strategy 1: mw.create_backup_now()
    if target_mw is not None and hasattr(target_mw, "create_backup_now"):
        try:
            logger.log_info(f"Creating pre-{reason} Anki collection backup via mw.create_backup_now()...")
            target_mw.create_backup_now()
            logger.log_info(f"Pre-{reason} Anki collection backup created successfully.")
            return True
        except Exception as exc:
            logger.log_warning(f"mw.create_backup_now() encountered: {exc}. Trying fallback...")

    # Strategy 2: col.create_backup() fallback
    target_col = col if col is not None else (getattr(target_mw, "col", None) if target_mw is not None else None)
    backup_folder = None
    if target_mw is not None and hasattr(target_mw, "pm") and target_mw.pm is not None and hasattr(target_mw.pm, "backupFolder"):
        try:
            backup_folder = target_mw.pm.backupFolder()
        except Exception:
            backup_folder = None

    if target_col is not None and hasattr(target_col, "create_backup") and backup_folder:
        try:
            logger.log_info(f"Creating pre-{reason} Anki collection backup via col.create_backup()...")
            target_col.create_backup(backup_folder=backup_folder, force=True, wait_for_completion=True)
            logger.log_info(f"Pre-{reason} Anki collection backup created successfully.")
            return True
        except Exception as exc:
            logger.log_warning(f"col.create_backup() failed: {exc}")
            return False

    logger.log_warning(f"Could not create pre-{reason} backup: Anki backup services not available in current environment.")
    return False
