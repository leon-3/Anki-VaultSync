from __future__ import annotations

from typing import TYPE_CHECKING

from aqt import mw
from aqt.operations import QueryOp
from aqt.qt import QAction, QMenu
from aqt.utils import tooltip

from ..profile_config import ProfileConfigManager
from ..worker import build_sync_plan, execute_sync_plan
from .config_window import ConfigWindow
from .main_window import MainWindow

_main_window_instance: MainWindow | None = None
_config_window_instance: ConfigWindow | None = None
_manager = ProfileConfigManager()


def _get_active_profile() -> str:
    if mw is not None and hasattr(mw, "pm") and mw.pm is not None:
        return mw.pm.name
    return "Default"


def open_dashboard() -> None:
    global _main_window_instance
    profile_name = _get_active_profile()
    settings = _manager.load(profile_name)

    if not settings.vault_path or not settings.vault_path.is_dir():
        open_settings()
        return

    if _main_window_instance is None or not _main_window_instance.isVisible():
        _main_window_instance = MainWindow(_manager, profile_name, mw)
        _main_window_instance.show()
    else:
        _main_window_instance.activateWindow()
        _main_window_instance.raise_()


def open_settings() -> None:
    global _config_window_instance
    profile_name = _get_active_profile()
    _config_window_instance = ConfigWindow(_manager, profile_name, mw)
    _config_window_instance.exec()


def _run_menu_sync(include_updates: bool) -> None:
    profile_name = _get_active_profile()
    settings = _manager.load(profile_name)

    if not settings.vault_path or not settings.vault_path.is_dir():
        tooltip("VaultSync: Vault directory not configured. Opening Settings...")
        open_settings()
        return

    vault_p = settings.vault_path
    occ_dir = settings.occlusion_dir

    def _full_sync_op(col):
        plan = build_sync_plan(col, vault_p)
        report = execute_sync_plan(col, plan, include_updates=include_updates, occlusion_dir=occ_dir)
        return report

    def _on_success(report):
        if mw is not None and hasattr(mw, "deckBrowser"):
            mw.deckBrowser.refresh()
            mw.reset()
        tooltip(f"VaultSync: {report.created_count} added, {report.updated_count} updated.")

    QueryOp(
        parent=mw,
        op=_full_sync_op,
        success=_on_success,
    ).with_progress("VaultSync running...").run_in_background()


def quick_sync_new() -> None:
    _run_menu_sync(include_updates=False)


def sync_and_update_all() -> None:
    _run_menu_sync(include_updates=True)


def on_main_window_did_init() -> None:
    if mw is None or not hasattr(mw, "form") or not hasattr(mw.form, "menuTools"):
        return

    menu_tools = mw.form.menuTools
    vault_menu = QMenu("VaultSync", menu_tools)

    action_dash = QAction("Open Dashboard...", vault_menu)
    action_dash.triggered.connect(open_dashboard)
    vault_menu.addAction(action_dash)

    action_quick = QAction("Quick Sync (New Cards)", vault_menu)
    action_quick.triggered.connect(quick_sync_new)
    vault_menu.addAction(action_quick)

    action_update = QAction("Sync & Update (Modified Cards)", vault_menu)
    action_update.triggered.connect(sync_and_update_all)
    vault_menu.addAction(action_update)

    vault_menu.addSeparator()

    action_settings = QAction("Settings...", vault_menu)
    action_settings.triggered.connect(open_settings)
    vault_menu.addAction(action_settings)

    menu_tools.addMenu(vault_menu)


def on_profile_did_open() -> None:
    pass


def on_profile_will_close() -> None:
    global _main_window_instance, _config_window_instance
    if _main_window_instance is not None:
        _main_window_instance.close()
        _main_window_instance = None
    if _config_window_instance is not None:
        _config_window_instance.close()
        _config_window_instance = None
