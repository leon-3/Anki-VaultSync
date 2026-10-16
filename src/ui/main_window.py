from __future__ import annotations

import datetime
from pathlib import Path
from typing import TYPE_CHECKING

from aqt import dialogs, mw
from aqt.operations import QueryOp
from aqt.qt import (
    QCheckBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
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
            self._log("Welcome to VaultSync! Click 'Change Vault' or 'Settings' below to select your Obsidian vault.")

    def _init_ui(self) -> None:
        layout = QVBoxLayout(self)

        # Header Info Card
        self.profile_label = QLabel(f"<b>Active Profile:</b> {self._profile_name}")
        self.vault_label = QLabel("<b>Vault Path:</b> (Not Configured)")
        self.deck_label = QLabel("<b>Default Deck:</b> (Default)")
        self.status_label = QLabel("<b>Status:</b> Ready")

        change_vault_btn = QPushButton("Change Vault")
        change_vault_btn.clicked.connect(self._open_settings)

        header_top = QHBoxLayout()
        header_top.addWidget(self.vault_label)
        header_top.addWidget(change_vault_btn)

        layout.addWidget(self.profile_label)
        layout.addLayout(header_top)
        layout.addWidget(self.deck_label)
        layout.addWidget(self.status_label)
        layout.addSpacing(8)

        # Action Buttons
        btn_layout = QHBoxLayout()
        self.rescan_btn = QPushButton("Rescan Vault")
        self.rescan_btn.clicked.connect(self._start_scan)

        self.diff_btn = QPushButton("Preview Diff")
        self.diff_btn.setEnabled(False)
        self.diff_btn.clicked.connect(self._open_diff)

        self.sync_new_btn = QPushButton("Sync New Cards")
        self.sync_new_btn.setEnabled(False)
        self.sync_new_btn.clicked.connect(lambda: self._start_sync(include_updates=False))

        self.sync_all_btn = QPushButton("Sync and Update (Modified Cards)")
        self.sync_all_btn.setEnabled(False)
        self.sync_all_btn.clicked.connect(lambda: self._start_sync(include_updates=True))

        btn_layout.addWidget(self.rescan_btn)
        btn_layout.addWidget(self.diff_btn)
        btn_layout.addWidget(self.sync_new_btn)
        btn_layout.addWidget(self.sync_all_btn)
        layout.addLayout(btn_layout)
        layout.addSpacing(8)

        # Activity Log
        layout.addWidget(QLabel("Activity Log:"))
        self.log_edit = QPlainTextEdit()
        self.log_edit.setReadOnly(True)
        layout.addWidget(self.log_edit)

        # Bottom Bar
        bottom_layout = QHBoxLayout()
        self.auto_close_cb = QCheckBox("Auto-close on success")
        self.auto_close_cb.setChecked(self._settings.auto_close_on_success)
        self.auto_close_cb.toggled.connect(self._on_auto_close_toggled)

        self.orphans_btn = QPushButton("View Orphans in Browser")
        self.orphans_btn.setEnabled(False)
        self.orphans_btn.clicked.connect(self._view_orphans)

        settings_btn = QPushButton("Settings")
        settings_btn.clicked.connect(self._open_settings)

        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.close)

        bottom_layout.addWidget(self.auto_close_cb)
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
            self.vault_label.setText(f"<b>Vault Path:</b> {self._settings.vault_path}")
            self.status_label.setText("<b>Status:</b> Ready to scan")
        else:
            self.vault_label.setText("<b>Vault Path:</b> (Not Configured)")
            self.status_label.setText("<b>Status:</b> Vault not configured. Click 'Change Vault' or 'Settings' to select your vault.")

        default_deck = "Default"
        if self._settings.vault_path and self._settings.vault_path.is_dir():
            try:
                vcfg = self._manager.load_vault(self._settings.vault_path)
                default_deck = vcfg.deck or "Default"
            except Exception:
                pass
        self.deck_label.setText(f"<b>Default Deck:</b> {default_deck}")

    def _set_buttons_busy(self, busy: bool) -> None:
        self._is_busy = busy
        self.rescan_btn.setEnabled(not busy)
        self.diff_btn.setEnabled(not busy and (self._current_plan is not None and self._current_plan.total_updates > 0))
        self.sync_new_btn.setEnabled(
            not busy
            and self._current_plan is not None
            and (self._current_plan.total_creates > 0 or len(self._current_plan.occlusions_to_stage) > 0)
        )
        self.sync_all_btn.setEnabled(
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

        summary = (
            f"<b>Status:</b> {plan.total_creates} new cards &middot; "
            f"{plan.total_updates} modified cards &middot; "
            f"{len(plan.occlusions_to_stage)} occlusions &middot; "
            f"{len(plan.orphans)} orphans"
        )
        self.status_label.setText(summary)

        self._log(
            f"Vault scan complete: {plan.total_files} file(s), "
            f"{plan.total_creates} new card(s), {plan.total_updates} modified, "
            f"{plan.total_unchanged} unchanged."
        )
        if plan.orphans:
            self._log(f"Detected {len(plan.orphans)} orphaned card(s) in Anki no longer found in vault.")

        # Update button labels with counts
        self.diff_btn.setText(f"Preview Diff ({plan.total_updates})")
        self.sync_new_btn.setText(f"Sync New Cards ({plan.total_creates})")
        self.sync_all_btn.setText(f"Sync and Update (Modified Cards) ({plan.total_updates})")
        self.orphans_btn.setText(f"View Orphans in Browser ({len(plan.orphans)})")

    def _open_diff(self) -> None:
        if self._current_plan is None or self._current_plan.total_updates == 0:
            return
        dlg = DiffDialog(self._current_plan, self)
        dlg.exec()
        # Refresh buttons state with approved updates count
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
        mode_text = "Sync and Update" if include_updates else "Sync New"
        self._log(f"Starting {mode_text}...")

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

        # Refresh UI
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

        if self.auto_close_cb.isChecked() and not report.skipped_files and not report.warnings:
            self.accept()

    def _view_orphans(self) -> None:
        if self._current_plan is None or not self._current_plan.orphans:
            return

        # Query in Anki card browser: "nid:123,456,789"
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

    def _on_auto_close_toggled(self, checked: bool) -> None:
        self._settings.auto_close_on_success = checked
        self._manager.save(self._settings)
