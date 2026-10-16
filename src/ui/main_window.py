from __future__ import annotations

import datetime
from pathlib import Path
from typing import TYPE_CHECKING

from aqt import dialogs, mw
from aqt.operations import QueryOp
from aqt.qt import (
    QDialog,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    Qt,
    QVBoxLayout,
)
from aqt.utils import tooltip

from ..contracts import SyncPlan, SyncReport
from ..profile_config import ProfileConfigManager
from ..worker import build_sync_plan, execute_sync_plan
from .config_window import ConfigWindow
from .diff_dialog import DiffDialog


class MainWindow(QDialog):
    """Main VaultSync Dashboard."""

    def __init__(self, profile_manager: ProfileConfigManager, profile_name: str, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("VaultSync Dashboard")
        self.resize(780, 520)

        self._manager = profile_manager
        self._profile_name = profile_name
        self._settings = self._manager.load(profile_name)

        self._current_plan: SyncPlan | None = None
        self._is_busy = False

        self._init_ui()
        self._refresh_header()

        # Trigger automatic initial scan if vault path is configured
        if self._settings.vault_path and self._settings.vault_path.is_dir():
            self._start_scan()
        else:
            self._log("Welcome to VaultSync! Click 'Change Vault Settings' below to select your Obsidian vault.")

    def _init_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setSpacing(14)
        layout.setContentsMargins(18, 18, 18, 18)

        # 1. Header: Vault Path + "Change Vault Settings" button on the right
        header_top = QHBoxLayout()
        self.vault_label = QLabel("<b>Vault:</b> (Not Configured)")
        self.vault_label.setStyleSheet("font-size: 13px;")

        self.change_vault_btn = QPushButton("Change Vault Settings")
        self.change_vault_btn.setFixedHeight(36)
        self.change_vault_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.change_vault_btn.setStyleSheet("""
            QPushButton {
                font-size: 13px;
                border-radius: 6px;
                padding: 6px 14px;
            }
        """)
        self.change_vault_btn.clicked.connect(self._open_settings)

        header_top.addWidget(self.vault_label)
        header_top.addStretch()
        header_top.addWidget(self.change_vault_btn)
        layout.addLayout(header_top)

        # 2. Two-column section: Status / Card counts on the left, Actions on the right
        middle_layout = QHBoxLayout()
        middle_layout.setSpacing(14)

        box_style = """
            QGroupBox {
                font-size: 13px;
                font-weight: bold;
                border: 1px solid rgba(255, 255, 255, 0.12);
                border-radius: 8px;
                margin-top: 14px;
                padding: 18px 16px 14px 16px;
                background-color: rgba(255, 255, 255, 0.02);
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                subcontrol-position: top left;
                left: 12px;
                padding: 0 4px;
            }
        """

        # Left Column: Card Counts
        status_box = QGroupBox("Card Counts")
        status_box.setStyleSheet(box_style)
        status_grid = QGridLayout(status_box)
        status_grid.setContentsMargins(8, 12, 8, 8)
        status_grid.setVerticalSpacing(10)
        status_grid.setHorizontalSpacing(20)

        # Category labels on left, values in a vertical column on right
        lbl_new = QLabel("New cards:")
        lbl_new.setStyleSheet("font-size: 13px; font-weight: normal;")
        self.new_val_label = QLabel("0")
        self.new_val_label.setStyleSheet("font-size: 13px; font-weight: bold;")
        self.new_cards_label = self.new_val_label
        status_grid.addWidget(lbl_new, 0, 0)
        status_grid.addWidget(self.new_val_label, 0, 1, Qt.AlignmentFlag.AlignRight)

        lbl_mod = QLabel("Modified cards:")
        lbl_mod.setStyleSheet("font-size: 13px; font-weight: normal;")
        self.modified_val_label = QLabel("0")
        self.modified_val_label.setStyleSheet("font-size: 13px; font-weight: bold;")
        self.modified_cards_label = self.modified_val_label
        status_grid.addWidget(lbl_mod, 1, 0)
        status_grid.addWidget(self.modified_val_label, 1, 1, Qt.AlignmentFlag.AlignRight)

        lbl_occ = QLabel("Image occlusions:")
        lbl_occ.setStyleSheet("font-size: 13px; font-weight: normal;")
        self.occlusions_val_label = QLabel("0")
        self.occlusions_val_label.setStyleSheet("font-size: 13px; font-weight: bold;")
        self.occlusions_label = self.occlusions_val_label
        status_grid.addWidget(lbl_occ, 2, 0)
        status_grid.addWidget(self.occlusions_val_label, 2, 1, Qt.AlignmentFlag.AlignRight)

        lbl_orph = QLabel("Orphaned cards:")
        lbl_orph.setStyleSheet("font-size: 13px; font-weight: normal;")
        self.orphans_val_label = QLabel("0")
        self.orphans_val_label.setStyleSheet("font-size: 13px; font-weight: bold;")
        self.orphans_label = self.orphans_val_label
        status_grid.addWidget(lbl_orph, 3, 0)
        status_grid.addWidget(self.orphans_val_label, 3, 1, Qt.AlignmentFlag.AlignRight)

        status_grid.setColumnStretch(0, 1)
        status_grid.setColumnStretch(1, 0)
        status_grid.setRowStretch(4, 1)

        middle_layout.addWidget(status_box, stretch=1)

        # Right Column: Action Buttons (Sync first, Preview Diff second, Rescan Vault third)
        actions_box = QGroupBox("Actions")
        actions_box.setStyleSheet(box_style)
        actions_layout = QVBoxLayout(actions_box)
        actions_layout.setContentsMargins(8, 12, 8, 8)
        actions_layout.setSpacing(10)

        action_btn_style = """
            QPushButton {
                font-size: 13px;
                border-radius: 6px;
                padding: 6px 14px;
            }
        """

        self.sync_btn = QPushButton("Sync")
        self.sync_btn.setFixedHeight(36)
        self.sync_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.sync_btn.setStyleSheet("""
            QPushButton {
                font-size: 13px;
                font-weight: bold;
                border-radius: 6px;
                padding: 6px 14px;
            }
        """)
        self.sync_btn.setEnabled(False)
        self.sync_btn.clicked.connect(lambda: self._start_sync(include_updates=True))

        self.diff_btn = QPushButton("Preview Diff")
        self.diff_btn.setFixedHeight(36)
        self.diff_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.diff_btn.setStyleSheet(action_btn_style)
        self.diff_btn.setEnabled(False)
        self.diff_btn.clicked.connect(self._open_diff)

        self.rescan_btn = QPushButton("Rescan Vault")
        self.rescan_btn.setFixedHeight(36)
        self.rescan_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.rescan_btn.setStyleSheet(action_btn_style)
        self.rescan_btn.clicked.connect(self._start_scan)

        actions_layout.addWidget(self.sync_btn)
        actions_layout.addWidget(self.diff_btn)
        actions_layout.addWidget(self.rescan_btn)
        actions_layout.addStretch()

        middle_layout.addWidget(actions_box, stretch=1)
        layout.addLayout(middle_layout)

        # 3. Activity Log
        log_label = QLabel("Activity Log:")
        log_label.setStyleSheet("font-size: 13px; font-weight: bold;")
        layout.addWidget(log_label)

        self.log_edit = QPlainTextEdit()
        self.log_edit.setReadOnly(True)
        self.log_edit.setStyleSheet("""
            QPlainTextEdit {
                font-family: monospace;
                font-size: 12px;
                border-radius: 8px;
                padding: 8px;
                border: 1px solid rgba(255, 255, 255, 0.1);
            }
        """)
        layout.addWidget(self.log_edit)

        # 4. Bottom Bar
        bottom_layout = QHBoxLayout()
        bottom_layout.setSpacing(10)

        bottom_btn_style = """
            QPushButton {
                font-size: 13px;
                border-radius: 6px;
                padding: 5px 14px;
            }
        """

        self.orphans_btn = QPushButton("View Orphans in Browser")
        self.orphans_btn.setFixedHeight(34)
        self.orphans_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.orphans_btn.setStyleSheet(bottom_btn_style)
        self.orphans_btn.setEnabled(False)
        self.orphans_btn.clicked.connect(self._view_orphans)

        settings_btn = QPushButton("Settings")
        settings_btn.setFixedHeight(34)
        settings_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        settings_btn.setStyleSheet(bottom_btn_style)
        settings_btn.clicked.connect(self._open_settings)

        close_btn = QPushButton("Close")
        close_btn.setFixedHeight(34)
        close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        close_btn.setStyleSheet(bottom_btn_style)
        close_btn.clicked.connect(self.close)

        bottom_layout.addStretch()
        bottom_layout.addWidget(self.orphans_btn)
        bottom_layout.addWidget(settings_btn)
        bottom_layout.addWidget(close_btn)
        layout.addLayout(bottom_layout)

    def _log(self, text: str) -> None:
        now = datetime.datetime.now().strftime("%H:%M:%S")
        self.log_edit.appendPlainText(f"[{now}] {text}")

    def _refresh_header(self) -> None:
        self._settings = self._manager.load(self._profile_name)
        if self._settings.vault_path:
            self.vault_label.setText(f"<b>Vault:</b> {self._settings.vault_path}")
        else:
            self.vault_label.setText("<b>Vault:</b> (Not Configured)")

    def _set_buttons_busy(self, busy: bool) -> None:
        self._is_busy = busy
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
        self.orphans_btn.setEnabled(not busy and (self._current_plan is not None and len(self._current_plan.orphans) > 0))

    def _start_scan(self) -> None:
        if self._is_busy:
            return
        if not self._settings.vault_path or not self._settings.vault_path.is_dir():
            QMessageBox.information(
                self, "Vault Not Configured", "Please select your Obsidian vault folder in Settings."
            )
            self._open_settings()
            return

        self._set_buttons_busy(True)
        self._log(f"Scanning vault: {self._settings.vault_path}...")

        vault_p = self._settings.vault_path
        b_mid = self._settings.basic_model_id
        c_mid = self._settings.cloze_model_id

        def _scan_op(col) -> SyncPlan:
            return build_sync_plan(col, vault_p, basic_model_id=b_mid, cloze_model_id=c_mid)

        QueryOp(
            parent=self,
            op=_scan_op,
            success=self._on_scan_success,
        ).run_in_background()

    def _on_scan_success(self, plan: SyncPlan) -> None:
        self._current_plan = plan
        self._set_buttons_busy(False)

        # Update card count values in aligned column
        self.new_val_label.setText(str(plan.total_creates))
        self.modified_val_label.setText(str(plan.total_updates))
        self.occlusions_val_label.setText(str(len(plan.occlusions_to_stage)))
        self.orphans_val_label.setText(str(len(plan.orphans)))

        self._log(
            f"Vault scan complete: {plan.total_files} file(s), "
            f"{plan.total_creates} new card(s), {plan.total_updates} modified, "
            f"{plan.total_unchanged} unchanged."
        )
        if plan.orphans:
            self._log(f"Detected {len(plan.orphans)} orphaned card(s) in Anki no longer found in vault.")

        # Update button labels with counts
        total_sync_count = plan.total_creates + plan.total_updates + len(plan.occlusions_to_stage)
        self.sync_btn.setText(f"Sync ({total_sync_count})")
        self.diff_btn.setText(f"Preview Diff ({plan.total_updates})")
        self.orphans_btn.setText(f"View Orphans in Browser ({len(plan.orphans)})")

    def _open_diff(self) -> None:
        if self._current_plan is None or self._current_plan.total_updates == 0:
            return
        dlg = DiffDialog(self._current_plan, self)
        dlg.exec()
        self.diff_btn.setText(f"Preview Diff ({self._current_plan.approved_updates})")

    def _start_sync(self, include_updates: bool) -> None:
        if self._is_busy or self._current_plan is None:
            return

        # Check if diff preview is requested before updating
        if include_updates and self._settings.show_diff_before_update and self._current_plan.total_updates > 0:
            dlg = DiffDialog(self._current_plan, self)
            if not dlg.exec():
                self._log("Sync cancelled by user in diff preview.")
                return

        self._set_buttons_busy(True)
        self._log("Starting Sync...")

        plan = self._current_plan
        occ_dir = self._settings.occlusion_dir
        b_mid = self._settings.basic_model_id
        c_mid = self._settings.cloze_model_id

        def _sync_op(col) -> SyncReport:
            return execute_sync_plan(
                col,
                plan,
                include_updates=include_updates,
                occlusion_dir=occ_dir,
                basic_model_id=b_mid,
                cloze_model_id=c_mid,
            )

        QueryOp(
            parent=self,
            op=_sync_op,
            success=self._on_sync_success,
        ).with_progress("Syncing cards to Anki...").run_in_background()

    def _on_sync_success(self, report: SyncReport) -> None:
        self._set_buttons_busy(False)

        if mw is not None and hasattr(mw, "deckBrowser"):
            mw.deckBrowser.refresh()
            mw.reset()

        self._log(
            f"Sync finished: {report.created_count} added, {report.updated_count} updated, "
            f"{report.deck_moved_count} moved, {report.media_added_count} media file(s)."
        )

        for f_path, reason in report.skipped_files:
            self._log(f"WARNING: Skipped {f_path.name}: {reason}")
        for warn in report.warnings:
            self._log(f"WARNING: {warn}")

        tooltip(f"VaultSync: {report.created_count} added, {report.updated_count} updated.")

        # Re-scan to refresh counters
        self._start_scan()

    def _view_orphans(self) -> None:
        if self._current_plan is None or not self._current_plan.orphans:
            return

        nids = [str(o.note_id) for o in self._current_plan.orphans]
        query = f"nid:{','.join(nids)}"
        browser = dialogs.open("Browser", mw)
        browser.search_for(query)

    def _open_settings(self) -> None:
        dlg = ConfigWindow(self._manager, self._profile_name, self)
        if dlg.exec():
            self._refresh_header()
            if self._settings.vault_path and self._settings.vault_path.is_dir():
                self._start_scan()
