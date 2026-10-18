from __future__ import annotations

import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from aqt import dialogs, mw
from aqt.operations import QueryOp
from aqt.qt import (
    QDialog,
    QEvent,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    Qt,
    QVBoxLayout,
    QWidget,
    sip,
)
from typing import Callable


def _is_deleted(widget: Any) -> bool:
    if sip is not None and hasattr(sip, "isdeleted"):
        try:
            return sip.isdeleted(widget)
        except Exception:
            return False
    return False
from aqt.utils import tooltip

from .. import logger
from ..contracts import SyncPlan, SyncReport
from ..profile_config import ProfileConfigManager
from ..worker import build_sync_plan, execute_sync_plan
from .config_window import ConfigWindow
from .diff_dialog import DiffDialog
from .sync_report_dialog import SyncIssuesDialog
from .notification import show_sync_notification


class ErrorBannerWidget(QFrame):
    """Inline non-blocking error banner for MainWindow."""

    def __init__(self, parent: QWidget | None = None, on_open_logs: Callable[[], None] | None = None) -> None:
        super().__init__(parent)
        self.setVisible(False)
        self._on_open_logs = on_open_logs
        self._retry_callback: Callable[[], None] | None = None

        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 8, 12, 8)
        layout.setSpacing(10)

        self.icon_lbl = QLabel("⚠️")
        self.icon_lbl.setStyleSheet("font-size: 14px;")
        layout.addWidget(self.icon_lbl)

        self.msg_lbl = QLabel()
        self.msg_lbl.setWordWrap(True)
        self.msg_lbl.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        layout.addWidget(self.msg_lbl)

        if self._on_open_logs is not None:
            self.view_log_btn = QPushButton("View Log")
            self.view_log_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            self.view_log_btn.clicked.connect(self._on_open_logs)
            layout.addWidget(self.view_log_btn)

        self.retry_btn = QPushButton("Retry")
        self.retry_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.retry_btn.clicked.connect(self._on_retry_clicked)
        layout.addWidget(self.retry_btn)

        self.close_btn = QPushButton("✕")
        self.close_btn.setObjectName("bannerCloseBtn")
        self.close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.close_btn.clicked.connect(self.hide)
        layout.addWidget(self.close_btn)

        self._apply_theme()

    def _apply_theme(self) -> None:
        is_dark = False
        try:
            from aqt.theme import theme_manager
            is_dark = bool(getattr(theme_manager, "night_mode", False))
        except Exception:
            pass

        bg = "rgba(244, 67, 54, 0.18)" if is_dark else "rgba(244, 67, 54, 0.10)"
        border = "#EF5350" if is_dark else "#D32F2F"
        text = "#FFCDD2" if is_dark else "#B71C1C"

        self.setStyleSheet(f"""
            ErrorBannerWidget {{
                background-color: {bg};
                border: 1px solid {border};
                border-radius: 6px;
            }}
            QLabel {{
                color: {text};
                font-size: 12px;
            }}
            QPushButton {{
                font-size: 11px;
                padding: 3px 10px;
                border-radius: 4px;
            }}
            QPushButton#bannerCloseBtn {{
                background: transparent;
                border: none;
                font-size: 12px;
                color: {text};
            }}
        """)

    def show_error(self, message: str, retry_action: Callable[[], None] | None = None) -> None:
        self.msg_lbl.setText(message)
        self._retry_callback = retry_action
        self.retry_btn.setVisible(retry_action is not None)
        self._apply_theme()
        self.show()

    def _on_retry_clicked(self) -> None:
        self.hide()
        if self._retry_callback is not None:
            self._retry_callback()


class MainWindow(QDialog):
    """Main VaultSync Dashboard."""

    def __init__(self, profile_manager: ProfileConfigManager, profile_name: str, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("VaultSync Dashboard")
        self.resize(760, 330)

        self._manager = profile_manager
        self._profile_name = profile_name
        self._settings = self._manager.load(profile_name)

        self._current_plan: SyncPlan | None = None
        self._last_report: SyncReport | None = None
        self._is_busy = False
        self._config_dlg: ConfigWindow | None = None

        self._init_ui()
        self._refresh_header()

        # Trigger automatic initial scan if vault path is configured
        if self._settings.vault_path and self._settings.vault_path.is_dir():
            self._start_scan()
        else:
            self._set_status("Vault not configured. Click 'Settings' to select your vault.", state="warning")
            self._log("Welcome to VaultSync! Click 'Settings' above to select your Obsidian vault.")

    def _init_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setSpacing(14)
        layout.setContentsMargins(18, 18, 18, 18)

        # 1. Header: Vault Path + "Settings" button on the right
        header_top = QHBoxLayout()
        self.vault_label = QLabel("<b>Vault:</b> (Not Configured)")
        self.vault_label.setStyleSheet("font-size: 13px;")

        self.settings_btn = QPushButton("Settings")
        self.settings_btn.setFixedHeight(36)
        self.settings_btn.setAutoDefault(False)
        self.settings_btn.setDefault(False)
        self.settings_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.settings_btn.setStyleSheet("""
            QPushButton {
                font-size: 13px;
                border-radius: 6px;
                padding: 6px 14px;
            }
        """)
        self.settings_btn.clicked.connect(self._open_settings)
        self.change_vault_btn = self.settings_btn  # alias for backwards compatibility

        header_top.addWidget(self.vault_label)
        header_top.addStretch()
        header_top.addWidget(self.settings_btn)
        layout.addLayout(header_top)

        # 2. Two-column section: Status / Card counts on the left, Actions on the right
        middle_layout = QHBoxLayout()
        middle_layout.setSpacing(14)

        box_style = """
            QFrame {
                border: 1px solid rgba(130, 130, 130, 0.25);
                border-radius: 8px;
                background-color: rgba(130, 130, 130, 0.04);
            }
        """

        # Left Column: Card Counts
        status_box = QFrame()
        status_box.setStyleSheet(box_style)
        status_layout = QVBoxLayout(status_box)
        status_layout.setContentsMargins(16, 14, 16, 14)
        status_layout.setSpacing(10)

        self.title_status = QLabel("Card Counts")
        self.title_status.setStyleSheet("font-size: 15px; font-weight: bold; border: none; background: transparent;")
        status_layout.addWidget(self.title_status)

        status_grid = QGridLayout()
        status_grid.setContentsMargins(0, 0, 0, 0)
        status_grid.setVerticalSpacing(9)
        status_grid.setHorizontalSpacing(20)

        for r in range(4):
            status_grid.setRowMinimumHeight(r, 25)

        lbl_new = QLabel("New cards:")
        lbl_new.setStyleSheet("font-size: 13px; font-weight: normal; border: none; background: transparent;")
        lbl_new.setToolTip("Cards found in Obsidian notes that are not yet in Anki (including new Image Clozes).")
        self.new_val_label = QLabel("0")
        self.new_val_label.setStyleSheet("font-size: 13px; font-weight: bold; border: none; background: transparent;")
        self.new_cards_label = self.new_val_label
        status_grid.addWidget(lbl_new, 0, 0)
        status_grid.addWidget(self.new_val_label, 0, 1, Qt.AlignmentFlag.AlignRight)

        lbl_mod = QLabel("Modified cards:")
        lbl_mod.setStyleSheet("font-size: 13px; font-weight: normal; border: none; background: transparent;")
        lbl_mod.setToolTip("Cards where note text, tags, or deck assignment changed in Obsidian.")
        self.modified_val_label = QLabel("0")
        self.modified_val_label.setStyleSheet("font-size: 13px; font-weight: bold; border: none; background: transparent;")
        self.modified_cards_label = self.modified_val_label
        status_grid.addWidget(lbl_mod, 1, 0)
        status_grid.addWidget(self.modified_val_label, 1, 1, Qt.AlignmentFlag.AlignRight)

        lbl_occ = QLabel("Unprocessed occlusions:")
        lbl_occ.setStyleSheet("font-size: 13px; font-weight: normal; border: none; background: transparent;")
        lbl_occ.setToolTip("Cards in Anki awaiting masks (tag:TODO_PROCESS_OCCLUSION).\nClick count to view in Anki Browser.")
        self.occlusions_val_label = QLabel("0")
        self.occlusions_val_label.setStyleSheet("font-size: 13px; font-weight: bold; border: none; background: transparent;")
        self.occlusions_val_label.setCursor(Qt.CursorShape.PointingHandCursor)
        self.occlusions_val_label.mousePressEvent = lambda event: self._view_unmasked_occlusions()
        self.occlusions_label = self.occlusions_val_label
        status_grid.addWidget(lbl_occ, 2, 0)
        status_grid.addWidget(self.occlusions_val_label, 2, 1, Qt.AlignmentFlag.AlignRight)

        lbl_orph = QLabel("Orphaned cards:")
        lbl_orph.setStyleSheet("font-size: 13px; font-weight: normal; border: none; background: transparent;")
        lbl_orph.setToolTip("Cards in Anki whose source note was deleted in Obsidian.\nCan also be viewed in Anki Browser via Settings.")
        self.orphans_val_label = QLabel("0")
        self.orphans_val_label.setStyleSheet("font-size: 13px; font-weight: bold; border: none; background: transparent;")
        self.orphans_val_label.setToolTip("Click to view orphaned cards in Anki Browser")
        self.orphans_val_label.setCursor(Qt.CursorShape.PointingHandCursor)
        self.orphans_val_label.mousePressEvent = lambda event: self._view_orphans()
        self.orphans_label = self.orphans_val_label
        status_grid.addWidget(lbl_orph, 3, 0)
        status_grid.addWidget(self.orphans_val_label, 3, 1, Qt.AlignmentFlag.AlignRight)

        status_grid.setColumnStretch(0, 1)
        status_grid.setColumnStretch(1, 0)

        status_layout.addLayout(status_grid)
        status_layout.addStretch()
        middle_layout.addWidget(status_box, stretch=1)

        # Right Column: Actions
        actions_box = QFrame()
        actions_box.setStyleSheet(box_style)
        actions_layout = QVBoxLayout(actions_box)
        actions_layout.setContentsMargins(16, 14, 16, 14)
        actions_layout.setSpacing(10)

        self.title_actions = QLabel("Actions")
        self.title_actions.setStyleSheet("font-size: 15px; font-weight: bold; border: none; background: transparent;")
        actions_layout.addWidget(self.title_actions)

        action_btn_style = """
            QPushButton {
                font-size: 13px;
                font-weight: normal;
                border-radius: 6px;
                padding: 6px 14px;
            }
        """

        self.sync_btn = QPushButton("Sync")
        self.sync_btn.setFixedHeight(36)
        self.sync_btn.setAutoDefault(False)
        self.sync_btn.setDefault(False)
        self.sync_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.sync_btn.setStyleSheet(action_btn_style)
        self.sync_btn.setEnabled(False)
        self.sync_btn.clicked.connect(lambda: self._start_sync(include_updates=True))

        self.diff_btn = QPushButton("Preview Diff")
        self.diff_btn.setFixedHeight(36)
        self.diff_btn.setAutoDefault(False)
        self.diff_btn.setDefault(False)
        self.diff_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.diff_btn.setStyleSheet(action_btn_style)
        self.diff_btn.setEnabled(False)
        self.diff_btn.clicked.connect(self._open_diff)

        self.rescan_btn = QPushButton("Rescan Vault")
        self.rescan_btn.setFixedHeight(36)
        self.rescan_btn.setAutoDefault(False)
        self.rescan_btn.setDefault(False)
        self.rescan_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.rescan_btn.setStyleSheet(action_btn_style)
        self.rescan_btn.clicked.connect(self._start_scan)

        actions_layout.addWidget(self.sync_btn)
        actions_layout.addWidget(self.diff_btn)
        actions_layout.addWidget(self.rescan_btn)
        actions_layout.addStretch()

        middle_layout.addWidget(actions_box, stretch=1)
        layout.addLayout(middle_layout)

        # Error Banner
        self.error_banner = ErrorBannerWidget(self, on_open_logs=lambda: self._open_settings(tab_index="Logs"))
        layout.addWidget(self.error_banner)

        # 3. Status Bar
        self.status_box = QFrame()
        self.status_box.setObjectName("statusBox")
        self.status_box.setStyleSheet("""
            #statusBox {
                border: 1px solid rgba(130, 130, 130, 0.25);
                border-radius: 8px;
                background-color: rgba(130, 130, 130, 0.04);
            }
        """)
        status_bar_layout = QHBoxLayout(self.status_box)
        status_bar_layout.setContentsMargins(14, 8, 14, 8)
        status_bar_layout.setSpacing(8)

        self.status_dot = QLabel("●")
        self.status_dot.setStyleSheet("font-size: 13px; color: #4CAF50;")
        self.status_label = QLabel("Ready")
        self.status_label.setStyleSheet("font-size: 13px;")

        status_bar_layout.addWidget(self.status_dot)
        status_bar_layout.addWidget(self.status_label)
        status_bar_layout.addStretch()

        self.status_report_btn = QPushButton("View Issues")
        self.status_report_btn.setFixedHeight(26)
        self.status_report_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.status_report_btn.setStyleSheet("""
            QPushButton {
                font-size: 12px;
                padding: 2px 10px;
                border-radius: 4px;
            }
        """)
        self.status_report_btn.setVisible(False)
        self.status_report_btn.clicked.connect(self._open_last_sync_report)
        status_bar_layout.addWidget(self.status_report_btn)

        layout.addWidget(self.status_box)

        # Ensure no button is selected or focused by default
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setFocus()

        from aqt.qt import QShortcut, QKeySequence
        self._undo_shortcut = QShortcut(QKeySequence.StandardKey.Undo, self)
        self._undo_shortcut.activated.connect(self._on_undo_triggered)
        self._redo_shortcut = QShortcut(QKeySequence.StandardKey.Redo, self)
        self._redo_shortcut.activated.connect(self._on_redo_triggered)

    def _on_undo_triggered(self) -> None:
        if self._is_busy:
            return
        import aqt
        if getattr(aqt, "mw", None) is not None and hasattr(aqt.mw, "undo"):
            aqt.mw.undo()
            if not _is_deleted(self):
                self._start_scan()

    def _on_redo_triggered(self) -> None:
        if self._is_busy:
            return
        import aqt
        if getattr(aqt, "mw", None) is not None and hasattr(aqt.mw, "redo"):
            aqt.mw.redo()
            if not _is_deleted(self):
                self._start_scan()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self.setFocus()

    def changeEvent(self, event: QEvent) -> None:
        if event.type() == QEvent.Type.ActivationChange and self.isActiveWindow():
            self._check_and_refresh_if_stale()
        super().changeEvent(event)

    def _check_and_refresh_if_stale(self) -> None:
        if self._is_busy or self._current_plan is None:
            return
        for fp in self._current_plan.file_plans:
            try:
                if fp.file_path.is_file() and fp.file_path.stat().st_mtime_ns != fp.mtime_ns:
                    self._log("Detected external changes in Obsidian while inactive. Refreshing scan...")
                    self._start_scan()
                    break
            except OSError:
                pass

    def _set_status(self, text: str, state: str = "normal", show_issues: bool = False) -> None:
        colors = {
            "normal": "#4CAF50",
            "ready": "#4CAF50",
            "busy": "#2196F3",
            "warning": "#FF9800",
            "error": "#F44336",
        }
        color = colors.get(state, "#4CAF50")
        self.status_dot.setStyleSheet(f"font-size: 13px; color: {color};")
        self.status_label.setText(text)
        self.status_report_btn.setVisible(show_issues)

    def _log(self, text: str, level: str = "INFO") -> None:
        logger.log(text, level=level)

    def _refresh_header(self) -> None:
        self._settings = self._manager.load(self._profile_name)
        if self._settings.vault_path:
            self.vault_label.setText(f"<b>Vault:</b> {self._settings.vault_path}")
        else:
            self.vault_label.setText("<b>Vault:</b> (Not Configured)")

    def _set_buttons_busy(self, busy: bool) -> None:
        self._is_busy = busy
        self.settings_btn.setEnabled(not busy)
        self.rescan_btn.setEnabled(not busy)
        self.diff_btn.setEnabled(not busy and (self._current_plan is not None and self._current_plan.total_updates > 0))
        self.sync_btn.setEnabled(
            not busy
            and self._current_plan is not None
            and (
                self._current_plan.total_creates > 0
                or self._current_plan.total_updates > 0
                or len(self._current_plan.occlusions_to_stage) > 0
            )
        )

    def _on_op_failure(self, exc: Exception, retry_action: Callable[[], None] | None = None) -> None:
        if _is_deleted(self):
            return
        self._set_buttons_busy(False)
        self._set_status(f"Operation failed: {exc}", state="error")
        self._log(f"ERROR: {exc}", level="ERROR")
        self.error_banner.show_error(f"An error occurred: {exc}", retry_action=retry_action)

    def _update_action_button_counts(self) -> None:
        if self._current_plan is None:
            return
        total_new = self._current_plan.total_creates + len(self._current_plan.occlusions_to_stage)
        approved_updates = self._current_plan.approved_updates
        total_sync_count = total_new + approved_updates
        self.sync_btn.setText(f"Sync ({total_sync_count})")
        self.diff_btn.setText(f"Preview Diff ({approved_updates})")

    def _start_scan(self) -> None:
        if self._is_busy:
            return
        if not self._settings.vault_path or not self._settings.vault_path.is_dir():
            self._set_status("Vault directory not configured", state="warning")
            QMessageBox.information(
                self, "Vault Not Configured", "Please select your Obsidian vault folder in Settings."
            )
            self._open_settings()
            return

        from ..sync_lock import acquire_sync_lock, release_sync_lock
        if not acquire_sync_lock():
            self._set_status("Operation in progress. Please wait...", state="warning")
            return

        self.error_banner.hide()
        self._set_buttons_busy(True)
        self._set_status("Scanning vault...", state="busy")
        self._log(f"Scanning vault: {self._settings.vault_path}...")

        vault_p = self._settings.vault_path
        b_mid = self._settings.basic_model_id
        c_mid = self._settings.cloze_model_id

        def _scan_op(col) -> tuple[SyncPlan, int]:
            plan = build_sync_plan(col, vault_p, basic_model_id=b_mid, cloze_model_id=c_mid)
            unmasked = 0
            try:
                unmasked = len(col.find_notes("tag:TODO_PROCESS_OCCLUSION"))
            except Exception:
                unmasked = 0
            return plan, unmasked

        def _on_success(res: Any) -> None:
            release_sync_lock()
            if _is_deleted(self):
                return
            if isinstance(res, tuple):
                plan, unmasked = res
            else:
                plan, unmasked = res, 0
            self._on_scan_success(plan, unmasked)

        def _on_failure(exc: Exception) -> None:
            release_sync_lock()
            if _is_deleted(self):
                return
            self._on_op_failure(exc, retry_action=self._start_scan)

        import aqt
        if getattr(aqt, "mw", None) is None:
            try:
                res = _scan_op(getattr(mw, "col", None))
                _on_success(res)
            except Exception as exc:
                _on_failure(exc)
            return

        QueryOp(
            parent=self,
            op=_scan_op,
            success=_on_success,
        ).failure(_on_failure).with_progress("Scanning vault...").run_in_background()

    def _on_scan_success(self, plan: SyncPlan, unmasked_in_anki: int = 0) -> None:
        if _is_deleted(self):
            return
        self._current_plan = plan
        self._set_buttons_busy(False)

        total_new = plan.total_creates + len(plan.occlusions_to_stage)
        self.new_val_label.setText(str(total_new))
        self.modified_val_label.setText(str(plan.total_updates))

        self.occlusions_val_label.setText(str(unmasked_in_anki))
        if unmasked_in_anki > 0:
            self.occlusions_val_label.setToolTip(
                f"{unmasked_in_anki} card(s) awaiting masks in Anki (tag:TODO_PROCESS_OCCLUSION).\n"
                "Click to view in Anki Browser."
            )
        else:
            self.occlusions_val_label.setToolTip("No unprocessed image occlusions in Anki.")

        self.orphans_val_label.setText(str(len(plan.orphans)))

        self._log(
            f"Vault scan complete: {plan.total_files} file(s), "
            f"{total_new} new card(s), {plan.total_updates} modified, "
            f"{plan.total_unchanged} unchanged."
        )
        if plan.orphans:
            self._log(f"Detected {len(plan.orphans)} orphaned card(s) in Anki no longer found in vault.")

        self._update_action_button_counts()

        total_sync_count = total_new + plan.approved_updates
        issues_count = len(plan.diagnostics)
        skipped_cards = plan.skipped_cards_count

        if issues_count > 0:
            status_text = f"Ready · ⚠️ {issues_count} issue(s) detected"
            if skipped_cards > 0:
                status_text += f" ({skipped_cards} card(s) will be skipped)"
            self._set_status(status_text, state="warning", show_issues=True)
            self.status_report_btn.setText("Review Issues")
        elif total_sync_count > 0:
            self._set_status(f"Scan complete: {total_sync_count} item(s) ready to sync", state="ready", show_issues=False)
        else:
            self._set_status("Ready (Vault is up to date)", state="ready", show_issues=False)

    def _open_diff(self) -> None:
        if self._current_plan is None or self._current_plan.total_updates == 0:
            return
        dlg = DiffDialog(self._current_plan, self)
        res = dlg.exec()
        if _is_deleted(self):
            return
        if getattr(dlg, "rescan_requested", False):
            self._start_scan()
            return
        if res:
            self._update_action_button_counts()

    def _start_sync(self, include_updates: bool) -> None:
        if self._is_busy or self._current_plan is None:
            return
        self._last_sync_included_updates = include_updates

        if include_updates and self._settings.show_diff_before_update and self._current_plan.total_updates > 0:
            dlg = DiffDialog(self._current_plan, self)
            res = dlg.exec()
            if _is_deleted(self):
                return
            if not res:
                if getattr(dlg, "rescan_requested", False):
                    self._start_scan()
                    return
                self._log("Sync cancelled by user in diff preview.")
                return
            self._update_action_button_counts()

        from ..sync_lock import acquire_sync_lock, release_sync_lock
        if not acquire_sync_lock():
            self._set_status("Operation in progress. Please wait...", state="warning")
            return

        self.error_banner.hide()
        self._set_buttons_busy(True)
        self._set_status("Syncing cards to Anki...", state="busy")
        self._log("Starting Sync...")

        plan = self._current_plan
        occ_dir = self._settings.occlusion_dir
        b_mid = self._settings.basic_model_id
        c_mid = self._settings.cloze_model_id

        def _sync_op(col) -> SyncReport:
            if getattr(self._settings, "backup_before_sync", True) and plan.has_changes:
                from ..backup import create_safety_backup
                create_safety_backup(live_mw=mw, col=col, reason="sync")
            return execute_sync_plan(
                col,
                plan,
                include_updates=include_updates,
                occlusion_dir=occ_dir,
                basic_model_id=b_mid,
                cloze_model_id=c_mid,
            )

        def _on_success(report: SyncReport) -> None:
            release_sync_lock()
            if _is_deleted(self):
                return
            self._on_sync_success(report)

        def _on_failure(exc: Exception) -> None:
            release_sync_lock()
            if _is_deleted(self):
                return
            self._on_op_failure(exc, retry_action=lambda: self._start_sync(include_updates=include_updates))

        import aqt
        if getattr(aqt, "mw", None) is None:
            try:
                res = _sync_op(getattr(mw, "col", None))
                _on_success(res)
            except Exception as exc:
                _on_failure(exc)
            return

        QueryOp(
            parent=self,
            op=_sync_op,
            success=_on_success,
        ).failure(_on_failure).with_progress("Syncing cards to Anki...").run_in_background()

    def _on_sync_success(self, report: SyncReport) -> None:
        if _is_deleted(self):
            return
        self._set_buttons_busy(False)
        self._last_report = report

        if mw is not None:
            if hasattr(mw, "deckBrowser"):
                mw.deckBrowser.refresh()
            if hasattr(mw, "update_undo_actions"):
                mw.update_undo_actions()
            if hasattr(mw, "reset"):
                mw.reset()

        self._log(
            f"Sync finished: {report.created_count} added, {report.updated_count} updated, "
            f"{report.deck_moved_count} moved, {report.media_added_count} media file(s)."
        )

        for f_path, reason in report.skipped_files:
            self._log(f"WARNING: Skipped {f_path.name}: {reason}", level="WARNING")
        for warn in report.warnings:
            self._log(f"WARNING: {warn}", level="WARNING")

        show_sync_notification(report, parent=mw or self, include_updates=getattr(self, "_last_sync_included_updates", True))

        has_issues = bool(report.skipped_files or report.warnings)
        if has_issues:
            issues_count = len(report.skipped_files) + len(report.warnings)
            self._set_status(
                f"Sync completed with {issues_count} issue(s) ({report.created_count} added, {report.updated_count} updated)",
                state="warning",
                show_issues=True,
            )
            dlg = SyncIssuesDialog(
                report,
                parent=self,
                on_open_logs=lambda: self._open_settings(tab_index="Logs"),
                on_rescan=self._start_scan,
            )
            dlg.exec()
            if _is_deleted(self):
                return
        else:
            self._set_status(
                f"Sync complete: {report.created_count} added, {report.updated_count} updated",
                state="ready",
                show_issues=False,
            )
            if self._settings.auto_close_on_success:
                self.accept()
                return

        if _is_deleted(self):
            return
        # Re-scan to refresh counters
        self._start_scan()

    def _open_last_sync_report(self) -> None:
        if self._last_report is not None:
            dlg = SyncIssuesDialog(
                report=self._last_report,
                parent=self,
                on_open_logs=lambda: self._open_settings(tab_index="Logs"),
                on_rescan=self._start_scan,
                vault_path=self._settings.vault_path,
            )
            dlg.exec()
        elif self._current_plan is not None and self._current_plan.diagnostics:
            dlg = SyncIssuesDialog(
                report=None,
                parent=self,
                on_open_logs=lambda: self._open_settings(tab_index="Logs"),
                on_rescan=self._start_scan,
                diagnostics=self._current_plan.diagnostics,
                vault_path=self._current_plan.vault_path,
            )
            dlg.exec()

    def _view_unmasked_occlusions(self) -> None:
        if mw is not None and getattr(mw, "col", None) is not None:
            query = "tag:TODO_PROCESS_OCCLUSION"
            browser = dialogs.open("Browser", mw)
            browser.search_for(query)

    def _view_orphans(self) -> None:
        self._open_settings(tab_index=ConfigWindow.TAB_MAINTENANCE)

    def _open_settings(self, tab_index: int | str | None = None) -> None:
        def _on_accepted() -> None:
            if _is_deleted(self):
                return
            self._settings = self._manager.load(self._profile_name)
            self._refresh_header()
            if self._settings.vault_path and self._settings.vault_path.is_dir():
                self._start_scan()

        from .menu import open_settings
        self._config_dlg = open_settings(
            tab_index=tab_index,
            sync_plan=self._current_plan,
            on_accepted=_on_accepted,
        )
