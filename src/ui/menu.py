from __future__ import annotations
from .. import logger

from typing import TYPE_CHECKING, Any

from aqt import mw
from aqt.operations import QueryOp
from aqt.qt import QAction, QKeySequence, QMenu, sip
from aqt.utils import tooltip

from ..profile_config import AddonProfileSettings, ProfileConfigManager
from ..worker import build_sync_plan, execute_sync_plan
from .config_window import ConfigWindow
from .main_window import MainWindow
from .shortcuts import find_shortcut_conflict
from .notification import show_sync_notification

_main_window_instance: MainWindow | None = None
_config_window_instance: ConfigWindow | None = None
_manager = ProfileConfigManager()

_action_dash: QAction | None = None
_action_quick: QAction | None = None
_action_update: QAction | None = None


def _is_valid_action(action: Any) -> bool:
    if action is None:
        return False
    if sip is not None and hasattr(sip, "isdeleted"):
        try:
            return not sip.isdeleted(action)
        except Exception:
            return False
    return True


def get_menu_actions() -> dict[str, QAction | None]:
    return {
        "open_dashboard": _action_dash if _is_valid_action(_action_dash) else None,
        "quick_sync": _action_quick if _is_valid_action(_action_quick) else None,
        "full_sync": _action_update if _is_valid_action(_action_update) else None,
    }


def apply_menu_shortcuts(settings: AddonProfileSettings | None = None) -> None:
    """Applies configured shortcuts to VaultSync menu actions.

    If a shortcut is already taken by Anki core, another add-on, or duplicates another
    VaultSync action, the shortcut is automatically disabled and a warning is logged.
    """
    global _action_dash, _action_quick, _action_update

    if not _is_valid_action(_action_dash):
        _action_dash = None
    if not _is_valid_action(_action_quick):
        _action_quick = None
    if not _is_valid_action(_action_update):
        _action_update = None

    actions = {
        "open_dashboard": _action_dash,
        "quick_sync": _action_quick,
        "full_sync": _action_update,
    }

    if not any(actions.values()):
        return

    if settings is None:
        profile_name = _get_active_profile()
        settings = _manager.load(profile_name)

    configured_shortcuts = settings.shortcuts or {}
    allowed = {act for act in actions.values() if act is not None}
    assigned_canonical: dict[str, str] = {}

    for key, action in actions.items():
        if action is None:
            continue

        sc_str = (configured_shortcuts.get(key) or "").strip()
        if not sc_str:
            action.setShortcut(QKeySequence())
            continue

        seq = QKeySequence(sc_str)
        if seq.isEmpty():
            logger.log_warning(f"VaultSync shortcut '{sc_str}' for '{key}' is invalid. Disabling shortcut.")
            action.setShortcut(QKeySequence())
            continue

        canonical = seq.toString(QKeySequence.SequenceFormat.PortableText).casefold()

        # Check collision within our own actions
        if canonical in assigned_canonical:
            dup_key = assigned_canonical[canonical]
            logger.log_warning(
                f"VaultSync shortcut '{sc_str}' for '{key}' duplicates '{dup_key}'. Automatically disabling shortcut."
            )
            action.setShortcut(QKeySequence())
            continue

        # Check collision with Anki or another add-on
        conflict = find_shortcut_conflict(sc_str, allowed_actions=allowed)
        if conflict:
            logger.log_warning(
                f"VaultSync shortcut '{sc_str}' for '{key}' is already taken by '{conflict}'. Automatically disabling shortcut."
            )
            action.setShortcut(QKeySequence())
            continue

        action.setShortcut(seq)
        assigned_canonical[canonical] = key
        logger.log_info(f"VaultSync shortcut '{sc_str}' active for '{key}'.")


def _get_active_profile() -> str:
    if mw is not None and hasattr(mw, "pm") and mw.pm is not None:
        return mw.pm.name
    return "Default"


def open_dashboard() -> None:
    global _main_window_instance
    profile_name = _get_active_profile()

    if _main_window_instance is None or not _main_window_instance.isVisible():
        _main_window_instance = MainWindow(_manager, profile_name, mw)
        _main_window_instance.show()
    else:
        _main_window_instance.activateWindow()
        _main_window_instance.raise_()


def open_settings() -> None:
    global _config_window_instance
    profile_name = _get_active_profile()
    if _config_window_instance is None or not _config_window_instance.isVisible():
        _config_window_instance = ConfigWindow(_manager, profile_name, mw)
        _config_window_instance.show()
    else:
        _config_window_instance.activateWindow()
        _config_window_instance.raise_()


def _run_menu_sync(include_updates: bool, is_startup: bool = False) -> None:
    profile_name = _get_active_profile()
    settings = _manager.load(profile_name)

    if not settings.vault_path or not settings.vault_path.is_dir():
        if not is_startup:
            tooltip("VaultSync: Vault directory not configured. Opening Settings...")
            open_settings()
        return

    vault_p = settings.vault_path
    occ_dir = settings.occlusion_dir
    b_mid = settings.basic_model_id
    c_mid = settings.cloze_model_id

    tag = "startup sync" if is_startup else "menu sync"
    logger.log_info(f"Starting {tag} (include_updates={include_updates})...")

    def _full_sync_op(col):
        plan = build_sync_plan(
            col,
            vault_p,
            basic_model_id=b_mid,
            cloze_model_id=c_mid,
        )
        report = execute_sync_plan(
            col,
            plan,
            include_updates=include_updates,
            occlusion_dir=occ_dir,
            basic_model_id=b_mid,
            cloze_model_id=c_mid,
        )
        return report

    def _on_success(report):
        if mw is not None and hasattr(mw, "deckBrowser"):
            mw.deckBrowser.refresh()
            mw.reset()

        logger.log_info(
            f"{tag.capitalize()} finished: {report.created_count} added, {report.updated_count} updated, "
            f"{report.deck_moved_count} moved, {report.media_added_count} media file(s)."
        )
        for f_path, reason in report.skipped_files:
            logger.log_warning(f"Skipped {f_path.name}: {reason}")
        for warn in report.warnings:
            logger.log_warning(warn)

        show_sync_notification(report, parent=mw, include_updates=include_updates, is_startup=is_startup)

    def _on_failure(exc: Exception) -> None:
        logger.log_error(f"Menu sync failed: {exc}")
        tooltip(f"⚠️ VaultSync error: {exc}", period=5000)

    op = QueryOp(
        parent=mw,
        op=_full_sync_op,
        success=_on_success,
    ).failure(_on_failure)
    if not is_startup:
        op = op.with_progress("VaultSync running...")
    op.run_in_background()


def quick_sync_new() -> None:
    _run_menu_sync(include_updates=False)


def sync_and_update_all() -> None:
    _run_menu_sync(include_updates=True)


def on_main_window_did_init() -> None:
    global _action_dash, _action_quick, _action_update

    if mw is None or not hasattr(mw, "form") or not hasattr(mw.form, "menubar"):
        return

    menubar = mw.form.menubar
    vault_menu = QMenu("VaultSync", menubar)

    _action_dash = QAction("Open Dashboard...", vault_menu)
    _action_dash.triggered.connect(open_dashboard)
    vault_menu.addAction(_action_dash)

    _action_quick = QAction("Quick Sync (New Cards)", vault_menu)
    _action_quick.triggered.connect(quick_sync_new)
    vault_menu.addAction(_action_quick)

    _action_update = QAction("Sync and Update (Modified Cards)", vault_menu)
    _action_update.triggered.connect(sync_and_update_all)
    vault_menu.addAction(_action_update)

    vault_menu.addSeparator()

    action_settings = QAction("Settings...", vault_menu)
    action_settings.triggered.connect(open_settings)
    vault_menu.addAction(action_settings)

    # Insert directly into top-level menubar right before Help (matching AnKing and standard add-ons)
    help_menu = getattr(mw.form, "menuHelp", None)
    if help_menu is not None and hasattr(help_menu, "menuAction"):
        menubar.insertMenu(help_menu.menuAction(), vault_menu)
    else:
        menubar.addMenu(vault_menu)

    apply_menu_shortcuts()


def on_profile_did_open() -> None:
    profile_name = _get_active_profile()
    settings = _manager.load(profile_name)
    apply_menu_shortcuts(settings)
    if settings.sync_on_startup and settings.vault_path and settings.vault_path.is_dir():
        logger.log_info(f"Auto-sync on startup triggered for profile '{profile_name}'...")
        _run_menu_sync(include_updates=True, is_startup=True)


def on_profile_will_close() -> None:
    global _main_window_instance, _config_window_instance
    if _main_window_instance is not None:
        _main_window_instance.close()
        _main_window_instance = None
    if _config_window_instance is not None:
        _config_window_instance.close()
        _config_window_instance = None
