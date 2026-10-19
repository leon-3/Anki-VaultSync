from __future__ import annotations

import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from aqt import dialogs, mw
from aqt.operations import QueryOp
from aqt.qt import (
    QComboBox,
    QDialog,
    QEvent,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QStackedWidget,
    Qt,
    QVBoxLayout,
    QWidget,
    sip,
)
from aqt.utils import openLink
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
from ..contracts import OcclusionStatus, SyncPlan, SyncReport, UnflagReport
from ..profile_config import ProfileConfigManager
from ..vault_detector import DetectedVault, detect_obsidian_vaults
from ..worker import (
    build_sync_plan,
    execute_sync_plan,
    get_occlusion_status,
    unflag_completed_occlusions,
)
from markdown2anki.constants import OCCLUSION_TAG
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

        self.icon_lbl = QLabel("!")
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
        self.resize(760, 350)

        self._manager = profile_manager
        self._profile_name = profile_name
        self._settings = self._manager.load(profile_name)

        self._current_plan: SyncPlan | None = None
        self._last_report: SyncReport | None = None
        self._is_busy = False
        self._config_dlg: ConfigWindow | None = None

        self._init_ui()
        self._refresh_view()

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

        box_style = """
            QFrame {
                border: 1px solid rgba(130, 130, 130, 0.25);
                border-radius: 8px;
                background-color: rgba(130, 130, 130, 0.04);
            }
        """

        # 2. Stacked container: Empty-State Setup View (Index 0) vs Active Dashboard (Index 1)
        self.main_stack = QStackedWidget(self)

        # -----------------------------
        # View 0: Setup Empty-State View
        # -----------------------------
        self.setup_widget = QFrame()
        self.setup_widget.setStyleSheet(box_style)
        setup_layout = QVBoxLayout(self.setup_widget)
        setup_layout.setContentsMargins(18, 14, 18, 14)
        setup_layout.setSpacing(10)

        title_setup = QLabel("Connect Your Obsidian Vault")
        title_setup.setStyleSheet("font-size: 15px; font-weight: bold; border: none; background: transparent;")
        setup_layout.addWidget(title_setup)

        desc_setup = QLabel("Select your local vault directory and a target deck to begin.")
        desc_setup.setStyleSheet("font-size: 12px; color: palette(window-text); opacity: 0.8; border: none; background: transparent;")
        setup_layout.addWidget(desc_setup)

        # Dynamic Quick Connect Card
        self.quick_connect_box = QFrame()
        self.quick_connect_box.setStyleSheet("""
            QFrame {
                border: 1px solid rgba(33, 150, 243, 0.35);
                border-radius: 6px;
                background-color: rgba(33, 150, 243, 0.08);
            }
        """)
        qc_layout = QHBoxLayout(self.quick_connect_box)
        qc_layout.setContentsMargins(12, 8, 12, 8)
        qc_layout.setSpacing(10)

        self.lbl_qc_info = QLabel()
        self.lbl_qc_info.setStyleSheet("font-size: 12px; border: none; background: transparent;")
        self.lbl_qc_info.setWordWrap(True)
        qc_layout.addWidget(self.lbl_qc_info, stretch=1)

        self.btn_quick_connect = QPushButton("Quick Connect & Scan")
        self.btn_quick_connect.setFixedHeight(32)
        self.btn_quick_connect.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_quick_connect.setStyleSheet("""
            QPushButton {
                font-size: 12px;
                font-weight: 600;
                border-radius: 5px;
                padding: 4px 14px;
                background-color: palette(highlight);
                color: palette(highlighted-text);
            }
        """)
        self.btn_quick_connect.clicked.connect(self._on_quick_connect_clicked)
        qc_layout.addWidget(self.btn_quick_connect)
        setup_layout.addWidget(self.quick_connect_box)

        # Manual configuration form
        manual_grid = QGridLayout()
        manual_grid.setContentsMargins(0, 4, 0, 0)
        manual_grid.setVerticalSpacing(8)
        manual_grid.setHorizontalSpacing(10)

        lbl_vpath = QLabel("Vault Folder:")
        lbl_vpath.setStyleSheet("font-size: 12px; font-weight: 600; border: none; background: transparent;")
        manual_grid.addWidget(lbl_vpath, 0, 0)

        v_row = QHBoxLayout()
        v_row.setSpacing(6)
        self.setup_vault_edit = QLineEdit()
        self.setup_vault_edit.setFixedHeight(30)
        self.setup_vault_edit.setPlaceholderText("/path/to/your/obsidian/vault")
        self.setup_vault_edit.setStyleSheet("""
            QLineEdit {
                border: 1px solid rgba(130, 130, 130, 0.35);
                border-radius: 5px;
                padding: 4px 8px;
                font-size: 12px;
            }
        """)
        self.setup_vault_edit.textChanged.connect(self._on_setup_vault_changed)
        self.setup_vault_edit.editingFinished.connect(lambda: self._on_setup_vault_changed(self.setup_vault_edit.text()))
        self.setup_browse_btn = QPushButton("Browse...")
        self.setup_browse_btn.setFixedHeight(30)
        self.setup_browse_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setup_browse_btn.clicked.connect(self._on_setup_browse)
        v_row.addWidget(self.setup_vault_edit)
        v_row.addWidget(self.setup_browse_btn)
        manual_grid.addLayout(v_row, 0, 1)

        lbl_tdeck = QLabel("Default Deck:")
        lbl_tdeck.setStyleSheet("font-size: 12px; font-weight: 600; border: none; background: transparent;")
        manual_grid.addWidget(lbl_tdeck, 1, 0)

        self.setup_deck_combo = QComboBox()
        self.setup_deck_combo.setEditable(True)
        self.setup_deck_combo.setFixedHeight(30)
        self.setup_deck_combo.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.setup_deck_combo.setStyleSheet("""
            QComboBox {
                border: 1px solid rgba(130, 130, 130, 0.35);
                border-radius: 5px;
                padding: 4px 8px;
                font-size: 12px;
            }
        """)
        manual_grid.addWidget(self.setup_deck_combo, 1, 1)

        setup_layout.addLayout(manual_grid)

        # Action row: Save and Scan Vault + Guide links side-by-side
        act_row = QHBoxLayout()
        act_row.setSpacing(8)

        self.btn_save_and_scan = QPushButton("Save and Scan Vault")
        self.btn_save_and_scan.setFixedHeight(34)
        self.btn_save_and_scan.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_save_and_scan.setStyleSheet("""
            QPushButton {
                font-size: 12px;
                font-weight: 600;
                border-radius: 6px;
                padding: 6px 16px;
                background-color: palette(highlight);
                color: palette(highlighted-text);
            }
        """)
        self.btn_save_and_scan.clicked.connect(self._on_save_and_scan_clicked)
        act_row.addWidget(self.btn_save_and_scan)
        act_row.addSpacing(6)

        guide_btn_style = """
            QPushButton {
                font-size: 11px;
                border: 1px solid rgba(130, 130, 130, 0.3);
                border-radius: 5px;
                padding: 4px 10px;
                background: transparent;
            }
            QPushButton:hover {
                background: rgba(130, 130, 130, 0.1);
            }
        """

        btn_first_guide = QPushButton("First User Guide ↗")
        btn_first_guide.setFixedHeight(32)
        btn_first_guide.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_first_guide.setStyleSheet(guide_btn_style)
        btn_first_guide.clicked.connect(
            lambda: openLink("https://github.com/leon-3/Anki-VaultSync#dashboard-overview")
        )
        act_row.addWidget(btn_first_guide)

        btn_doc_guide = QPushButton("Documentation / Card Syntax ↗")
        btn_doc_guide.setFixedHeight(32)
        btn_doc_guide.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_doc_guide.setStyleSheet(guide_btn_style)
        btn_doc_guide.clicked.connect(
            lambda: openLink("https://github.com/leon-3/Anki-VaultSync#note-syntax--conventions")
        )
        act_row.addWidget(btn_doc_guide)
        act_row.addStretch()
        setup_layout.addLayout(act_row)

        self.main_stack.addWidget(self.setup_widget)

        # -----------------------------
        # View 1: Active Dashboard View
        # -----------------------------
        self.dashboard_widget = QWidget()
        middle_layout = QHBoxLayout(self.dashboard_widget)
        middle_layout.setContentsMargins(0, 0, 0, 0)
        middle_layout.setSpacing(14)

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

        self.unflag_occ_btn = QPushButton("Unflag Occlusions")
        self.unflag_occ_btn.setFixedHeight(36)
        self.unflag_occ_btn.setAutoDefault(False)
        self.unflag_occ_btn.setDefault(False)
        self.unflag_occ_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.unflag_occ_btn.setStyleSheet(action_btn_style)
        self.unflag_occ_btn.setEnabled(False)
        self.unflag_occ_btn.clicked.connect(self._on_unflag_occlusions)

        actions_layout.addWidget(self.sync_btn)
        actions_layout.addWidget(self.diff_btn)
        actions_layout.addWidget(self.rescan_btn)
        actions_layout.addWidget(self.unflag_occ_btn)
        actions_layout.addStretch()

        middle_layout.addWidget(actions_box, stretch=1)
        self.main_stack.addWidget(self.dashboard_widget)
        layout.addWidget(self.main_stack)

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

    def _refresh_view(self) -> None:
        self._settings = self._manager.load(self._profile_name)
        has_valid_vault = bool(self._settings.vault_path and self._settings.vault_path.is_dir())

        self._populate_setup_fields()

        if has_valid_vault:
            self.main_stack.setCurrentWidget(self.dashboard_widget)
            self._refresh_header()
            self._start_scan()
        else:
            self.main_stack.setCurrentWidget(self.setup_widget)
            self.vault_label.setText("<b>Vault:</b> (Not Connected)")
            self._set_status("No vault connected. Configure your vault to begin.", state="warning")

    def _populate_setup_fields(self) -> None:
        self.setup_deck_combo.clear()
        deck_names: list[str] = []
        if mw is not None and getattr(mw, "col", None) is not None and hasattr(mw.col, "decks"):
            try:
                deck_names = sorted(mw.col.decks.all_names())
            except Exception:
                pass
        if not deck_names:
            deck_names = ["Default"]
        for d in deck_names:
            self.setup_deck_combo.addItem(d)

        default_idx = self.setup_deck_combo.findText("Default")
        if default_idx >= 0:
            self.setup_deck_combo.setCurrentIndex(default_idx)

        detected = detect_obsidian_vaults()
        self._detected_vaults = list(detected)
        if self._detected_vaults:
            best = self._detected_vaults[0]
            self._primary_detected = best
            open_str = " (Active)" if best.is_open else ""
            self.lbl_qc_info.setText(
                f"<b>Detected Vault:</b> {best.name}{open_str}<br>"
                f"<span style='color: palette(window-text); opacity: 0.75;'>{best.path}</span>"
            )
            self.quick_connect_box.setVisible(True)
            self.setup_vault_edit.setText(str(best.path))
            self._on_setup_vault_changed(str(best.path))
        else:
            self._primary_detected = None
            self.quick_connect_box.setVisible(False)
            if self._settings.vault_path:
                self.setup_vault_edit.setText(str(self._settings.vault_path))
                self._on_setup_vault_changed(str(self._settings.vault_path))

    def _on_quick_connect_clicked(self) -> None:
        if not getattr(self, "_primary_detected", None):
            return
        target_deck = self.setup_deck_combo.currentText().strip() or "Default"
        self._save_vault_and_start(self._primary_detected.path, target_deck)

    def _on_setup_vault_changed(self, raw_path: str) -> None:
        raw = raw_path.strip()
        if not raw:
            return
        p = Path(raw).expanduser()
        try:
            p_resolved = p.resolve()
            if p_resolved.is_dir() and (p_resolved / "m2a.toml").is_file():
                cfg = self._manager.load_vault(p_resolved)
                if cfg.deck:
                    self.setup_deck_combo.setCurrentText(cfg.deck)
        except Exception:
            pass

    def _on_setup_browse(self) -> None:
        curr = self.setup_vault_edit.text().strip() or str(Path.home())
        selected = QFileDialog.getExistingDirectory(self, "Select Obsidian Vault Folder", curr)
        if selected:
            self.setup_vault_edit.setText(selected)
            self._on_setup_vault_changed(selected)

    def _on_save_and_scan_clicked(self) -> None:
        raw = self.setup_vault_edit.text().strip()
        if not raw:
            QMessageBox.warning(self, "Invalid Vault", "Please specify an Obsidian vault directory.")
            return
        p = Path(raw).expanduser().resolve()
        if not p.is_dir():
            QMessageBox.warning(self, "Invalid Directory", f"The folder '{p}' does not exist.")
            return
        target_deck = self.setup_deck_combo.currentText().strip() or "Default"
        self._save_vault_and_start(p, target_deck)

    def _save_vault_and_start(self, vault_path: Path, target_deck: str) -> None:
        self._settings = self._manager.load(self._profile_name)
        self._settings.vault_path = vault_path
        self._settings.onboarding_completed = True
        self._manager.save(self._settings)

        try:
            cfg = self._manager.load_vault(vault_path)
            if not cfg.deck:
                cfg.deck = target_deck
            elif target_deck and target_deck != "Default" and cfg.deck == "Default":
                cfg.deck = target_deck
            self._manager.save_vault(vault_path, cfg)
        except Exception:
            pass

        self._refresh_view()

    def _refresh_header(self) -> None:
        self._settings = self._manager.load(self._profile_name)
        if self._settings.vault_path:
            self.vault_label.setText(f"<b>Vault:</b> {self._settings.vault_path}")
        else:
            self.vault_label.setText("<b>Vault:</b> (Not Connected)")

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
        has_ready_occ = getattr(self, "_occ_status", None) is not None and self._occ_status.ready_to_unflag > 0
        if hasattr(self, "unflag_occ_btn"):
            self.unflag_occ_btn.setEnabled(not busy and has_ready_occ)

    def _on_op_failure(self, exc: Exception, retry_action: Callable[[], None] | None = None) -> None:
        if _is_deleted(self):
            return
        self._set_buttons_busy(False)
        self._set_status(f"Operation failed: {exc}", state="error")
        self._log(f"ERROR: {exc}", level="ERROR")
        self.error_banner.show_error(f"An error occurred: {exc}", retry_action=retry_action)
        from .status_bar import notify_sync_finished
        notify_sync_finished(report=None, error=exc)

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

        def _scan_op(col) -> tuple[SyncPlan, OcclusionStatus]:
            plan = build_sync_plan(col, vault_p, basic_model_id=b_mid, cloze_model_id=c_mid)
            occ_status = get_occlusion_status(col)
            return plan, occ_status

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

    def _on_scan_success(self, plan: SyncPlan, unmasked_in_anki: Any = 0) -> None:
        if _is_deleted(self):
            return
        self._current_plan = plan
        if isinstance(unmasked_in_anki, OcclusionStatus):
            self._occ_status = unmasked_in_anki
        else:
            self._occ_status = OcclusionStatus(total=int(unmasked_in_anki), awaiting_masks=int(unmasked_in_anki), ready_to_unflag=0)
        self._set_buttons_busy(False)

        from .status_bar import notify_scan_result
        notify_scan_result(plan)

        total_new = plan.total_creates + len(plan.occlusions_to_stage)
        self.new_val_label.setText(str(total_new))
        self.modified_val_label.setText(str(plan.total_updates))

        if self._occ_status.total > 0:
            if self._occ_status.ready_to_unflag > 0:
                self.occlusions_val_label.setText(f"{self._occ_status.total} ({self._occ_status.ready_to_unflag} ready)")
                self.occlusions_val_label.setToolTip(
                    f"{self._occ_status.awaiting_masks} card(s) awaiting masks in Anki (tag:{OCCLUSION_TAG}).\n"
                    f"{self._occ_status.ready_to_unflag} card(s) have masks drawn and are ready to unsuspend.\n"
                    "Click to view in Anki Browser."
                )
            else:
                self.occlusions_val_label.setText(str(self._occ_status.total))
                self.occlusions_val_label.setToolTip(
                    f"{self._occ_status.total} card(s) awaiting masks in Anki (tag:{OCCLUSION_TAG}).\n"
                    "Click to view in Anki Browser."
                )
        else:
            self.occlusions_val_label.setText("0")
            self.occlusions_val_label.setToolTip("No unprocessed image occlusions in Anki.")

        if hasattr(self, "unflag_occ_btn"):
            if self._occ_status.ready_to_unflag > 0:
                self.unflag_occ_btn.setText(f"Unflag Occlusions ({self._occ_status.ready_to_unflag})")
                self.unflag_occ_btn.setEnabled(not self._is_busy)
            else:
                self.unflag_occ_btn.setText("Unflag Occlusions")
                self.unflag_occ_btn.setEnabled(False)

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
            status_text = f"Ready · {issues_count} issue(s) detected"
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
        dlg = DiffDialog(self._current_plan, self, occ_status=getattr(self, "_occ_status", None))
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

        unflag_from_diff = False
        if include_updates and self._settings.show_diff_before_update and self._current_plan.total_updates > 0:
            dlg = DiffDialog(self._current_plan, self, occ_status=getattr(self, "_occ_status", None))
            res = dlg.exec()
            if _is_deleted(self):
                return
            if not res:
                if getattr(dlg, "rescan_requested", False):
                    self._start_scan()
                    return
                self._log("Sync cancelled by user in diff preview.")
                return
            unflag_from_diff = dlg.should_unflag_occlusions
            self._update_action_button_counts()

        from ..sync_lock import acquire_sync_lock, release_sync_lock
        if not acquire_sync_lock():
            self._set_status("Operation in progress. Please wait...", state="warning")
            return

        self.error_banner.hide()
        self._set_buttons_busy(True)
        self._set_status("Syncing cards to Anki...", state="busy")
        self._log("Starting Sync...")

        from .status_bar import notify_sync_started
        notify_sync_started()

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
                unflag_occlusions=unflag_from_diff,
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

        from .status_bar import notify_sync_finished
        notify_sync_finished(report=report)

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

    def _on_unflag_occlusions(self) -> None:
        from ..sync_lock import acquire_sync_lock, release_sync_lock
        if not acquire_sync_lock():
            self._set_status("Operation in progress. Please wait...", state="warning")
            return
        self._set_buttons_busy(True)
        self._set_status("Unflagging completed occlusions...", state="busy")

        def _op(col):
            return unflag_completed_occlusions(col, dry_run=False)

        def _success(rep: UnflagReport):
            release_sync_lock()
            if _is_deleted(self):
                return
            self._set_buttons_busy(False)
            if rep.unflagged_notes_count > 0:
                self._log(f"Unflagged {rep.unflagged_notes_count} note(s), unsuspended {rep.unsuspended_cards_count} card(s).")
                try:
                    tooltip(f"✔ Unsuspended {rep.unflagged_notes_count} note(s) ({rep.unsuspended_cards_count} card(s)).")
                except Exception:
                    pass
            else:
                self._log("No completed occlusions found to unflag.")
                try:
                    tooltip("No completed occlusions found.")
                except Exception:
                    pass
            self._start_scan()

        def _failure(exc: Exception):
            release_sync_lock()
            if _is_deleted(self):
                return
            self._set_buttons_busy(False)
            self._on_op_failure(exc, retry_action=self._on_unflag_occlusions)

        import aqt
        if getattr(aqt, "mw", None) is None:
            try:
                res = _op(getattr(mw, "col", None))
                _success(res)
            except Exception as exc:
                _failure(exc)
            return

        QueryOp(
            parent=self,
            op=_op,
            success=_success,
        ).failure(_failure).with_progress("Unflagging completed occlusions...").run_in_background()

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
            self._refresh_view()

        from .menu import open_settings
        self._config_dlg = open_settings(
            tab_index=tab_index,
            sync_plan=self._current_plan,
            on_accepted=_on_accepted,
        )
