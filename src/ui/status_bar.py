from __future__ import annotations

import datetime
import os
from pathlib import Path
from typing import TYPE_CHECKING, Any

import aqt
from aqt import mw
from aqt.operations import QueryOp
from aqt.qt import (
    QAction,
    QCursor,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMenu,
    QMouseEvent,
    QPoint,
    QTimer,
    Qt,
    sip,
)

from .. import logger
from ..profile_config import AddonProfileSettings, ProfileConfigManager
from ..sync_lock import is_sync_running

if TYPE_CHECKING:
    from ..contracts import SyncPlan, SyncReport


def _get_mw() -> Any:
    return aqt.mw if getattr(aqt, "mw", None) is not None else mw


def _is_deleted(widget: Any) -> bool:
    if widget is None:
        return True
    if sip is not None and hasattr(sip, "isdeleted"):
        try:
            return sip.isdeleted(widget)
        except Exception:
            return False
    return False


# Minimalist, elegant colors (no emojis)
COLOR_IDLE = "#10B981"         # Subtle emerald green (up to date)
COLOR_PENDING = "#3B82F6"      # Clean accent blue (cards available to sync)
COLOR_SYNCING = "#8B5CF6"      # Soft purple / violet (sync in progress)
COLOR_SUCCESS = "#10B981"      # Emerald green (sync completed)
COLOR_ERROR = "#EF4444"        # Soft red (sync error)
COLOR_UNCONFIGURED = "#9CA3AF" # Muted gray (no vault configured)

_status_bar_instance: VaultSyncStatusBarWidget | None = None
_manager = ProfileConfigManager()


def _get_vault_latest_mtime(vault_path: Path) -> float:
    """Return the latest mtime among markdown files in vault, skipping hidden directories."""
    latest = 0.0
    try:
        for root, dirs, files in os.walk(vault_path):
            dirs[:] = [d for d in dirs if not d.startswith(".")]
            for f in files:
                if f.endswith(".md"):
                    try:
                        m = os.path.getmtime(os.path.join(root, f))
                        if m > latest:
                            latest = m
                    except OSError:
                        pass
    except Exception:
        pass
    return latest


class VaultSyncStatusBarWidget(QFrame):
    """Minimalist status bar widget for VaultSync, displayed in the bottom-right of Anki's main window.

    - Smart-Compact: In idle/up-to-date state, shows only a discreet colored indicator dot.
    - When cards are available in the Obsidian vault, expands subtly to show the count (e.g. '3 pending').
    - Reports sync progress ('Syncing...'), success, and errors clearly with zero emojis.
    - Left-click opens the Dashboard (or Settings if unconfigured).
    - Right-click opens a context menu with direct sync actions and settings.
    """

    def __init__(self, parent: Any = None) -> None:
        super().__init__(parent)
        self.setObjectName("vaultSyncStatusBar")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedHeight(22)

        self._state: str = "idle"
        self._creates_count: int = 0
        self._updates_count: int = 0
        self._last_checked_mtime: float = 0.0
        self._last_synced_time_str: str | None = None

        layout = QHBoxLayout(self)
        layout.setContentsMargins(6, 2, 6, 2)
        layout.setSpacing(6)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self._dot = QLabel(self)
        self._dot.setObjectName("statusDot")
        self._dot.setFixedSize(8, 8)

        self._label = QLabel(self)
        self._label.setObjectName("statusLabel")
        self._label.setStyleSheet("font-size: 11px; font-weight: 500; color: palette(text);")

        layout.addWidget(self._dot)
        layout.addWidget(self._label)

        self.setStyleSheet("""
            QFrame#vaultSyncStatusBar {
                background-color: transparent;
                border-radius: 4px;
                padding: 1px 4px;
            }
            QFrame#vaultSyncStatusBar:hover {
                background-color: rgba(127, 127, 127, 0.18);
            }
        """)

        # Revert timer for temporary success state (4 seconds)
        self._revert_timer = QTimer(self)
        self._revert_timer.setSingleShot(True)
        self._revert_timer.timeout.connect(self._on_revert_timeout)

        # Periodic background poll timer (60 seconds)
        self._poll_timer = QTimer(self)
        self._poll_timer.setInterval(60000)
        self._poll_timer.timeout.connect(self._on_poll_timer)

        self.set_idle()

    def start_poll_timer(self) -> None:
        if not self._poll_timer.isActive():
            self._poll_timer.start()

    def stop_poll_timer(self) -> None:
        if self._poll_timer.isActive():
            self._poll_timer.stop()

    def _set_dot_color(self, color: str) -> None:
        self._dot.setStyleSheet(
            f"min-width: 8px; max-width: 8px; min-height: 8px; max-height: 8px; "
            f"border-radius: 4px; background-color: {color};"
        )

    def set_unconfigured(self) -> None:
        if _is_deleted(self):
            return
        self._state = "unconfigured"
        self._creates_count = 0
        self._updates_count = 0
        self._set_dot_color(COLOR_UNCONFIGURED)
        self._label.setText("No vault")
        self._label.show()
        self.setToolTip("VaultSync: No vault configured\nClick to open Settings.")

    def set_idle(self) -> None:
        """Everything is up to date: pure minimalist mode with indicator dot only."""
        if _is_deleted(self):
            return
        self._state = "idle"
        self._creates_count = 0
        self._updates_count = 0
        self._set_dot_color(COLOR_IDLE)
        # Smart-compact: empty text when up to date
        self._label.setText("")
        self._label.hide()

        tip = "VaultSync: Up to date\n- No pending changes"
        if self._last_synced_time_str:
            tip += f"\n- Last sync: {self._last_synced_time_str}"
        tip += "\n\nClick to open Dashboard, right-click for options."
        self.setToolTip(tip)

    def set_pending(self, creates: int, updates: int) -> None:
        """Cards available in the vault to synchronize."""
        if _is_deleted(self):
            return
        self._state = "pending"
        self._creates_count = creates
        self._updates_count = updates
        total = creates + updates

        if total <= 0:
            self.set_idle()
            return

        self._set_dot_color(COLOR_PENDING)
        self._label.setText(f"{total} pending")
        self._label.show()

        tip = (
            f"VaultSync: {total} card(s) available to sync\n"
            f"- {creates} new card(s)\n"
            f"- {updates} modified note(s)\n\n"
            f"Click to open Dashboard, right-click for quick sync."
        )
        self.setToolTip(tip)

    def set_syncing(self) -> None:
        if _is_deleted(self):
            return
        self._revert_timer.stop()
        self._state = "syncing"
        self._set_dot_color(COLOR_SYNCING)
        self._label.setText("Syncing...")
        self._label.show()
        self.setToolTip("VaultSync: Synchronization in progress...")

    def set_success(self, report: SyncReport | None = None) -> None:
        if _is_deleted(self):
            return
        self._state = "success"
        self._set_dot_color(COLOR_SUCCESS)

        now_str = datetime.datetime.now().strftime("%H:%M")
        self._last_synced_time_str = now_str

        count = (report.created_count + report.updated_count) if report else 0
        if count > 0:
            self._label.setText(f"{count} synced")
            tip = (
                f"VaultSync: Sync completed successfully at {now_str}\n"
                f"- {report.created_count} added, {report.updated_count} updated"
            )
        else:
            self._label.setText("Up to date")
            tip = f"VaultSync: Sync completed successfully at {now_str}\n- Everything up to date"

        self._label.show()
        self.setToolTip(tip)

        # Revert to idle after 4 seconds
        self._revert_timer.start(4000)

    def set_error(self, message: str) -> None:
        if _is_deleted(self):
            return
        self._revert_timer.stop()
        self._state = "error"
        self._set_dot_color(COLOR_ERROR)
        self._label.setText("Sync error")
        self._label.show()
        clean_msg = str(message).strip() or "Unknown error"
        self.setToolTip(f"VaultSync: Sync error\n- {clean_msg}\n\nClick to open Dashboard.")

    def _on_revert_timeout(self) -> None:
        if _is_deleted(self):
            return
        if self._state == "success":
            self.set_idle()

    def _on_poll_timer(self) -> None:
        if _is_deleted(self) or not self.isVisible():
            return
        if self._state == "syncing" or is_sync_running():
            return
        check_pending_cards(fast_check=True)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            from .menu import open_dashboard, open_settings
            if self._state == "unconfigured":
                open_settings()
            else:
                open_dashboard()
            event.accept()
            return
        elif event.button() == Qt.MouseButton.RightButton:
            pos = event.globalPosition().toPoint() if hasattr(event, "globalPosition") else event.globalPos()
            self._show_context_menu(pos)
            event.accept()
            return
        super().mousePressEvent(event)

    def _show_context_menu(self, pos: QPoint) -> None:
        from .menu import open_dashboard, open_settings, quick_sync_new, sync_and_update_all

        menu = QMenu(self)

        act_dash = QAction("Open Dashboard...", menu)
        act_dash.triggered.connect(open_dashboard)
        menu.addAction(act_dash)

        if self._state == "pending":
            total = self._creates_count + self._updates_count
            act_quick = QAction(f"Quick Sync Now ({total} cards)", menu)
            act_quick.triggered.connect(quick_sync_new)
            menu.addAction(act_quick)
        else:
            act_quick = QAction("Quick Sync (New Cards)", menu)
            act_quick.triggered.connect(quick_sync_new)
            menu.addAction(act_quick)

        act_full = QAction("Sync and Update (Modified Cards)", menu)
        act_full.triggered.connect(sync_and_update_all)
        menu.addAction(act_full)

        menu.addSeparator()

        act_check = QAction("Check for Changes", menu)
        act_check.triggered.connect(lambda: check_pending_cards(fast_check=False))
        menu.addAction(act_check)

        act_settings = QAction("Settings...", menu)
        act_settings.triggered.connect(open_settings)
        menu.addAction(act_settings)

        menu.addSeparator()

        act_hide = QAction("Hide Status Bar Indicator", menu)
        act_hide.triggered.connect(self._hide_and_save_setting)
        menu.addAction(act_hide)

        menu.exec(pos)

    def _hide_and_save_setting(self) -> None:
        live_mw = _get_mw()
        profile_name = live_mw.pm.name if live_mw and hasattr(live_mw, "pm") and live_mw.pm else "Default"
        settings = _manager.load(profile_name)
        settings.show_status_bar = False
        _manager.save(settings)
        self.setVisible(False)
        self.stop_poll_timer()
        logger.log_info("Status bar indicator disabled via context menu.")


def init_status_bar(live_mw: Any) -> VaultSyncStatusBarWidget | None:
    """Initialize and attach the permanent status bar widget to Anki's main status bar."""
    global _status_bar_instance

    if live_mw is None or not hasattr(live_mw, "statusBar") or live_mw.statusBar() is None:
        return None

    status_bar = live_mw.statusBar()

    if _status_bar_instance is not None and not _is_deleted(_status_bar_instance):
        return _status_bar_instance

    _status_bar_instance = VaultSyncStatusBarWidget()
    status_bar.addPermanentWidget(_status_bar_instance)

    profile_name = live_mw.pm.name if hasattr(live_mw, "pm") and live_mw.pm else "Default"
    settings = _manager.load(profile_name)
    _status_bar_instance.setVisible(settings.show_status_bar)
    if settings.show_status_bar:
        _status_bar_instance.start_poll_timer()

    return _status_bar_instance


def get_status_bar() -> VaultSyncStatusBarWidget | None:
    global _status_bar_instance
    if _status_bar_instance is not None and not _is_deleted(_status_bar_instance):
        return _status_bar_instance
    return None


def update_status_bar_visibility(visible: bool) -> None:
    """Show or hide the status bar widget dynamically when settings are changed."""
    widget = get_status_bar()
    if widget is not None:
        widget.setVisible(visible)
        if visible:
            widget.start_poll_timer()
            check_pending_cards(fast_check=True)
        else:
            widget.stop_poll_timer()


def notify_sync_started() -> None:
    widget = get_status_bar()
    if widget is not None:
        widget.set_syncing()


def notify_sync_finished(report: SyncReport | None = None, error: Exception | str | None = None) -> None:
    widget = get_status_bar()
    if widget is not None:
        if error is not None:
            widget.set_error(str(error))
        else:
            widget.set_success(report)


def notify_scan_result(plan: SyncPlan) -> None:
    widget = get_status_bar()
    if widget is not None:
        if plan is not None:
            widget.set_pending(plan.total_creates, plan.total_updates)
        else:
            widget.set_idle()


def check_pending_cards(fast_check: bool = True) -> None:
    """Check whether cards are available in the vault and update the status bar."""
    widget = get_status_bar()
    if widget is None or not widget.isVisible():
        return

    if is_sync_running():
        return

    live_mw = _get_mw()
    profile_name = live_mw.pm.name if live_mw and hasattr(live_mw, "pm") and live_mw.pm else "Default"
    settings = _manager.load(profile_name)

    if not settings.vault_path or not settings.vault_path.is_dir():
        widget.set_unconfigured()
        return

    # Fast check: If mtime has not changed since last scan and widget is idle, skip heavy scan
    current_mtime = _get_vault_latest_mtime(settings.vault_path)
    if fast_check and widget._state == "idle" and widget._last_checked_mtime > 0 and current_mtime <= widget._last_checked_mtime:
        return

    widget._last_checked_mtime = current_mtime

    vault_p = settings.vault_path
    b_mid = settings.basic_model_id
    c_mid = settings.cloze_model_id

    from ..worker import build_sync_plan

    def _scan_op(col) -> tuple[int, int]:
        plan = build_sync_plan(
            col,
            vault_p,
            basic_model_id=b_mid,
            cloze_model_id=c_mid,
        )
        return plan.total_creates, plan.total_updates

    def _on_success(counts: tuple[int, int]) -> None:
        if _is_deleted(widget):
            return
        creates, updates = counts
        if creates + updates > 0:
            widget.set_pending(creates, updates)
        else:
            widget.set_idle()

    def _on_failure(exc: Exception) -> None:
        logger.log_warning(f"Status bar background check failed: {exc}")

    # Headless / unit testing fallback
    if getattr(aqt, "mw", None) is None:
        try:
            col = getattr(mw, "col", None)
            res = _scan_op(col)
            _on_success(res)
        except Exception as exc:
            _on_failure(exc)
        return

    QueryOp(
        parent=live_mw,
        op=_scan_op,
        success=_on_success,
    ).failure(_on_failure).run_in_background()


def on_status_bar_profile_loaded(settings: AddonProfileSettings) -> None:
    """Invoked when a profile opens in Anki."""
    widget = get_status_bar()
    if widget is not None:
        widget.setVisible(settings.show_status_bar)
        if settings.show_status_bar:
            widget.start_poll_timer()
            check_pending_cards(fast_check=False)
        else:
            widget.stop_poll_timer()


def on_status_bar_profile_closed() -> None:
    """Invoked when a profile closes in Anki."""
    widget = get_status_bar()
    if widget is not None:
        widget.stop_poll_timer()
        widget.set_idle()
