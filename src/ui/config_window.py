from __future__ import annotations

import html
import re
from pathlib import Path
from typing import Any

from aqt import dialogs, mw
from aqt.operations import QueryOp
from aqt.qt import (
    sip,
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QColor,
    QComboBox,
    QCursor,
    QDesktopServices,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QKeySequence,
    QKeySequenceEdit,
    QMessageBox,
    QPalette,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QToolTip,
    QUrl,
    Qt,
    QVBoxLayout,
    QWidget,
)
from ..logger import clear_log, get_log_file_path, log_error, read_log_text

def _is_deleted(widget: Any) -> bool:
    if sip is not None and hasattr(sip, "isdeleted"):
        try:
            return sip.isdeleted(widget)
        except Exception:
            return False
    return False


from ..bridge import ensure_models
from ..contracts import MissingInAnkiRecord, OrphanNoteRecord, SyncPlan
from ..worker import build_sync_plan, execute_sync_plan
from markdown2anki.flags import write_deleted_flags
from ..profile_config import AddonProfileSettings, DEFAULT_SHORTCUTS, ProfileConfigManager
from .help_tooltips import TOOLTIPS
from .shortcuts import find_shortcut_conflict


def _dim_placeholder(widget: QLineEdit | QPlainTextEdit) -> None:
    """Explicitly styles and dims placeholder text so it is clearly distinct from filled text."""
    pal = widget.palette()
    pal.setColor(QPalette.ColorRole.PlaceholderText, QColor(140, 140, 140, 170))
    widget.setPalette(pal)


class HelpBadge(QLabel):
    """Clean, circular help badge that shows a multiline wrapped tooltip box on hover and on click."""

    def __init__(self, tip_text: str, parent=None) -> None:
        super().__init__("?", parent)
        self.tip_text = tip_text
        self.tip_html = f'<div style="width: 320px; font-size: 12px; line-height: 1.4;">{html.escape(tip_text)}</div>'
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setFixedSize(18, 18)
        self.setToolTip(self.tip_html)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setStyleSheet("""
            QLabel {
                border: 1px solid rgba(130, 130, 130, 0.6);
                border-radius: 9px;
                font-weight: bold;
                font-size: 11px;
                color: palette(text);
                background-color: rgba(130, 130, 130, 0.15);
            }
            QLabel:hover {
                background-color: rgba(0, 122, 204, 0.45);
                border-color: rgba(0, 122, 204, 0.95);
                color: #ffffff;
            }
        """)

    def enterEvent(self, event) -> None:
        super().enterEvent(event)
        QToolTip.showText(QCursor.pos(), self.tip_html, self)

    def mousePressEvent(self, event) -> None:
        super().mousePressEvent(event)
        QToolTip.showText(QCursor.pos(), self.tip_html, self)


def _create_section_card(title: str | None = None, subtitle: str | None = None) -> tuple[QFrame, QVBoxLayout]:
    """Helper creating a modern, rounded card container for grouping settings."""
    card = QFrame()
    card.setObjectName("settingsCard")
    card.setStyleSheet("""
        #settingsCard {
            border: 1px solid rgba(255, 255, 255, 0.1);
            border-radius: 8px;
            background-color: rgba(255, 255, 255, 0.02);
        }
    """)
    layout = QVBoxLayout(card)
    layout.setContentsMargins(16, 14, 16, 16)
    layout.setSpacing(12)

    if title or subtitle:
        header_box = QVBoxLayout()
        header_box.setSpacing(3)
        if title:
            t_lbl = QLabel(f"<b>{title}</b>")
            t_lbl.setStyleSheet("font-size: 13px; font-weight: bold; border: none; background: transparent;")
            header_box.addWidget(t_lbl)
        if subtitle:
            s_lbl = QLabel(subtitle)
            s_lbl.setStyleSheet("font-size: 12px; color: rgba(180, 180, 180, 0.85); border: none; background: transparent;")
            s_lbl.setWordWrap(True)
            header_box.addWidget(s_lbl)
        layout.addLayout(header_box)
        layout.addSpacing(2)

    return card, layout


class ConfigWindow(QDialog):
    """Modern, tabbed settings configurator for Anki-VaultSync."""

    TAB_GENERAL = 0
    TAB_DECK_ROUTING = 1
    TAB_TAG_ALIASING = 2
    TAB_RULES = 3
    TAB_ADVANCED = 4
    TAB_MAINTENANCE = 5
    TAB_LOGS = 6

    def __init__(
        self,
        profile_manager: ProfileConfigManager,
        profile_name: str,
        parent=None,
        initial_tab: int | str | None = None,
        sync_plan: SyncPlan | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("VaultSync Settings")
        self.resize(820, 700)
        self.setWindowModality(Qt.WindowModality.NonModal)

        self._manager = profile_manager
        self._profile_name = profile_name
        self._profile_settings = self._manager.load(profile_name)

        self._vault_cfg = None
        self._vault_cfg_error: str | None = None
        if self._profile_settings.vault_path and self._profile_settings.vault_path.is_dir():
            try:
                self._vault_cfg = self._manager.load_vault(self._profile_settings.vault_path)
            except Exception as exc:
                self._vault_cfg = None
                self._vault_cfg_error = str(exc)

        self._current_orphans: list[OrphanNoteRecord] = []
        self._current_missing: list[MissingInAnkiRecord] = []

        self._init_ui()
        self._populate_fields()
        self._initial_state = self._snapshot_state()

        if sync_plan is not None:
            self.set_maintenance_plan(sync_plan)
        else:
            self._refresh_maintenance_data()

        if initial_tab is not None:
            self.switch_to_tab(initial_tab)

    def switch_to_tab(self, tab: int | str) -> None:
        if isinstance(tab, str):
            for idx in range(self.tabs.count()):
                if self.tabs.tabText(idx).strip().lower() == tab.strip().lower():
                    self.tabs.setCurrentIndex(idx)
                    return
        elif isinstance(tab, int) and 0 <= tab < self.tabs.count():
            self.tabs.setCurrentIndex(tab)

    @property
    def _default_deck_name(self) -> str:
        if self._vault_cfg and getattr(self._vault_cfg, "deck", None):
            return self._vault_cfg.deck
        if hasattr(self, "deck_combo") and self.deck_combo.currentText().strip():
            return self.deck_combo.currentText().strip()
        return "Default"

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self.setFocus()

    def _help_button(self, key: str) -> HelpBadge:
        tip_text = TOOLTIPS.get(key, "No help available.")
        return HelpBadge(tip_text)

    def _wrap_tab_in_scroll(self, tab_widget: QWidget, content_widget: QWidget) -> None:
        outer_layout = QVBoxLayout(tab_widget)
        outer_layout.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setStyleSheet("QScrollArea { background: transparent; border: none; }")
        content_widget.setStyleSheet("background: transparent;")
        scroll.setWidget(content_widget)
        outer_layout.addWidget(scroll)

    def _init_ui(self) -> None:
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(18, 16, 18, 16)
        main_layout.setSpacing(14)

        if self._vault_cfg_error:
            self.error_banner = QLabel(
                f"<b>⚠️ Warning: Failed to parse vault configuration (m2a.toml):</b><br>"
                f"{html.escape(self._vault_cfg_error)}<br>"
                f"<small>Default settings loaded. Please verify your m2a.toml syntax.</small>"
            )
            self.error_banner.setWordWrap(True)
            self.error_banner.setStyleSheet(
                "background-color: #fff3cd; color: #856404; border: 1px solid #ffeeba; "
                "border-radius: 6px; padding: 10px; font-size: 12px; font-family: sans-serif;"
            )
            main_layout.addWidget(self.error_banner)

        # Tabs container with modern styling
        self.tabs = QTabWidget()
        self.tabs.setStyleSheet("""
            QTabWidget::pane {
                border: 1px solid rgba(255, 255, 255, 0.12);
                border-radius: 8px;
                background-color: transparent;
                top: -1px;
            }
            QTabBar::tab {
                background: transparent;
                padding: 8px 14px;
                font-size: 13px;
                border-top-left-radius: 6px;
                border-top-right-radius: 6px;
                color: palette(text);
            }
            QTabBar::tab:selected {
                background: rgba(255, 255, 255, 0.08);
                font-weight: bold;
                border-bottom: 2px solid #2196F3;
            }
            QTabBar::tab:hover:!selected {
                background: rgba(255, 255, 255, 0.04);
            }
        """)
        main_layout.addWidget(self.tabs)

        # Tab 1: General
        self.tab_general = QWidget()
        self._init_tab_general()
        self.tabs.addTab(self.tab_general, "General")

        # Tab 2: Deck Routing
        self.tab_deck_routing = QWidget()
        self.tab_routing = self.tab_deck_routing
        self._init_tab_deck_routing()
        self.tabs.addTab(self.tab_deck_routing, "Deck Routing")

        # Tab 3: Tag Aliasing
        self.tab_tag_aliasing = QWidget()
        self._init_tab_tag_aliasing()
        self.tabs.addTab(self.tab_tag_aliasing, "Tag Aliasing")

        # Tab 4: Card Rules and Media
        self.tab_rules = QWidget()
        self._init_tab_rules()
        self.tabs.addTab(self.tab_rules, "Card Rules and Media")

        # Tab 5: Advanced
        self.tab_advanced = QWidget()
        self._init_tab_advanced()
        self.tabs.addTab(self.tab_advanced, "Advanced")

        # Tab 6: Maintenance
        self.tab_maintenance = QWidget()
        self._init_tab_maintenance()
        self.tabs.addTab(self.tab_maintenance, "Maintenance")

        # Tab 7: Logs
        self.tab_logs = QWidget()
        self._init_tab_logs()
        self.tabs.addTab(self.tab_logs, "Logs")

        # Dialog Buttons
        self.button_box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        save_btn = self.button_box.button(QDialogButtonBox.StandardButton.Save)
        if save_btn:
            save_btn.setDefault(False)
            save_btn.setAutoDefault(False)
        cancel_btn = self.button_box.button(QDialogButtonBox.StandardButton.Cancel)
        if cancel_btn:
            cancel_btn.setDefault(False)
            cancel_btn.setAutoDefault(False)

        self.button_box.setStyleSheet("""
            QPushButton {
                font-size: 13px;
                border-radius: 6px;
                padding: 6px 18px;
                min-height: 26px;
            }
        """)
        self.button_box.accepted.connect(self._on_save)
        self.button_box.rejected.connect(self.reject)
        main_layout.addWidget(self.button_box)

        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setFocus()

    def _init_tab_general(self) -> None:
        content_widget = QWidget()
        layout = QVBoxLayout(content_widget)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(16)

        input_style = """
            QLineEdit, QComboBox, QKeySequenceEdit {
                font-size: 13px;
                padding: 6px 10px;
                border-radius: 6px;
                border: 1px solid rgba(255, 255, 255, 0.15);
                min-height: 24px;
            }
            QLineEdit::placeholder {
                color: rgba(140, 140, 140, 0.65);
                font-style: italic;
            }
        """
        btn_style = """
            QPushButton {
                font-size: 13px;
                padding: 6px 14px;
                border-radius: 6px;
                min-height: 24px;
            }
        """

        # Section 1: Vault Directories
        card_dirs, card_dirs_l = _create_section_card(
            "Vault Directories",
            "Specify the local folders for your Obsidian markdown vault and image occlusions.",
        )

        lbl_vault = QLabel("Obsidian Vault Folder:")
        lbl_vault.setStyleSheet("font-size: 12px; font-weight: 600; color: rgba(220, 220, 220, 0.95);")
        card_dirs_l.addWidget(lbl_vault)

        vault_row = QHBoxLayout()
        vault_row.setSpacing(8)
        self.vault_edit = QLineEdit()
        self.vault_edit.setStyleSheet(input_style)
        self.vault_edit.setPlaceholderText("Select Obsidian vault folder...")
        _dim_placeholder(self.vault_edit)
        vault_browse = QPushButton("Browse...")
        vault_browse.setStyleSheet(btn_style)
        vault_browse.setCursor(Qt.CursorShape.PointingHandCursor)
        vault_browse.clicked.connect(self._browse_vault)
        vault_row.addWidget(self.vault_edit, 1)
        vault_row.addWidget(vault_browse)
        vault_row.addWidget(self._help_button("vault_path"))
        card_dirs_l.addLayout(vault_row)

        card_dirs_l.addSpacing(6)

        lbl_occ = QLabel("Image Occlusion Base Folder:")
        lbl_occ.setStyleSheet("font-size: 12px; font-weight: 600; color: rgba(220, 220, 220, 0.95);")
        card_dirs_l.addWidget(lbl_occ)

        occ_row = QHBoxLayout()
        occ_row.setSpacing(8)
        self.occlusion_edit = QLineEdit()
        self.occlusion_edit.setStyleSheet(input_style)
        self.occlusion_edit.setPlaceholderText("Optional: Base folder (an 'occlusions/' subfolder will be created inside)...")
        _dim_placeholder(self.occlusion_edit)
        occ_browse = QPushButton("Browse...")
        occ_browse.setStyleSheet(btn_style)
        occ_browse.setCursor(Qt.CursorShape.PointingHandCursor)
        occ_browse.clicked.connect(self._browse_occlusion)
        occ_row.addWidget(self.occlusion_edit, 1)
        occ_row.addWidget(occ_browse)
        occ_row.addWidget(self._help_button("occlusion_dir"))
        card_dirs_l.addLayout(occ_row)

        occ_hint = QLabel(
            "<i>Note: Images will be exported into an <code>occlusions/</code> subfolder inside this directory (e.g. <code>[Selected Folder]/occlusions/</code>), along with an <code>index.md</code> mapping tags to image files.</i>"
        )
        occ_hint.setStyleSheet("font-size: 11px; color: rgba(180, 180, 180, 0.85); line-height: 1.3;")
        occ_hint.setWordWrap(True)
        card_dirs_l.addWidget(occ_hint)
        layout.addWidget(card_dirs)

        # Section 2: Sync Behavior
        card_sync, card_sync_l = _create_section_card(
            "Sync Behavior",
            "Preferences for synchronization prompts and automated background sync.",
        )

        self.show_diff_cb = QCheckBox("Show Diff Preview Dialog Before Updating Modified Cards")
        self.show_diff_cb.setStyleSheet("font-size: 13px;")
        row_diff = QHBoxLayout()
        row_diff.setSpacing(8)
        row_diff.addWidget(self.show_diff_cb)
        row_diff.addWidget(self._help_button("show_diff_before_update"))
        row_diff.addStretch()
        card_sync_l.addLayout(row_diff)

        self.auto_close_cb = QCheckBox("Automatically Close Dashboard on Successful Sync")
        self.auto_close_cb.setStyleSheet("font-size: 13px;")
        row_auto_close = QHBoxLayout()
        row_auto_close.setSpacing(8)
        row_auto_close.addWidget(self.auto_close_cb)
        row_auto_close.addWidget(self._help_button("auto_close_on_success"))
        row_auto_close.addStretch()
        card_sync_l.addLayout(row_auto_close)

        self.sync_on_startup_cb = QCheckBox("Auto-sync on Anki Startup (Background)")
        self.sync_on_startup_cb.setStyleSheet("font-size: 13px;")
        row_startup = QHBoxLayout()
        row_startup.setSpacing(8)
        row_startup.addWidget(self.sync_on_startup_cb)
        row_startup.addWidget(self._help_button("sync_on_startup"))
        row_startup.addStretch()
        card_sync_l.addLayout(row_startup)

        self.suspend_new_clozes_cb = QCheckBox("Suspend Newly Created Text-Cloze Cards by Default")
        self.suspend_new_clozes_cb.setStyleSheet("font-size: 13px;")
        row_cloze_susp = QHBoxLayout()
        row_cloze_susp.setSpacing(8)
        row_cloze_susp.addWidget(self.suspend_new_clozes_cb)
        row_cloze_susp.addWidget(self._help_button("suspend_new_clozes"))
        row_cloze_susp.addStretch()
        card_sync_l.addLayout(row_cloze_susp)

        layout.addWidget(card_sync)

        # Section 3: Tag Formatting & Rules
        card_tag, card_tag_l = _create_section_card(
            "Tag Formatting",
            "Global hierarchical tag formatting for notes synced from vault folders.",
        )

        lbl_tag = QLabel("Base Tag Prefix:")
        lbl_tag.setStyleSheet("font-size: 12px; font-weight: 600; color: rgba(220, 220, 220, 0.95);")
        card_tag_l.addWidget(lbl_tag)

        tag_row = QHBoxLayout()
        tag_row.setSpacing(8)
        self.tag_edit = QLineEdit()
        self.tag_edit.setStyleSheet(input_style)
        self.tag_edit.setPlaceholderText("Optional: e.g. vaultsync")
        self.tag_edit.setMaximumWidth(360)
        _dim_placeholder(self.tag_edit)
        tag_row.addWidget(self.tag_edit)
        tag_row.addWidget(self._help_button("base_tag"))
        tag_row.addStretch()
        card_tag_l.addLayout(tag_row)

        tag_hint = QLabel("<i>Optional prefix prepended to all synced card tags (e.g. <code>vaultsync</code>).</i>")
        tag_hint.setStyleSheet("font-size: 11px; color: rgba(180, 180, 180, 0.85);")
        card_tag_l.addWidget(tag_hint)

        card_tag_l.addSpacing(8)

        self.pad_single_digits_cb = QCheckBox("Pad Single-Digit Tag Numbers (e.g. '1. Topic' → '01_Topic')")
        self.pad_single_digits_cb.setStyleSheet("font-size: 13px;")
        row_pad = QHBoxLayout()
        row_pad.setSpacing(8)
        row_pad.addWidget(self.pad_single_digits_cb)
        row_pad.addWidget(self._help_button("pad_single_digit_tags"))
        row_pad.addStretch()
        card_tag_l.addLayout(row_pad)

        pad_hint = QLabel("<i>Ensures natural sorting for numbered chapters/folders. Alphanumeric terms like 'MP3' or 'ISO9001' are protected.</i>")
        pad_hint.setStyleSheet("font-size: 11px; color: rgba(180, 180, 180, 0.85);")
        card_tag_l.addWidget(pad_hint)

        layout.addWidget(card_tag)

        # Section 4: Keyboard Shortcuts
        card_sc, card_sc_l = _create_section_card(
            "Keyboard Shortcuts",
            "Configure global keyboard shortcuts for VaultSync operations. Conflicting shortcuts are automatically disabled.",
        )

        lbl_sc_dash = QLabel("Open Dashboard:")
        lbl_sc_dash.setStyleSheet("font-size: 12px; font-weight: 600; color: rgba(220, 220, 220, 0.95);")
        card_sc_l.addWidget(lbl_sc_dash)

        row_dash = QHBoxLayout()
        row_dash.setSpacing(8)
        self.sc_dash_edit = QKeySequenceEdit()
        self.sc_dash_edit.setStyleSheet(input_style)
        self.sc_dash_edit.setMaximumWidth(260)
        btn_clear_dash = QPushButton("Clear")
        btn_clear_dash.setStyleSheet(btn_style)
        btn_clear_dash.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_clear_dash.clicked.connect(lambda: self.sc_dash_edit.setKeySequence(QKeySequence()))
        row_dash.addWidget(self.sc_dash_edit)
        row_dash.addWidget(btn_clear_dash)
        row_dash.addWidget(self._help_button("shortcuts_open_dashboard"))
        row_dash.addStretch()
        card_sc_l.addLayout(row_dash)

        self.sc_dash_warn = QLabel()
        self.sc_dash_warn.setStyleSheet("font-size: 11px; color: #e5c07b; font-weight: 500;")
        self.sc_dash_warn.setVisible(False)
        card_sc_l.addWidget(self.sc_dash_warn)

        card_sc_l.addSpacing(4)

        lbl_sc_quick = QLabel("Quick Sync (New Cards):")
        lbl_sc_quick.setStyleSheet("font-size: 12px; font-weight: 600; color: rgba(220, 220, 220, 0.95);")
        card_sc_l.addWidget(lbl_sc_quick)

        row_quick = QHBoxLayout()
        row_quick.setSpacing(8)
        self.sc_quick_edit = QKeySequenceEdit()
        self.sc_quick_edit.setStyleSheet(input_style)
        self.sc_quick_edit.setMaximumWidth(260)
        btn_clear_quick = QPushButton("Clear")
        btn_clear_quick.setStyleSheet(btn_style)
        btn_clear_quick.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_clear_quick.clicked.connect(lambda: self.sc_quick_edit.setKeySequence(QKeySequence()))
        row_quick.addWidget(self.sc_quick_edit)
        row_quick.addWidget(btn_clear_quick)
        row_quick.addWidget(self._help_button("shortcuts_quick_sync"))
        row_quick.addStretch()
        card_sc_l.addLayout(row_quick)

        self.sc_quick_warn = QLabel()
        self.sc_quick_warn.setStyleSheet("font-size: 11px; color: #e5c07b; font-weight: 500;")
        self.sc_quick_warn.setVisible(False)
        card_sc_l.addWidget(self.sc_quick_warn)

        card_sc_l.addSpacing(4)

        lbl_sc_full = QLabel("Sync and Update (Modified Cards):")
        lbl_sc_full.setStyleSheet("font-size: 12px; font-weight: 600; color: rgba(220, 220, 220, 0.95);")
        card_sc_l.addWidget(lbl_sc_full)

        row_full = QHBoxLayout()
        row_full.setSpacing(8)
        self.sc_full_edit = QKeySequenceEdit()
        self.sc_full_edit.setStyleSheet(input_style)
        self.sc_full_edit.setMaximumWidth(260)
        btn_clear_full = QPushButton("Clear")
        btn_clear_full.setStyleSheet(btn_style)
        btn_clear_full.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_clear_full.clicked.connect(lambda: self.sc_full_edit.setKeySequence(QKeySequence()))
        row_full.addWidget(self.sc_full_edit)
        row_full.addWidget(btn_clear_full)
        row_full.addWidget(self._help_button("shortcuts_full_sync"))
        row_full.addStretch()
        card_sc_l.addLayout(row_full)

        self.sc_full_warn = QLabel()
        self.sc_full_warn.setStyleSheet("font-size: 11px; color: #e5c07b; font-weight: 500;")
        self.sc_full_warn.setVisible(False)
        card_sc_l.addWidget(self.sc_full_warn)

        card_sc_l.addSpacing(6)

        row_reset = QHBoxLayout()
        btn_reset_sc = QPushButton("Reset Shortcuts to Defaults")
        btn_reset_sc.setStyleSheet(btn_style)
        btn_reset_sc.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_reset_sc.clicked.connect(self._reset_shortcuts_to_defaults)
        row_reset.addWidget(btn_reset_sc)
        row_reset.addStretch()
        card_sc_l.addLayout(row_reset)

        layout.addWidget(card_sc)
        layout.addStretch()

        self.sc_dash_edit.keySequenceChanged.connect(self._validate_shortcuts)
        self.sc_quick_edit.keySequenceChanged.connect(self._validate_shortcuts)
        self.sc_full_edit.keySequenceChanged.connect(self._validate_shortcuts)

        self._wrap_tab_in_scroll(self.tab_general, content_widget)

    def _init_tab_deck_routing(self) -> None:
        content_widget = QWidget()
        layout = QVBoxLayout(content_widget)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(16)

        combo_style = """
            QComboBox {
                font-size: 13px;
                padding: 6px 10px;
                border-radius: 6px;
                border: 1px solid rgba(255, 255, 255, 0.15);
                min-height: 24px;
                min-width: 260px;
                max-width: 400px;
            }
        """
        table_style = """
            QTableWidget {
                border: 1px solid rgba(255, 255, 255, 0.12);
                border-radius: 6px;
                font-size: 12px;
                gridline-color: rgba(255, 255, 255, 0.05);
            }
            QHeaderView::section {
                font-size: 12px;
                font-weight: bold;
                padding: 6px;
                border: none;
                border-bottom: 1px solid rgba(255, 255, 255, 0.15);
            }
        """
        btn_style = """
            QPushButton {
                font-size: 12px;
                padding: 6px 14px;
                border-radius: 6px;
                min-height: 22px;
            }
        """

        # Section 1: Fallback Default Deck
        card_def, card_def_l = _create_section_card(
            "Default Target Deck",
            "Fallback Anki deck used when a note does not match any specific routing rule.",
        )
        deck_row = QHBoxLayout()
        deck_row.setSpacing(8)
        self.deck_combo = QComboBox()
        self.deck_combo.setStyleSheet(combo_style)
        self.deck_combo.setEditable(True)
        if mw is not None and hasattr(mw, "col") and mw.col is not None:
            for d in mw.col.decks.all_names_and_ids():
                try:
                    if hasattr(mw.col.decks, "is_filtered") and mw.col.decks.is_filtered(d.id):
                        continue
                    deck = mw.col.decks.get(d.id)
                    if deck and deck.get("dyn", False):
                        continue
                except Exception:
                    pass
                self.deck_combo.addItem(d.name)
        deck_row.addWidget(self.deck_combo)
        deck_row.addWidget(self._help_button("default_deck"))
        deck_row.addStretch()
        card_def_l.addLayout(deck_row)
        layout.addWidget(card_def)

        # Section 2: Deck Routing Rules
        card_rules, card_rules_l = _create_section_card(
            "Deck Routing Rules",
            "Route vault folders and nested subdirectories directly to target Anki decks using longest prefix matching.",
        )

        self.routing_table = QTableWidget(0, 2)
        self.routing_table.setHorizontalHeaderLabels(["Vault Folder / Path", "Target Anki Deck"])
        self.routing_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.routing_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.routing_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.routing_table.setMinimumHeight(240)
        self.routing_table.setStyleSheet(table_style)
        card_rules_l.addWidget(self.routing_table)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)
        add_btn = QPushButton("Add Routing Rule")
        add_btn.setStyleSheet(btn_style)
        add_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        add_btn.clicked.connect(self._add_routing_row)

        browse_btn = QPushButton("Browse Folder...")
        browse_btn.setStyleSheet(btn_style)
        browse_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        browse_btn.clicked.connect(self._browse_routing_folder)

        remove_btn = QPushButton("Remove Selected Rule")
        remove_btn.setStyleSheet(btn_style)
        remove_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        remove_btn.clicked.connect(self._remove_routing_row)

        btn_row.addWidget(add_btn)
        btn_row.addWidget(browse_btn)
        btn_row.addWidget(remove_btn)
        btn_row.addWidget(self._help_button("deck_routing"))
        btn_row.addStretch()
        card_rules_l.addLayout(btn_row)

        rule_hint = QLabel(
            "<i>Longest prefix match applies: e.g. rules for 'everyday' and 'everyday/deep' will route notes in the deep folder to the more specific deck.</i>"
        )
        rule_hint.setStyleSheet("font-size: 11px; color: rgba(180, 180, 180, 0.85);")
        rule_hint.setWordWrap(True)
        card_rules_l.addWidget(rule_hint)

        layout.addWidget(card_rules)
        layout.addStretch()

        self._wrap_tab_in_scroll(self.tab_deck_routing, content_widget)

    def _init_tab_routing(self) -> None:
        """Backward-compatibility alias."""
        self._init_tab_deck_routing()

    def _init_tab_tag_aliasing(self) -> None:
        content_widget = QWidget()
        layout = QVBoxLayout(content_widget)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(16)

        table_style = """
            QTableWidget {
                border: 1px solid rgba(255, 255, 255, 0.12);
                border-radius: 6px;
                font-size: 12px;
                gridline-color: rgba(255, 255, 255, 0.05);
            }
            QHeaderView::section {
                font-size: 12px;
                font-weight: bold;
                padding: 6px;
                border: none;
                border-bottom: 1px solid rgba(255, 255, 255, 0.15);
            }
        """
        btn_style = """
            QPushButton {
                font-size: 12px;
                padding: 6px 14px;
                border-radius: 6px;
                min-height: 22px;
            }
        """

        card_aliases, card_aliases_l = _create_section_card(
            "Tag Path Aliases",
            "Map vault folder paths to custom hierarchical tag replacements, or leave the target alias empty to omit that folder from tags.",
        )

        self.alias_table = QTableWidget(0, 2)
        self.alias_table.setHorizontalHeaderLabels(["Vault Folder / Path", "Tag Alias / Replacement"])
        self.alias_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.alias_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.alias_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.alias_table.setMinimumHeight(280)
        self.alias_table.setStyleSheet(table_style)
        card_aliases_l.addWidget(self.alias_table)

        alias_btn_row = QHBoxLayout()
        alias_btn_row.setSpacing(8)
        add_alias_btn = QPushButton("Add Tag Alias")
        add_alias_btn.setStyleSheet(btn_style)
        add_alias_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        add_alias_btn.clicked.connect(self._add_alias_row)

        browse_alias_btn = QPushButton("Browse Folder...")
        browse_alias_btn.setStyleSheet(btn_style)
        browse_alias_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        browse_alias_btn.clicked.connect(self._browse_alias_folder)

        remove_alias_btn = QPushButton("Remove Selected Alias")
        remove_alias_btn.setStyleSheet(btn_style)
        remove_alias_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        remove_alias_btn.clicked.connect(self._remove_alias_row)

        alias_btn_row.addWidget(add_alias_btn)
        alias_btn_row.addWidget(browse_alias_btn)
        alias_btn_row.addWidget(remove_alias_btn)
        alias_btn_row.addWidget(self._help_button("tag_aliases"))
        alias_btn_row.addStretch()
        card_aliases_l.addLayout(alias_btn_row)

        alias_hint = QLabel(
            "<i>Leave 'Tag Alias / Replacement' empty to completely suppress generating tags for that folder level.<br>"
            "Global tag options (Base Tag Prefix and Single-Digit Padding) are configured under the <b>General</b> tab.</i>"
        )
        alias_hint.setStyleSheet("font-size: 11px; color: rgba(180, 180, 180, 0.85); line-height: 1.3;")
        alias_hint.setWordWrap(True)
        card_aliases_l.addWidget(alias_hint)

        layout.addWidget(card_aliases)
        layout.addStretch()

        self._wrap_tab_in_scroll(self.tab_tag_aliasing, content_widget)

    def _init_tab_rules(self) -> None:
        content_widget = QWidget()
        layout = QVBoxLayout(content_widget)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(16)

        # Section 1: Parsing & Media Flags
        card_media, card_media_l = _create_section_card(
            "Parsing & Media Options",
            "Fine-tune how cards, media references, and clozes are extracted from markdown.",
        )

        self.cloze_cb = QCheckBox("Enable Cloze Syntax Processing (==cloze==, {cloze})")
        self.cloze_cb.setStyleSheet("font-size: 13px;")
        row_cloze = QHBoxLayout()
        row_cloze.setSpacing(8)
        row_cloze.addWidget(self.cloze_cb)
        row_cloze.addWidget(self._help_button("cloze_notes"))
        row_cloze.addStretch()
        card_media_l.addLayout(row_cloze)

        self.hash_media_cb = QCheckBox("Hash Media (Rename media files to deterministic content hashes)")
        self.hash_media_cb.setStyleSheet("font-size: 13px;")
        row_hash = QHBoxLayout()
        row_hash.setSpacing(8)
        row_hash.addWidget(self.hash_media_cb)
        row_hash.addWidget(self._help_button("hash_media"))
        row_hash.addStretch()
        card_media_l.addLayout(row_hash)

        self.re_stage_cb = QCheckBox("Re-stage Already Flagged Image Occlusions")
        self.re_stage_cb.setStyleSheet("font-size: 13px;")
        row_occ = QHBoxLayout()
        row_occ.setSpacing(8)
        row_occ.addWidget(self.re_stage_cb)
        row_occ.addWidget(self._help_button("re_stage_occlusions"))
        row_occ.addStretch()
        card_media_l.addLayout(row_occ)

        layout.addWidget(card_media)

        # Section 2: Image Occlusion Settings
        card_io, card_io_l = _create_section_card(
            "Image Occlusion Settings",
            "Configure how image cloze notes are created, masked, and protected in Anki.",
        )

        lbl_io_mode = QLabel("Image Occlusion Mode:")
        lbl_io_mode.setStyleSheet("font-size: 12px; font-weight: 600; color: rgba(220, 220, 220, 0.95);")
        card_io_l.addWidget(lbl_io_mode)

        row_io_mode = QHBoxLayout()
        row_io_mode.setSpacing(8)
        self.image_occlusion_mode_combo = QComboBox()
        self.image_occlusion_mode_combo.setStyleSheet("""
            QComboBox {
                font-size: 13px;
                padding: 6px 10px;
                border-radius: 6px;
                border: 1px solid rgba(255, 255, 255, 0.15);
                min-width: 320px;
                min-height: 24px;
            }
        """)
        self.image_occlusion_mode_combo.addItem("Direct Native Image Occlusions (Recommended)", userData="native")
        self.image_occlusion_mode_combo.addItem("Folder Staging (Legacy / Manual)", userData="stage")
        row_io_mode.addWidget(self.image_occlusion_mode_combo)
        row_io_mode.addWidget(self._help_button("image_occlusion_mode"))
        row_io_mode.addStretch()
        card_io_l.addLayout(row_io_mode)

        card_io_l.addSpacing(6)

        self.suspend_new_occlusions_cb = QCheckBox("Automatically Suspend New Image Occlusion Cards Until Masked")
        self.suspend_new_occlusions_cb.setStyleSheet("font-size: 13px;")
        row_io_susp = QHBoxLayout()
        row_io_susp.setSpacing(8)
        row_io_susp.addWidget(self.suspend_new_occlusions_cb)
        row_io_susp.addWidget(self._help_button("suspend_new_occlusions"))
        row_io_susp.addStretch()
        card_io_l.addLayout(row_io_susp)

        self.freeze_image_occlusions_cb = QCheckBox("Completely Freeze Added Image Occlusion Cards (Skip Updates)")
        self.freeze_image_occlusions_cb.setStyleSheet("font-size: 13px;")
        row_io_freeze = QHBoxLayout()
        row_io_freeze.setSpacing(8)
        row_io_freeze.addWidget(self.freeze_image_occlusions_cb)
        row_io_freeze.addWidget(self._help_button("freeze_image_occlusions"))
        row_io_freeze.addStretch()
        card_io_l.addLayout(row_io_freeze)

        card_io_l.addSpacing(6)

        mask_info = QLabel(
            "<b>ℹ️ Mask Protection:</b> Once an Image Occlusion card is created, user-drawn masks and images in Anki are strictly frozen and protected from being overwritten by Markdown syncs. Deck routing and tag updates continue to sync cleanly."
        )
        mask_info.setWordWrap(True)
        mask_info.setStyleSheet(
            "background-color: rgba(33, 150, 243, 0.12); color: palette(text); border: 1px solid rgba(33, 150, 243, 0.35); "
            "border-radius: 6px; padding: 10px; font-size: 12px; line-height: 1.4;"
        )
        card_io_l.addWidget(mask_info)

        layout.addWidget(card_io)

        # Section 3: Ignore Patterns
        card_ign, card_ign_l = _create_section_card(
            "Ignored Directories & Files",
            "Folders or glob patterns to skip during vault scanning (one per line, e.g. .obsidian, templates/*, archive).",
        )

        self.ignore_edit = QPlainTextEdit()
        self.ignore_edit.setPlaceholderText("Optional: Folders or globs to ignore (e.g. .obsidian, templates/*, archive)")
        _dim_placeholder(self.ignore_edit)
        self.ignore_edit.setMinimumHeight(140)
        self.ignore_edit.setMaximumHeight(220)
        self.ignore_edit.setStyleSheet("""
            QPlainTextEdit {
                font-family: monospace;
                font-size: 12px;
                border: 1px solid rgba(255, 255, 255, 0.12);
                border-radius: 6px;
                padding: 8px;
            }
            QPlainTextEdit::placeholder {
                color: rgba(140, 140, 140, 0.65);
                font-style: italic;
            }
        """)
        card_ign_l.addWidget(self.ignore_edit)

        layout.addWidget(card_ign)
        layout.addStretch()

        self._wrap_tab_in_scroll(self.tab_rules, content_widget)

    def _init_tab_advanced(self) -> None:
        content_widget = QWidget()
        layout = QVBoxLayout(content_widget)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(16)

        combo_style = """
            QComboBox {
                font-size: 13px;
                padding: 6px 10px;
                border-radius: 6px;
                border: 1px solid rgba(255, 255, 255, 0.15);
                min-width: 260px;
                max-width: 400px;
                min-height: 24px;
            }
        """
        btn_style = """
            QPushButton {
                font-size: 13px;
                padding: 6px 16px;
                border-radius: 6px;
                min-height: 24px;
            }
        """

        # Section 1: Note Type Bindings
        card_models, card_models_l = _create_section_card(
            "Custom Note Type Bindings",
            "Optionally bind VaultSync cards to existing Anki Note Types instead of default templates.",
        )

        lbl_basic = QLabel("Basic Note Type:")
        lbl_basic.setStyleSheet("font-size: 12px; font-weight: 600; color: rgba(220, 220, 220, 0.95);")
        card_models_l.addWidget(lbl_basic)

        b_row = QHBoxLayout()
        b_row.setSpacing(8)
        self.basic_model_combo = QComboBox()
        self.basic_model_combo.setStyleSheet(combo_style)
        self.basic_model_combo.addItem("Default (M2A Basic)", userData=None)
        if mw is not None and hasattr(mw, "col") and mw.col is not None:
            for m in mw.col.models.all():
                if m.get("type", 0) == 0:  # standard model
                    self.basic_model_combo.addItem(m["name"], userData=int(m["id"]))
        b_row.addWidget(self.basic_model_combo)
        b_row.addWidget(self._help_button("basic_model_id"))
        b_row.addStretch()
        card_models_l.addLayout(b_row)

        card_models_l.addSpacing(6)

        lbl_cloze = QLabel("Cloze Note Type:")
        lbl_cloze.setStyleSheet("font-size: 12px; font-weight: 600; color: rgba(220, 220, 220, 0.95);")
        card_models_l.addWidget(lbl_cloze)

        c_row = QHBoxLayout()
        c_row.setSpacing(8)
        self.cloze_model_combo = QComboBox()
        self.cloze_model_combo.setStyleSheet(combo_style)
        self.cloze_model_combo.addItem("Default (M2A Cloze)", userData=None)
        if mw is not None and hasattr(mw, "col") and mw.col is not None:
            for m in mw.col.models.all():
                if m.get("type", 1) == 1:  # cloze model
                    self.cloze_model_combo.addItem(m["name"], userData=int(m["id"]))
        c_row.addWidget(self.cloze_model_combo)
        c_row.addWidget(self._help_button("cloze_model_id"))
        c_row.addStretch()
        card_models_l.addLayout(c_row)

        layout.addWidget(card_models)

        # Section 2: Maintenance Tools
        card_tools, card_tools_l = _create_section_card(
            "Maintenance & Diagnostic Tools",
            "Actions to inspect, repair, or clean up your Anki card collection.",
        )

        repair_btn = QPushButton("Repair / Re-apply M2A Note Type Templates")
        repair_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        repair_btn.setStyleSheet(btn_style)
        repair_btn.clicked.connect(self._repair_templates)
        row_rep = QHBoxLayout()
        row_rep.setSpacing(8)
        row_rep.addWidget(repair_btn)
        row_rep.addWidget(self._help_button("repair_templates"))
        row_rep.addStretch()
        card_tools_l.addLayout(row_rep)

        self.orphans_btn = QPushButton("Find & View Orphaned Cards in Browser")
        self.orphans_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.orphans_btn.setStyleSheet(btn_style)
        self.orphans_btn.clicked.connect(self._view_orphans_in_browser)
        row_orph = QHBoxLayout()
        row_orph.setSpacing(8)
        row_orph.addWidget(self.orphans_btn)
        row_orph.addWidget(self._help_button("orphaned_cards"))
        row_orph.addStretch()
        card_tools_l.addLayout(row_orph)

        layout.addWidget(card_tools)
        layout.addStretch()

        self._wrap_tab_in_scroll(self.tab_advanced, content_widget)

    def _init_tab_maintenance(self) -> None:
        content_widget = QWidget()
        layout = QVBoxLayout(content_widget)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(16)

        btn_style = """
            QPushButton {
                font-size: 13px;
                padding: 6px 14px;
                border-radius: 6px;
                min-height: 24px;
            }
        """
        table_style = """
            QTableWidget {
                border: 1px solid rgba(255, 255, 255, 0.12);
                border-radius: 6px;
                gridline-color: rgba(255, 255, 255, 0.06);
                font-size: 12px;
            }
            QHeaderView::section {
                background: rgba(255, 255, 255, 0.05);
                padding: 6px;
                border: none;
                font-size: 12px;
                font-weight: 600;
            }
        """

        # Section 1: Orphaned Cards in Anki (Deleted from Obsidian)
        card_orph, card_orph_l = _create_section_card(
            "Orphaned Cards in Anki (Deleted from Obsidian)",
            "Cards present in Anki under VaultSync note types whose source was removed from your vault.",
        )

        self.orphans_status_label = QLabel("0 orphaned note(s) detected in Anki.")
        self.orphans_status_label.setStyleSheet("font-size: 13px; font-weight: 600;")
        card_orph_l.addWidget(self.orphans_status_label)

        orph_btns = QHBoxLayout()
        orph_btns.setSpacing(8)

        self.view_orphans_btn = QPushButton("View in Anki Browser")
        self.view_orphans_btn.setStyleSheet(btn_style)
        self.view_orphans_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.view_orphans_btn.clicked.connect(self._on_view_orphans)

        self.suspend_orphans_btn = QPushButton("Suspend All Orphaned Cards")
        self.suspend_orphans_btn.setStyleSheet(btn_style)
        self.suspend_orphans_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.suspend_orphans_btn.setToolTip("Suspend all cards of orphaned notes to preserve review history and intervals without active reviews.")
        self.suspend_orphans_btn.clicked.connect(self._on_suspend_orphans)

        self.delete_orphans_btn = QPushButton("Delete Permanently...")
        self.delete_orphans_btn.setStyleSheet(btn_style + " QPushButton { color: #ff6b6b; }")
        self.delete_orphans_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.delete_orphans_btn.setToolTip("Permanently delete orphaned notes from Anki database. Review history will be lost.")
        self.delete_orphans_btn.clicked.connect(self._on_delete_orphans)

        orph_btns.addWidget(self.view_orphans_btn)
        orph_btns.addWidget(self.suspend_orphans_btn)
        orph_btns.addWidget(self.delete_orphans_btn)
        orph_btns.addWidget(self._help_button("orphaned_cards"))
        orph_btns.addStretch()
        card_orph_l.addLayout(orph_btns)

        layout.addWidget(card_orph)

        # Section 2: Cards Deleted in Anki (Still Active in Obsidian)
        card_del, card_del_l = _create_section_card(
            "Cards Deleted in Anki (Still Active in Obsidian)",
            "Cards that have sync IDs in Obsidian but no longer exist in Anki. By default, they will never be recreated automatically.",
        )

        self.missing_status_label = QLabel("0 card(s) deleted in Anki.")
        self.missing_status_label.setStyleSheet("font-size: 13px; font-weight: 600;")
        card_del_l.addWidget(self.missing_status_label)

        self.missing_table = QTableWidget(0, 4)
        self.missing_table.setHorizontalHeaderLabels(["", "File", "Line", "Question / Preview"])
        self.missing_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.missing_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Interactive)
        self.missing_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.missing_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self.missing_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.missing_table.setMinimumHeight(240)
        self.missing_table.setStyleSheet(table_style)
        card_del_l.addWidget(self.missing_table)

        table_ctrl_row = QHBoxLayout()
        table_ctrl_row.setSpacing(8)

        sel_all_btn = QPushButton("Select All")
        sel_all_btn.setStyleSheet(btn_style)
        sel_all_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        sel_all_btn.clicked.connect(lambda: self._on_select_all_missing(True))

        desel_all_btn = QPushButton("Deselect All")
        desel_all_btn.setStyleSheet(btn_style)
        desel_all_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        desel_all_btn.clicked.connect(lambda: self._on_select_all_missing(False))

        table_ctrl_row.addWidget(sel_all_btn)
        table_ctrl_row.addWidget(desel_all_btn)
        table_ctrl_row.addStretch()
        card_del_l.addLayout(table_ctrl_row)

        action_row = QHBoxLayout()
        action_row.setSpacing(8)

        self.mark_deleted_btn = QPushButton("Mark Selected as DELETED in Obsidian")
        self.mark_deleted_btn.setStyleSheet(btn_style)
        self.mark_deleted_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.mark_deleted_btn.setToolTip("Replaces ADDED: ... %%id%% with DELETED: ... %%id%% so the parser permanently ignores them during regular sync.")
        self.mark_deleted_btn.clicked.connect(self._on_mark_missing_deleted)

        self.resync_missing_btn = QPushButton("Resync / Recreate Selected in Anki")
        self.resync_missing_btn.setStyleSheet(btn_style)
        self.resync_missing_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.resync_missing_btn.setToolTip("Re-creates the selected cards in Anki using their existing IDs.")
        self.resync_missing_btn.clicked.connect(self._on_resync_missing)

        refresh_maint_btn = QPushButton("↻ Refresh Scan")
        refresh_maint_btn.setStyleSheet(btn_style)
        refresh_maint_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        refresh_maint_btn.clicked.connect(self._refresh_maintenance_data)

        action_row.addWidget(self.mark_deleted_btn)
        action_row.addWidget(self.resync_missing_btn)
        action_row.addWidget(refresh_maint_btn)
        action_row.addWidget(self._help_button("missing_in_anki"))
        action_row.addStretch()
        card_del_l.addLayout(action_row)

        layout.addWidget(card_del)
        layout.addStretch()

        self._wrap_tab_in_scroll(self.tab_maintenance, content_widget)

    def set_maintenance_plan(self, plan: SyncPlan) -> None:
        self._current_orphans = list(plan.orphans)
        self._current_missing = list(plan.missing_in_anki)

        # Section 1: Orphans
        count_orphans = len(self._current_orphans)
        self.orphans_status_label.setText(f"{count_orphans} orphaned note(s) detected in Anki.")
        self.view_orphans_btn.setEnabled(count_orphans > 0)
        self.suspend_orphans_btn.setEnabled(count_orphans > 0)
        self.delete_orphans_btn.setEnabled(count_orphans > 0)

        # Section 2: Missing in Anki
        count_missing = len(self._current_missing)
        self.missing_status_label.setText(
            f"{count_missing} card(s) detected with sync IDs in vault that no longer exist in Anki."
        )
        self.missing_table.setRowCount(count_missing)
        for row, rec in enumerate(self._current_missing):
            chk_item = QTableWidgetItem()
            chk_item.setFlags(Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled)
            chk_item.setCheckState(Qt.CheckState.Checked)
            self.missing_table.setItem(row, 0, chk_item)

            file_item = QTableWidgetItem(rec.file_path.name)
            file_item.setToolTip(str(rec.file_path))
            file_item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
            self.missing_table.setItem(row, 1, file_item)

            line_item = QTableWidgetItem(str(rec.line))
            line_item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
            self.missing_table.setItem(row, 2, line_item)

            preview_text = rec.preview_question.replace("\n", " ").strip()
            q_item = QTableWidgetItem(preview_text)
            q_item.setToolTip(rec.preview_question)
            q_item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
            self.missing_table.setItem(row, 3, q_item)

        self.mark_deleted_btn.setEnabled(count_missing > 0)
        self.resync_missing_btn.setEnabled(count_missing > 0)

    def _set_maintenance_buttons_enabled(self, enabled: bool) -> None:
        self.view_orphans_btn.setEnabled(enabled and len(self._current_orphans) > 0)
        self.suspend_orphans_btn.setEnabled(enabled and len(self._current_orphans) > 0)
        self.delete_orphans_btn.setEnabled(enabled and len(self._current_orphans) > 0)
        self.mark_deleted_btn.setEnabled(enabled and len(self._current_missing) > 0)
        self.resync_missing_btn.setEnabled(enabled and len(self._current_missing) > 0)

    def _reset_maintenance_ui(self) -> None:
        self.orphans_status_label.setText("0 orphaned note(s) detected in Anki.")
        self.missing_status_label.setText("0 card(s) detected with sync IDs in vault that no longer exist in Anki.")
        self.missing_table.setRowCount(0)
        self._set_maintenance_buttons_enabled(False)

    def _refresh_maintenance_data(self) -> None:
        if (
            mw is None
            or getattr(mw, "col", None) is None
            or not self._profile_settings.vault_path
            or not self._profile_settings.vault_path.is_dir()
        ):
            self._reset_maintenance_ui()
            return

        from ..sync_lock import acquire_sync_lock, release_sync_lock
        if not acquire_sync_lock():
            self.orphans_status_label.setText("Another operation is running. Please refresh later.")
            return

        self.orphans_status_label.setText("Scanning for maintenance data in background...")
        self.missing_status_label.setText("Scanning vault...")
        self._set_maintenance_buttons_enabled(False)

        vault_p = self._profile_settings.vault_path
        deck_name = self._default_deck_name
        b_mid = self._profile_settings.basic_model_id
        c_mid = self._profile_settings.cloze_model_id

        def _op(col) -> SyncPlan:
            return build_sync_plan(
                col,
                vault_p,
                default_deck=deck_name,
                basic_model_id=b_mid,
                cloze_model_id=c_mid,
            )

        def _on_success(plan: SyncPlan) -> None:
            release_sync_lock()
            if _is_deleted(self):
                return
            self.set_maintenance_plan(plan)

        def _on_failure(exc: Exception) -> None:
            release_sync_lock()
            if _is_deleted(self):
                return
            log_error(f"Failed to refresh maintenance data: {exc}")
            self._reset_maintenance_ui()

        import aqt
        if getattr(aqt, "mw", None) is None:
            try:
                res = _op(getattr(mw, "col", None))
                _on_success(res)
            except Exception as exc:
                _on_failure(exc)
            return

        QueryOp(
            parent=self,
            op=_op,
            success=_on_success,
        ).failure(_on_failure).run_in_background()

    def _on_view_orphans(self) -> None:
        if not self._current_orphans:
            return
        nids = [str(o.note_id) for o in self._current_orphans]
        query = f"nid:{",".join(nids)}"
        browser = dialogs.open("Browser", mw)
        browser.search_for(query)
        browser.activateWindow()
        browser.raise_()
        self.lower()

    def _on_suspend_orphans(self) -> None:
        if not self._current_orphans or mw is None or mw.col is None:
            return
        all_cids: list[int] = []
        for o in self._current_orphans:
            if hasattr(mw.col, "card_ids_of_note"):
                all_cids.extend(mw.col.card_ids_of_note(o.note_id))
            else:
                note = mw.col.get_note(o.note_id)
                all_cids.extend([c.id for c in note.cards()])
        if all_cids:
            mw.col.sched.suspend_cards(all_cids)
            if hasattr(mw, "reset"):
                mw.reset()
        QToolTip.showText(QCursor.pos(), f"{len(self._current_orphans)} orphaned note(s) ({len(all_cids)} card(s)) suspended.", self)
        self.orphans_status_label.setText(f"{len(self._current_orphans)} orphaned note(s) suspended.")

    def _on_delete_orphans(self) -> None:
        if not self._current_orphans or mw is None or mw.col is None:
            return
        from ..sync_lock import acquire_sync_lock, release_sync_lock
        if not acquire_sync_lock():
            QToolTip.showText(QCursor.pos(), "Another operation is in progress.", self)
            return

        count = len(self._current_orphans)
        reply = QMessageBox.warning(
            self,
            "Delete Orphaned Cards?",
            f"Are you sure you want to permanently delete {count} orphaned note(s) from Anki?\n\n"
            "All review history, learning intervals, and statistics for these cards will be lost.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if reply != QMessageBox.StandardButton.Yes:
            release_sync_lock()
            return

        from anki.notes import NoteId
        nids = [NoteId(o.note_id) for o in self._current_orphans]

        def _op(col) -> int:
            col.remove_notes(nids)
            return count

        def _on_success(c: int) -> None:
            release_sync_lock()
            if mw is not None and hasattr(mw, "reset"):
                mw.reset()
            if _is_deleted(self):
                return
            QToolTip.showText(QCursor.pos(), f"{c} orphaned note(s) deleted permanently.", self)
            self._refresh_maintenance_data()

        def _on_failure(exc: Exception) -> None:
            release_sync_lock()
            if _is_deleted(self):
                return
            log_error(f"Failed to delete orphaned notes: {exc}")
            QMessageBox.critical(self, "Error", f"Failed to delete orphaned notes: {exc}")
            self._refresh_maintenance_data()

        import aqt
        if getattr(aqt, "mw", None) is None:
            try:
                res = _op(getattr(mw, "col", None))
                _on_success(res)
            except Exception as exc:
                _on_failure(exc)
            return

        QueryOp(parent=self, op=_op, success=_on_success).failure(_on_failure).with_progress("Deleting orphaned notes...").run_in_background()

    def _on_select_all_missing(self, select: bool) -> None:
        for row in range(self.missing_table.rowCount()):
            item = self.missing_table.item(row, 0)
            if item:
                item.setCheckState(Qt.CheckState.Checked if select else Qt.CheckState.Unchecked)

    def _get_selected_missing_records(self) -> list[MissingInAnkiRecord]:
        selected: list[MissingInAnkiRecord] = []
        for row in range(self.missing_table.rowCount()):
            item = self.missing_table.item(row, 0)
            if item and item.checkState() == Qt.CheckState.Checked:
                if row < len(self._current_missing):
                    selected.append(self._current_missing[row])
        return selected

    def _on_mark_missing_deleted(self) -> None:
        selected = self._get_selected_missing_records()
        if not selected:
            QMessageBox.information(self, "No Cards Selected", "Please select at least one card to mark as DELETED in Obsidian.")
            return
        cards_to_flag = [rec.card for rec in selected if rec.card is not None]
        if cards_to_flag:
            flagged, problems = write_deleted_flags(cards_to_flag)
            if problems:
                QMessageBox.warning(self, "Some Cards Could Not Be Marked", "\n".join(problems[:10]))
            QToolTip.showText(QCursor.pos(), f"Marked {flagged} card(s) as DELETED in Obsidian.", self)
        self._refresh_maintenance_data()

    def _on_resync_missing(self) -> None:
        selected = self._get_selected_missing_records()
        if not selected:
            QMessageBox.information(self, "No Cards Selected", "Please select at least one card to resync to Anki.")
            return
        if mw is None or mw.col is None or not self._profile_settings.vault_path:
            return
        from ..sync_lock import acquire_sync_lock, release_sync_lock
        if not acquire_sync_lock():
            QToolTip.showText(QCursor.pos(), "Another operation is in progress.", self)
            return

        selected_ids = {rec.card_id for rec in selected}
        vault_p = self._profile_settings.vault_path
        deck_name = self._default_deck_name
        b_mid = self._profile_settings.basic_model_id
        c_mid = self._profile_settings.cloze_model_id
        occ_dir = self._profile_settings.occlusion_dir

        self._set_maintenance_buttons_enabled(False)

        def _op(col) -> SyncReport:
            plan = build_sync_plan(
                col,
                vault_p,
                default_deck=deck_name,
                basic_model_id=b_mid,
                cloze_model_id=c_mid,
                recreate_missing_ids=selected_ids,
            )
            return execute_sync_plan(
                col,
                plan,
                include_updates=False,
                occlusion_dir=occ_dir,
                basic_model_id=b_mid,
                cloze_model_id=c_mid,
            )

        def _on_success(report: SyncReport) -> None:
            release_sync_lock()
            if mw is not None:
                if hasattr(mw, "update_undo_actions"):
                    mw.update_undo_actions()
                if hasattr(mw, "reset"):
                    mw.reset()
            if _is_deleted(self):
                return
            QToolTip.showText(QCursor.pos(), f"Recreated {report.created_count} card(s) in Anki.", self)
            self._refresh_maintenance_data()

        def _on_failure(exc: Exception) -> None:
            release_sync_lock()
            if _is_deleted(self):
                return
            log_error(f"Failed to resync missing cards: {exc}")
            QMessageBox.critical(self, "Resync Failed", f"An error occurred while recreating cards:\n\n{exc}")
            self._refresh_maintenance_data()

        import aqt
        if getattr(aqt, "mw", None) is None:
            try:
                res = _op(getattr(mw, "col", None))
                _on_success(res)
            except Exception as exc:
                _on_failure(exc)
            return

        QueryOp(parent=self, op=_op, success=_on_success).failure(_on_failure).with_progress("Recreating cards in Anki...").run_in_background()

    def _init_tab_logs(self) -> None:
        layout = QVBoxLayout(self.tab_logs)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        # Header with actions
        header = QHBoxLayout()
        header.setSpacing(10)
        desc = QLabel("Operation history across sessions (<b>newest entries on top</b>):")
        desc.setStyleSheet("font-size: 13px;")
        header.addWidget(desc)
        header.addStretch()

        open_folder_btn = QPushButton("Open Logs Folder")
        open_folder_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        open_folder_btn.setStyleSheet("QPushButton { font-size: 12px; padding: 5px 12px; border-radius: 4px; }")
        open_folder_btn.clicked.connect(self._open_log_folder)
        header.addWidget(open_folder_btn)

        clear_btn = QPushButton("Clear Logs")
        clear_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        clear_btn.setStyleSheet("QPushButton { font-size: 12px; padding: 5px 12px; border-radius: 4px; }")
        clear_btn.clicked.connect(self._clear_logs)
        header.addWidget(clear_btn)
        layout.addLayout(header)

        # Log content display
        self.log_viewer = QPlainTextEdit()
        self.log_edit = self.log_viewer
        self.log_viewer.setReadOnly(True)
        self.log_viewer.setStyleSheet("""
            QPlainTextEdit {
                font-family: monospace;
                font-size: 12px;
                border: 1px solid rgba(255, 255, 255, 0.12);
                border-radius: 6px;
                background-color: rgba(0, 0, 0, 0.2);
                padding: 10px;
            }
        """)
        layout.addWidget(self.log_viewer)

        self._refresh_logs()

    def _open_log_folder(self) -> None:
        log_path = get_log_file_path()
        if log_path.exists():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(log_path.parent)))
        else:
            QMessageBox.information(self, "Logs", "Log folder does not exist yet.")

    def _clear_logs(self) -> None:
        reply = QMessageBox.question(
            self,
            "Clear Logs",
            "Are you sure you want to clear the entire log file history?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            clear_log()
            self._refresh_logs()

    def _refresh_logs(self) -> None:
        log_text = read_log_text(newest_first=True, max_lines=1000)
        if not log_text.strip():
            self.log_viewer.setPlainText("(No log records recorded yet)")
        else:
            self.log_viewer.setPlainText(log_text)

    def _view_orphans_in_browser(self) -> None:
        if mw is None or not hasattr(mw, "col") or mw.col is None:
            QMessageBox.warning(self, "Unavailable", "Anki collection is not currently open.")
            return

        vault_str = self.vault_edit.text().strip()
        vault_path = Path(vault_str).expanduser().resolve() if vault_str else None
        if not vault_path or not vault_path.is_dir():
            QMessageBox.information(
                self, "Vault Not Configured", "Please select a valid Obsidian vault directory first."
            )
            return

        basic_id = self._get_selected_model_id(self.basic_model_combo)
        cloze_id = self._get_selected_model_id(self.cloze_model_combo)

        def _op(col):
            return build_sync_plan(col, vault_path, basic_model_id=basic_id, cloze_model_id=cloze_id)

        def _success(plan):
            if _is_deleted(self):
                return
            if not plan.orphans:
                QMessageBox.information(
                    self, "No Orphans Found", "All cards in Anki match cards in your Obsidian vault. Zero orphaned cards!"
                )
                return

            nids = [str(o.note_id) for o in plan.orphans]
            query = f"nid:{','.join(nids)}"
            browser = dialogs.open("Browser", mw)
            browser.search_for(query)
            browser.activateWindow()
            browser.raise_()
            self.lower()

        def _failure(exc):
            if _is_deleted(self):
                return
            QMessageBox.critical(self, "Error", f"Failed to detect orphaned cards: {exc}")

        QueryOp(parent=self, op=_op, success=_success).failure(_failure).with_progress("Scanning for orphaned cards...").run_in_background()

    def _get_selected_model_id(self, combo: QComboBox) -> int | None:
        data = combo.currentData()
        if data is not None and isinstance(data, int):
            return data
        text = combo.currentText().strip()
        if not text or text.startswith("Default"):
            return None
        id_match = re.search(r"\(ID:\s*(\d+)\)", text)
        if id_match:
            return int(id_match.group(1))
        if text.isdigit():
            return int(text)
        if mw is not None and hasattr(mw, "col") and mw.col is not None:
            try:
                m = mw.col.models.by_name(text)
                if m:
                    return int(m["id"])
            except Exception:
                pass
        return None

    def _browse_vault(self) -> None:
        dir_path = QFileDialog.getExistingDirectory(self, "Select Obsidian Vault Folder")
        if dir_path:
            self.vault_edit.setText(dir_path)
            p = Path(dir_path)
            if (p / "m2a.toml").is_file():
                try:
                    self._vault_cfg = self._manager.load_vault(p)
                    self._populate_vault_fields()
                except Exception:
                    pass

    def _browse_occlusion(self) -> None:
        dir_path = QFileDialog.getExistingDirectory(self, "Select Image Occlusion Folder")
        if dir_path:
            self.occlusion_edit.setText(dir_path)

    def _browse_routing_folder(self) -> None:
        curr = self.routing_table.currentRow()
        if curr < 0:
            return
        vault_str = self.vault_edit.text().strip()
        start_dir = vault_str if vault_str and Path(vault_str).is_dir() else None
        dir_path = QFileDialog.getExistingDirectory(self, "Select Vault Folder for Routing", start_dir or "")
        if dir_path:
            p = Path(dir_path)
            if start_dir:
                try:
                    rel = p.relative_to(Path(start_dir)).as_posix()
                    self.routing_table.setItem(curr, 0, QTableWidgetItem(rel))
                    return
                except ValueError:
                    pass
            self.routing_table.setItem(curr, 0, QTableWidgetItem(p.name))

    def _browse_alias_folder(self) -> None:
        curr = self.alias_table.currentRow()
        if curr < 0:
            return
        vault_str = self.vault_edit.text().strip()
        start_dir = vault_str if vault_str and Path(vault_str).is_dir() else None
        dir_path = QFileDialog.getExistingDirectory(self, "Select Vault Folder for Tag Alias", start_dir or "")
        if dir_path:
            p = Path(dir_path)
            if start_dir:
                try:
                    rel = p.relative_to(Path(start_dir)).as_posix()
                    self.alias_table.setItem(curr, 0, QTableWidgetItem(rel))
                    return
                except ValueError:
                    pass
            self.alias_table.setItem(curr, 0, QTableWidgetItem(p.name))

    def _add_routing_row(self) -> None:
        row = self.routing_table.rowCount()
        self.routing_table.insertRow(row)
        self.routing_table.setItem(row, 0, QTableWidgetItem("folder_path"))
        self.routing_table.setItem(row, 1, QTableWidgetItem("Target Deck"))

    def _remove_routing_row(self) -> None:
        curr = self.routing_table.currentRow()
        if curr >= 0:
            self.routing_table.removeRow(curr)

    def _add_alias_row(self) -> None:
        row = self.alias_table.rowCount()
        self.alias_table.insertRow(row)
        self.alias_table.setItem(row, 0, QTableWidgetItem("folder_path"))
        self.alias_table.setItem(row, 1, QTableWidgetItem("tag_alias"))

    def _remove_alias_row(self) -> None:
        curr = self.alias_table.currentRow()
        if curr >= 0:
            self.alias_table.removeRow(curr)

    def _repair_templates(self) -> None:
        if mw is None or not hasattr(mw, "col") or mw.col is None:
            QMessageBox.warning(self, "Unavailable", "Anki collection is not currently open.")
            return

        basic_id = self._get_selected_model_id(self.basic_model_combo)
        cloze_id = self._get_selected_model_id(self.cloze_model_combo)

        def _op(col):
            ensure_models(
                col,
                repair_templates=True,
                basic_model_id=basic_id,
                cloze_model_id=cloze_id,
            )
# Saving is automatic in modern Anki

        def _success(_):
            if _is_deleted(self):
                return
            mw.reset()
            QMessageBox.information(self, "Success", "Note type templates repaired successfully.")

        def _failure(exc):
            if _is_deleted(self):
                return
            QMessageBox.critical(self, "Error", f"Failed to repair templates: {exc}")

        QueryOp(parent=self, op=_op, success=_success).failure(_failure).with_progress("Repairing templates...").run_in_background()

    def _reset_shortcuts_to_defaults(self) -> None:
        self.sc_dash_edit.setKeySequence(QKeySequence(DEFAULT_SHORTCUTS["open_dashboard"]))
        self.sc_quick_edit.setKeySequence(QKeySequence(DEFAULT_SHORTCUTS["quick_sync"]))
        self.sc_full_edit.setKeySequence(QKeySequence(DEFAULT_SHORTCUTS["full_sync"]))
        self._validate_shortcuts()

    def _validate_shortcuts(self) -> None:
        from .menu import get_menu_actions
        menu_actions = get_menu_actions()
        allowed = {act for act in menu_actions.values() if act is not None}

        fields = [
            ("Open Dashboard", self.sc_dash_edit, self.sc_dash_warn),
            ("Quick Sync", self.sc_quick_edit, self.sc_quick_warn),
            ("Sync and Update", self.sc_full_edit, self.sc_full_warn),
        ]

        for i, (name, edit, warn_lbl) in enumerate(fields):
            seq = edit.keySequence()
            if seq.isEmpty():
                warn_lbl.setVisible(False)
                continue

            seq_str = seq.toString(QKeySequence.SequenceFormat.PortableText)
            seq_canon = seq_str.casefold()

            # Check mutual conflict with other fields in this dialog
            mutual_conflict = None
            for j, (other_name, other_edit, _) in enumerate(fields):
                if i != j:
                    other_seq = other_edit.keySequence()
                    if not other_seq.isEmpty() and other_seq.toString(QKeySequence.SequenceFormat.PortableText).casefold() == seq_canon:
                        mutual_conflict = other_name
                        break

            if mutual_conflict:
                warn_lbl.setText(f"⚠️ Conflicts with '{mutual_conflict}'. Shortcut will be disabled if saved.")
                warn_lbl.setVisible(True)
                continue

            # Check conflict with external Anki or add-on actions
            conflict = find_shortcut_conflict(seq_str, allowed_actions=allowed)
            if conflict:
                warn_lbl.setText(f"⚠️ Already taken by '{conflict}'. Shortcut will be disabled if saved.")
                warn_lbl.setVisible(True)
            else:
                warn_lbl.setVisible(False)

    def _populate_fields(self) -> None:
        if self._profile_settings.vault_path:
            self.vault_edit.setText(str(self._profile_settings.vault_path))
        if self._profile_settings.occlusion_dir:
            self.occlusion_edit.setText(str(self._profile_settings.occlusion_dir))

        self.show_diff_cb.setChecked(self._profile_settings.show_diff_before_update)
        self.auto_close_cb.setChecked(self._profile_settings.auto_close_on_success)
        self.sync_on_startup_cb.setChecked(self._profile_settings.sync_on_startup)

        # Select basic model
        if self._profile_settings.basic_model_id is not None:
            found = False
            for i in range(self.basic_model_combo.count()):
                if self.basic_model_combo.itemData(i) == self._profile_settings.basic_model_id:
                    self.basic_model_combo.setCurrentIndex(i)
                    found = True
                    break
            if not found:
                name = None
                model_deleted = False
                if mw is not None and hasattr(mw, "col") and mw.col is not None:
                    try:
                        m = mw.col.models.get(self._profile_settings.basic_model_id)
                        if m is not None:
                            name = m["name"]
                        else:
                            model_deleted = True
                    except Exception:
                        pass
                if model_deleted:
                    self.basic_model_combo.setCurrentIndex(0)
                else:
                    display_text = name or f"Note Type ({self._profile_settings.basic_model_id})"
                    self.basic_model_combo.addItem(display_text, userData=self._profile_settings.basic_model_id)
                    self.basic_model_combo.setCurrentIndex(self.basic_model_combo.count() - 1)
        else:
            self.basic_model_combo.setCurrentIndex(0)

        # Select cloze model
        if self._profile_settings.cloze_model_id is not None:
            found = False
            for i in range(self.cloze_model_combo.count()):
                if self.cloze_model_combo.itemData(i) == self._profile_settings.cloze_model_id:
                    self.cloze_model_combo.setCurrentIndex(i)
                    found = True
                    break
            if not found:
                name = None
                model_deleted = False
                if mw is not None and hasattr(mw, "col") and mw.col is not None:
                    try:
                        m = mw.col.models.get(self._profile_settings.cloze_model_id)
                        if m is not None:
                            name = m["name"]
                        else:
                            model_deleted = True
                    except Exception:
                        pass
                if model_deleted:
                    self.cloze_model_combo.setCurrentIndex(0)
                else:
                    display_text = name or f"Note Type ({self._profile_settings.cloze_model_id})"
                    self.cloze_model_combo.addItem(display_text, userData=self._profile_settings.cloze_model_id)
                    self.cloze_model_combo.setCurrentIndex(self.cloze_model_combo.count() - 1)
        else:
            self.cloze_model_combo.setCurrentIndex(0)

        shortcuts = self._profile_settings.shortcuts or {}
        self.sc_dash_edit.setKeySequence(
            QKeySequence(shortcuts.get("open_dashboard", DEFAULT_SHORTCUTS["open_dashboard"]))
        )
        self.sc_quick_edit.setKeySequence(
            QKeySequence(shortcuts.get("quick_sync", DEFAULT_SHORTCUTS["quick_sync"]))
        )
        self.sc_full_edit.setKeySequence(
            QKeySequence(shortcuts.get("full_sync", DEFAULT_SHORTCUTS["full_sync"]))
        )
        self._validate_shortcuts()

        self._populate_vault_fields()

    def _populate_vault_fields(self) -> None:
        if self._vault_cfg is None:
            self.pad_single_digits_cb.setChecked(True)
            self.hash_media_cb.setChecked(True)
            self.cloze_cb.setChecked(True)
            self.suspend_new_clozes_cb.setChecked(False)
            self.image_occlusion_mode_combo.setCurrentIndex(0)
            self.suspend_new_occlusions_cb.setChecked(True)
            self.freeze_image_occlusions_cb.setChecked(False)
            return

        self.deck_combo.setCurrentText(self._vault_cfg.deck)
        self.tag_edit.setText(self._vault_cfg.base_tag)
        self.pad_single_digits_cb.setChecked(self._vault_cfg.pad_single_digit_tags)
        self.hash_media_cb.setChecked(self._vault_cfg.hash_media)
        self.cloze_cb.setChecked(self._vault_cfg.cloze_notes)
        self.suspend_new_clozes_cb.setChecked(getattr(self._vault_cfg, "suspend_new_clozes", False))

        mode = getattr(self._vault_cfg, "image_occlusion_mode", "native")
        self.image_occlusion_mode_combo.setCurrentIndex(1 if mode == "stage" else 0)
        self.suspend_new_occlusions_cb.setChecked(getattr(self._vault_cfg, "suspend_new_occlusions", True))
        self.freeze_image_occlusions_cb.setChecked(getattr(self._vault_cfg, "freeze_image_occlusions", False))

        self.re_stage_occlusions = self._vault_cfg.re_stage_occlusions
        self.re_stage_cb.setChecked(self._vault_cfg.re_stage_occlusions)

        # Deck routing
        routing = self._vault_cfg.deck_routing or {}
        self.routing_table.setRowCount(len(routing))
        for r, (folder, target_deck) in enumerate(routing.items()):
            self.routing_table.setItem(r, 0, QTableWidgetItem(folder))
            self.routing_table.setItem(r, 1, QTableWidgetItem(target_deck))

        # Tag aliases
        aliases = self._vault_cfg.tag_aliases or {}
        self.alias_table.setRowCount(len(aliases))
        for r, (folder, alias) in enumerate(aliases.items()):
            self.alias_table.setItem(r, 0, QTableWidgetItem(folder))
            self.alias_table.setItem(r, 1, QTableWidgetItem(alias))

        # Ignore globs
        ignore_lines = "\n".join(self._vault_cfg.ignore or [])
        self.ignore_edit.setPlainText(ignore_lines)

    def _get_routing_table_data(self) -> list[tuple[str, str]]:
        rules: list[tuple[str, str]] = []
        for r in range(self.routing_table.rowCount()):
            f_item = self.routing_table.item(r, 0)
            d_item = self.routing_table.item(r, 1)
            f_text = f_item.text().strip() if f_item else ""
            d_text = d_item.text().strip() if d_item else ""
            rules.append((f_text, d_text))
        return rules

    def _get_alias_table_data(self) -> list[tuple[str, str]]:
        rules: list[tuple[str, str]] = []
        for r in range(self.alias_table.rowCount()):
            f_item = self.alias_table.item(r, 0)
            a_item = self.alias_table.item(r, 1)
            f_text = f_item.text().strip() if f_item else ""
            a_text = a_item.text().strip() if a_item else ""
            rules.append((f_text, a_text))
        return rules

    def _snapshot_state(self) -> dict[str, Any]:
        return {
            "vault": self.vault_edit.text().strip(),
            "occlusion": self.occlusion_edit.text().strip(),
            "show_diff": self.show_diff_cb.isChecked(),
            "auto_close": self.auto_close_cb.isChecked(),
            "sync_on_startup": self.sync_on_startup_cb.isChecked(),
            "sc_dash": self.sc_dash_edit.keySequence().toString(QKeySequence.SequenceFormat.PortableText),
            "sc_quick": self.sc_quick_edit.keySequence().toString(QKeySequence.SequenceFormat.PortableText),
            "sc_full": self.sc_full_edit.keySequence().toString(QKeySequence.SequenceFormat.PortableText),
            "tag": self.tag_edit.text().strip(),
            "pad_single_digits": self.pad_single_digits_cb.isChecked(),
            "deck": self.deck_combo.currentText().strip(),
            "cloze": self.cloze_cb.isChecked(),
            "suspend_new_clozes": self.suspend_new_clozes_cb.isChecked(),
            "image_occlusion_mode": self.image_occlusion_mode_combo.currentData() or "native",
            "suspend_new_occlusions": self.suspend_new_occlusions_cb.isChecked(),
            "freeze_image_occlusions": self.freeze_image_occlusions_cb.isChecked(),
            "hash_media": self.hash_media_cb.isChecked(),
            "re_stage": self.re_stage_cb.isChecked(),
            "ignore": self.ignore_edit.toPlainText().strip(),
            "basic_model": self._get_selected_model_id(self.basic_model_combo),
            "cloze_model": self._get_selected_model_id(self.cloze_model_combo),
            "routing": self._get_routing_table_data(),
            "aliases": self._get_alias_table_data(),
        }

    def _has_unsaved_changes(self) -> bool:
        if not hasattr(self, "_initial_state"):
            return False
        return self._snapshot_state() != self._initial_state

    def _confirm_discard_changes(self) -> bool:
        if not self._has_unsaved_changes():
            return True
        reply = QMessageBox.question(
            self,
            "Unsaved Changes",
            "You have unsaved changes in Settings.\n\nDo you want to discard your changes and close?",
            QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        return reply == QMessageBox.StandardButton.Discard

    def reject(self) -> None:
        if not self._confirm_discard_changes():
            return
        super().reject()

    def closeEvent(self, event) -> None:
        if not self._confirm_discard_changes():
            event.ignore()
            return
        event.accept()

    def _on_save(self) -> None:
        vault_str = self.vault_edit.text().strip()
        vault_path = Path(vault_str).expanduser().resolve() if vault_str else None

        occ_str = self.occlusion_edit.text().strip()
        occ_path = Path(occ_str).expanduser().resolve() if occ_str else None

        basic_id = self._get_selected_model_id(self.basic_model_combo)
        cloze_id = self._get_selected_model_id(self.cloze_model_combo)

        # Validate duplicate path keys in routing and alias tables
        def _norm_path_key(k: str) -> str:
            parts = tuple(p for p in k.replace("\\", "/").strip().split("/") if p and p != ".")
            return "/".join(parts) if parts else "."

        deck_routing: dict[str, str] = {}
        for r in range(self.routing_table.rowCount()):
            f_item = self.routing_table.item(r, 0)
            d_item = self.routing_table.item(r, 1)
            raw_folder = f_item.text().strip() if f_item else ""
            target_deck = d_item.text().strip() if d_item else ""
            if not raw_folder:
                continue
            norm_key = _norm_path_key(raw_folder)
            if norm_key in deck_routing:
                QMessageBox.warning(
                    self,
                    "Duplicate Deck Routing Rule",
                    f"The folder path '{norm_key}' is configured more than once in Deck Routing.\n"
                    "Please remove or merge duplicate rules before saving.",
                )
                return
            deck_routing[norm_key] = target_deck

        tag_aliases: dict[str, str] = {}
        for r in range(self.alias_table.rowCount()):
            f_item = self.alias_table.item(r, 0)
            a_item = self.alias_table.item(r, 1)
            raw_folder = f_item.text().strip() if f_item else ""
            alias_val = a_item.text().strip() if a_item else ""
            if not raw_folder:
                continue
            norm_key = _norm_path_key(raw_folder)
            if norm_key in tag_aliases:
                QMessageBox.warning(
                    self,
                    "Duplicate Tag Alias",
                    f"The folder path '{norm_key}' is configured more than once in Tag Path Aliases.\n"
                    "Please remove or merge duplicate aliases before saving.",
                )
                return
            tag_aliases[norm_key] = alias_val

        new_shortcuts = {
            "open_dashboard": self.sc_dash_edit.keySequence().toString(QKeySequence.SequenceFormat.PortableText),
            "quick_sync": self.sc_quick_edit.keySequence().toString(QKeySequence.SequenceFormat.PortableText),
            "full_sync": self.sc_full_edit.keySequence().toString(QKeySequence.SequenceFormat.PortableText),
        }

        # 1. Save profile settings
        new_settings = AddonProfileSettings(
            profile_name=self._profile_name,
            vault_path=vault_path,
            occlusion_dir=occ_path,
            auto_close_on_success=self.auto_close_cb.isChecked(),
            show_diff_before_update=self.show_diff_cb.isChecked(),
            sync_on_startup=self.sync_on_startup_cb.isChecked(),
            basic_model_id=basic_id,
            cloze_model_id=cloze_id,
            shortcuts=new_shortcuts,
        )
        self._manager.save(new_settings)

        from .menu import apply_menu_shortcuts
        apply_menu_shortcuts(new_settings)

        # 2. Save vault config to m2a.toml if vault path is valid
        if vault_path and vault_path.is_dir():
            try:
                cfg = self._manager.load_vault(vault_path)
                cfg.deck = self.deck_combo.currentText().strip() or "Default"
                cfg.base_tag = self.tag_edit.text().strip()
                cfg.pad_single_digit_tags = self.pad_single_digits_cb.isChecked()
                cfg.hash_media = self.hash_media_cb.isChecked()
                cfg.cloze_notes = self.cloze_cb.isChecked()
                cfg.suspend_new_clozes = self.suspend_new_clozes_cb.isChecked()
                cfg.image_occlusion_mode = self.image_occlusion_mode_combo.currentData() or "native"
                cfg.suspend_new_occlusions = self.suspend_new_occlusions_cb.isChecked()
                cfg.freeze_image_occlusions = self.freeze_image_occlusions_cb.isChecked()
                cfg.re_stage_occlusions = self.re_stage_cb.isChecked()
                cfg.deck_routing = deck_routing
                cfg.tag_aliases = tag_aliases

                # Collect ignore
                ignore_text = self.ignore_edit.toPlainText().strip()
                cfg.ignore = [line.strip() for line in ignore_text.splitlines() if line.strip()]

                self._manager.save_vault(vault_path, cfg)
            except Exception as exc:
                QMessageBox.warning(self, "Warning", f"Could not update m2a.toml: {exc}")

        self._initial_state = self._snapshot_state()
        self.accept()
