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
    QGridLayout,
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
from ..logger import clear_log, get_log_file_path, log_error, log_warning, read_log_text

def _is_deleted(widget: Any) -> bool:
    if sip is not None and hasattr(sip, "isdeleted"):
        try:
            return sip.isdeleted(widget)
        except Exception:
            return False
    return False


from ..contracts import MissingInAnkiRecord, OrphanNoteRecord, SyncPlan, SyncReport
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
    TAB_DECKS_TAGS = 1
    TAB_CARD_TYPES = 2
    TAB_DELETED_CARDS = 3
    TAB_SHORTCUTS_LOGS = 4

    # Backwards-compatibility aliases
    TAB_MAINTENANCE = 3
    TAB_LOGS = 4
    TAB_DECK_ROUTING = 1
    TAB_TAG_ALIASING = 1
    TAB_RULES = 2
    TAB_ADVANCED = 2

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
        self.resize(760, 540)
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
            target = tab.strip().lower()
            for idx in range(self.tabs.count()):
                raw_text = self.tabs.tabText(idx).strip().lower()
                clean_text = raw_text.replace("&&", "&")
                if raw_text == target or clean_text == target:
                    self.tabs.setCurrentIndex(idx)
                    return
            for idx in range(self.tabs.count()):
                clean_text = self.tabs.tabText(idx).replace("&&", "&").strip().lower()
                if (
                    target in clean_text
                    or any(w == target for w in clean_text.replace("&", " ").split())
                    or (target in ("maintenance", "orphans", "deleted") and idx == self.TAB_DELETED_CARDS)
                ):
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
                f"<b>Warning: Failed to parse vault configuration (m2a.toml):</b><br>"
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

        # Tab 1: General && Sync
        self.tab_general = QWidget()
        self._init_tab_general()
        self.tabs.addTab(self.tab_general, "General && Sync")

        # Tab 2: Decks && Tags
        self.tab_deck_routing = QWidget()
        self.tab_routing = self.tab_deck_routing
        self.tab_tag_aliasing = self.tab_deck_routing
        self._init_tab_decks_and_tags()
        self.tabs.addTab(self.tab_deck_routing, "Decks && Tags")

        # Tab 3: Card Types && Media
        self.tab_rules = QWidget()
        self.tab_advanced = self.tab_rules
        self._init_tab_card_types_and_media()
        self.tabs.addTab(self.tab_rules, "Card Types && Media")

        # Tab 4: Deleted Cards
        self.tab_maintenance = QWidget()
        self.tab_deleted_cards = self.tab_maintenance
        self._init_tab_maintenance()
        self.tabs.addTab(self.tab_maintenance, "Deleted Cards")

        # Tab 5: Shortcuts && Logs
        self.tab_logs = QWidget()
        self.tab_shortcuts_logs = self.tab_logs
        self._init_tab_shortcuts_and_logs()
        self.tabs.addTab(self.tab_logs, "Shortcuts && Logs")

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
        layout = QVBoxLayout(self.tab_general)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(10)

        input_style = """
            QLineEdit, QComboBox {
                font-size: 13px;
                padding: 5px 8px;
                border-radius: 5px;
                border: 1px solid rgba(255, 255, 255, 0.15);
                min-height: 22px;
            }
        """
        btn_style = """
            QPushButton {
                font-size: 12px;
                padding: 5px 12px;
                border-radius: 5px;
                min-height: 22px;
            }
        """

        # Top Row: 2-Column Split (2/3 Vault & Deck | 1/3 Ignored Files)
        top_row = QHBoxLayout()
        top_row.setSpacing(10)

        # Left (2/3 width): Vault Location & Default Deck
        card_dirs, card_dirs_l = _create_section_card(
            "Vault Location & Default Deck",
            "Specify the local folder of your Obsidian vault and fallback deck.",
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

        card_dirs_l.addSpacing(4)

        lbl_deck = QLabel("Default Target Deck:")
        lbl_deck.setStyleSheet("font-size: 12px; font-weight: 600; color: rgba(220, 220, 220, 0.95);")
        card_dirs_l.addWidget(lbl_deck)

        deck_row = QHBoxLayout()
        deck_row.setSpacing(8)
        self.deck_combo = QComboBox()
        self.deck_combo.setStyleSheet(input_style + " QComboBox { min-width: 220px; }")
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
        deck_row.addWidget(self.deck_combo, 1)
        deck_row.addWidget(self._help_button("default_deck"))
        card_dirs_l.addLayout(deck_row)
        top_row.addWidget(card_dirs, stretch=2)

        # Right (1/3 width): Ignored Directories & Files
        card_ign, card_ign_l = _create_section_card(
            "Ignored Directories & Files",
            "Skip during scanning (one per line):",
        )
        self.ignore_edit = QPlainTextEdit()
        self.ignore_edit.setPlaceholderText("e.g. .obsidian\ntemplates/*\narchive")
        _dim_placeholder(self.ignore_edit)
        self.ignore_edit.setMinimumHeight(65)
        self.ignore_edit.setStyleSheet("""
            QPlainTextEdit {
                font-family: monospace;
                font-size: 12px;
                border: 1px solid rgba(255, 255, 255, 0.12);
                border-radius: 6px;
                padding: 6px;
                background-color: transparent;
            }
        """)
        card_ign_l.addWidget(self.ignore_edit)
        top_row.addWidget(card_ign, stretch=1)

        layout.addLayout(top_row)

        # Section 2: Sync Automation & Safety (2-Column Grid, ordered by importance)
        card_sync, card_sync_l = _create_section_card(
            "Sync Behavior & Safety",
            "Configure prompts, safety backups, and automated background sync.",
        )

        grid_sync = QGridLayout()
        grid_sync.setContentsMargins(0, 2, 0, 2)
        grid_sync.setHorizontalSpacing(24)
        grid_sync.setVerticalSpacing(8)

        # Top-Left (Row 0, Col 0): Safety Backup (Most important safety feature)
        self.backup_before_sync_cb = QCheckBox("Create Collection Backup Before Sync")
        self.backup_before_sync_cb.setStyleSheet("font-size: 12px;")
        r_back = QHBoxLayout()
        r_back.setSpacing(6)
        r_back.addWidget(self.backup_before_sync_cb)
        r_back.addWidget(self._help_button("backup_before_sync"))
        r_back.addStretch()
        grid_sync.addLayout(r_back, 0, 0)

        # Top-Right (Row 0, Col 1): Auto-sync on startup
        self.sync_on_startup_cb = QCheckBox("Auto-sync on Anki Startup (Background)")
        self.sync_on_startup_cb.setStyleSheet("font-size: 12px;")
        r_start = QHBoxLayout()
        r_start.setSpacing(6)
        r_start.addWidget(self.sync_on_startup_cb)
        r_start.addWidget(self._help_button("sync_on_startup"))
        r_start.addStretch()
        grid_sync.addLayout(r_start, 0, 1)

        # Bottom-Left (Row 1, Col 0): Close Dashboard on successful sync
        self.auto_close_cb = QCheckBox("Close Dashboard on Successful Sync")
        self.auto_close_cb.setStyleSheet("font-size: 12px;")
        r_close = QHBoxLayout()
        r_close.setSpacing(6)
        r_close.addWidget(self.auto_close_cb)
        r_close.addWidget(self._help_button("auto_close_on_success"))
        r_close.addStretch()
        grid_sync.addLayout(r_close, 1, 0)

        # Bottom-Right (Row 1, Col 1): Show Diff Preview Before Updating (Explicitly requested!)
        self.show_diff_cb = QCheckBox("Show Diff Preview Before Updating")
        self.show_diff_cb.setStyleSheet("font-size: 12px;")
        r_diff = QHBoxLayout()
        r_diff.setSpacing(6)
        r_diff.addWidget(self.show_diff_cb)
        r_diff.addWidget(self._help_button("show_diff_before_update"))
        r_diff.addStretch()
        grid_sync.addLayout(r_diff, 1, 1)

        card_sync_l.addLayout(grid_sync)
        layout.addWidget(card_sync)
        layout.addStretch()

    def _init_tab_decks_and_tags(self) -> None:
        layout = QVBoxLayout(self.tab_deck_routing)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(10)

        btn_style = """
            QPushButton {
                font-size: 12px;
                padding: 4px 10px;
                border-radius: 4px;
                min-height: 20px;
            }
        """
        table_style = """
            QTableWidget {
                background-color: transparent;
                border: 1px solid rgba(255, 255, 255, 0.12);
                border-radius: 6px;
                font-size: 12px;
                gridline-color: rgba(255, 255, 255, 0.05);
            }
            QTableWidget::item {
                background-color: transparent;
            }
            QHeaderView::section {
                background-color: rgba(255, 255, 255, 0.04);
                font-size: 12px;
                font-weight: bold;
                padding: 5px;
                border: none;
                border-bottom: 1px solid rgba(255, 255, 255, 0.15);
            }
        """

        # Section 1: Deck Routing Rules
        card_rules, card_rules_l = _create_section_card(
            "Folder Deck Routing",
            "Map vault folders directly to specific Anki decks using longest prefix matching.",
        )

        self.routing_table = QTableWidget(0, 2)
        self.routing_table.setHorizontalHeaderLabels(["Vault Folder / Path", "Target Anki Deck"])
        self.routing_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.routing_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.routing_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.routing_table.setMinimumHeight(125)
        self.routing_table.setMaximumHeight(145)
        self.routing_table.setStyleSheet(table_style)
        card_rules_l.addWidget(self.routing_table)
        card_rules_l.addSpacing(6)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(6)
        add_btn = QPushButton("Add Rule")
        add_btn.setStyleSheet(btn_style)
        add_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        add_btn.clicked.connect(self._add_routing_row)

        browse_btn = QPushButton("Browse Folder...")
        browse_btn.setStyleSheet(btn_style)
        browse_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        browse_btn.clicked.connect(self._browse_routing_folder)

        remove_btn = QPushButton("Remove Selected")
        remove_btn.setStyleSheet(btn_style)
        remove_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        remove_btn.clicked.connect(self._remove_routing_row)

        btn_row.addWidget(add_btn)
        btn_row.addWidget(browse_btn)
        btn_row.addWidget(remove_btn)
        btn_row.addWidget(self._help_button("deck_routing"))
        btn_row.addStretch()
        card_rules_l.addLayout(btn_row)
        layout.addWidget(card_rules)

        # Section 2: Tag Formatting & Path Aliases
        card_tag, card_tag_l = _create_section_card(
            "Tag Formatting & Folder Aliases",
            "Set hierarchical tag rules and rename or suppress specific folder levels.",
        )

        row_tsettings = QHBoxLayout()
        row_tsettings.setSpacing(12)

        lbl_tag = QLabel("Base Tag Prefix:")
        lbl_tag.setStyleSheet("font-size: 12px; font-weight: 600; color: rgba(220, 220, 220, 0.95);")
        row_tsettings.addWidget(lbl_tag)

        self.tag_edit = QLineEdit()
        self.tag_edit.setPlaceholderText("Optional: e.g. vaultsync")
        self.tag_edit.setMaximumWidth(200)
        _dim_placeholder(self.tag_edit)
        row_tsettings.addWidget(self.tag_edit)
        row_tsettings.addWidget(self._help_button("base_tag"))

        self.pad_single_digits_cb = QCheckBox("Pad Folder Numbers (01, 02...)")
        self.pad_single_digits_cb.setStyleSheet("font-size: 12px;")
        row_tsettings.addWidget(self.pad_single_digits_cb)
        row_tsettings.addWidget(self._help_button("pad_single_digit_tags"))
        row_tsettings.addStretch()
        card_tag_l.addLayout(row_tsettings)

        self.alias_table = QTableWidget(0, 2)
        self.alias_table.setHorizontalHeaderLabels(["Vault Folder / Path", "Tag Alias / Replacement"])
        self.alias_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.alias_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.alias_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.alias_table.setMinimumHeight(125)
        self.alias_table.setMaximumHeight(145)
        self.alias_table.setStyleSheet(table_style)
        card_tag_l.addWidget(self.alias_table)
        card_tag_l.addSpacing(6)

        alias_btn_row = QHBoxLayout()
        alias_btn_row.setSpacing(6)
        add_alias_btn = QPushButton("Add Tag Alias")
        add_alias_btn.setStyleSheet(btn_style)
        add_alias_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        add_alias_btn.clicked.connect(self._add_alias_row)

        browse_alias_btn = QPushButton("Browse Folder...")
        browse_alias_btn.setStyleSheet(btn_style)
        browse_alias_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        browse_alias_btn.clicked.connect(self._browse_alias_folder)

        remove_alias_btn = QPushButton("Remove Selected")
        remove_alias_btn.setStyleSheet(btn_style)
        remove_alias_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        remove_alias_btn.clicked.connect(self._remove_alias_row)

        alias_btn_row.addWidget(add_alias_btn)
        alias_btn_row.addWidget(browse_alias_btn)
        alias_btn_row.addWidget(remove_alias_btn)
        alias_btn_row.addWidget(self._help_button("tag_aliases"))
        alias_btn_row.addStretch()
        card_tag_l.addLayout(alias_btn_row)

        layout.addWidget(card_tag)
        layout.addStretch()

    def _init_tab_card_types_and_media(self) -> None:
        layout = QVBoxLayout(self.tab_rules)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(10)

        input_style = """
            QLineEdit, QComboBox {
                font-size: 13px;
                padding: 5px 8px;
                border-radius: 5px;
                border: 1px solid rgba(255, 255, 255, 0.15);
                min-height: 22px;
            }
        """
        btn_style = """
            QPushButton {
                font-size: 12px;
                padding: 5px 12px;
                border-radius: 5px;
                min-height: 22px;
            }
        """

        # Section 1: Cloze Notes
        card_cloze, card_cloze_l = _create_section_card(
            "Cloze Deletions",
            "Configure parsing and scheduling of {{c1::...}} cloze deletion notes.",
        )
        row_c = QHBoxLayout()
        row_c.setSpacing(16)
        self.cloze_cb = QCheckBox("Enable Cloze Syntax Processing")
        self.cloze_cb.setStyleSheet("font-size: 12px;")
        row_c.addWidget(self.cloze_cb)
        row_c.addWidget(self._help_button("cloze_notes"))

        self.suspend_new_clozes_cb = QCheckBox("Suspend Newly Created Cloze Cards")
        self.suspend_new_clozes_cb.setStyleSheet("font-size: 12px;")
        row_c.addWidget(self.suspend_new_clozes_cb)
        row_c.addWidget(self._help_button("suspend_new_clozes"))
        row_c.addStretch()
        card_cloze_l.addLayout(row_c)
        layout.addWidget(card_cloze)

        # Section 2: Image Occlusion Settings
        card_io, card_io_l = _create_section_card(
            "Image Occlusion Settings",
            "Choose native Anki image occlusion or legacy folder staging.",
        )

        row_mode = QHBoxLayout()
        row_mode.setSpacing(8)
        lbl_iom = QLabel("Mode:")
        lbl_iom.setStyleSheet("font-size: 12px; font-weight: 600; color: rgba(220, 220, 220, 0.95);")
        row_mode.addWidget(lbl_iom)

        self.image_occlusion_mode_combo = QComboBox()
        self.image_occlusion_mode_combo.setStyleSheet(input_style + " QComboBox { min-width: 280px; }")
        self.image_occlusion_mode_combo.addItem("Direct Native Image Occlusions (Recommended)", userData="native")
        self.image_occlusion_mode_combo.addItem("Folder Staging (Legacy / Manual)", userData="stage")
        self.image_occlusion_mode_combo.currentIndexChanged.connect(self._on_io_mode_changed)
        row_mode.addWidget(self.image_occlusion_mode_combo)
        row_mode.addWidget(self._help_button("image_occlusion_mode"))
        row_mode.addStretch()
        card_io_l.addLayout(row_mode)

        # Dynamic staging folder row (hidden in native mode)
        self.staging_folder_row = QWidget()
        sf_layout = QHBoxLayout(self.staging_folder_row)
        sf_layout.setContentsMargins(0, 4, 0, 0)
        sf_layout.setSpacing(8)

        lbl_occ = QLabel("Export Folder:")
        lbl_occ.setStyleSheet("font-size: 12px; font-weight: 600; color: rgba(220, 220, 220, 0.95);")
        sf_layout.addWidget(lbl_occ)

        self.occlusion_edit = QLineEdit()
        self.occlusion_edit.setStyleSheet(input_style)
        self.occlusion_edit.setPlaceholderText("Output folder for staged occlusions...")
        _dim_placeholder(self.occlusion_edit)
        occ_browse = QPushButton("Browse...")
        occ_browse.setStyleSheet(btn_style)
        occ_browse.setCursor(Qt.CursorShape.PointingHandCursor)
        occ_browse.clicked.connect(self._browse_occlusion)
        sf_layout.addWidget(self.occlusion_edit, 1)
        sf_layout.addWidget(occ_browse)
        sf_layout.addWidget(self._help_button("occlusion_dir"))
        card_io_l.addWidget(self.staging_folder_row)

        row_io_opts = QHBoxLayout()
        row_io_opts.setSpacing(16)
        self.suspend_new_occlusions_cb = QCheckBox("Suspend New Occlusions Until Masked")
        self.suspend_new_occlusions_cb.setStyleSheet("font-size: 12px;")
        row_io_opts.addWidget(self.suspend_new_occlusions_cb)
        row_io_opts.addWidget(self._help_button("suspend_new_occlusions"))

        self.re_stage_cb = QCheckBox("Re-export Existing Occlusion Images")
        self.re_stage_cb.setStyleSheet("font-size: 12px;")
        row_io_opts.addWidget(self.re_stage_cb)
        row_io_opts.addWidget(self._help_button("re_stage_occlusions"))

        self.freeze_image_occlusions_cb = QCheckBox("Freeze Occlusion Notes (Skip Updates)")
        self.freeze_image_occlusions_cb.setStyleSheet("font-size: 12px;")
        row_io_opts.addWidget(self.freeze_image_occlusions_cb)
        row_io_opts.addWidget(self._help_button("freeze_image_occlusions"))
        row_io_opts.addStretch()
        card_io_l.addLayout(row_io_opts)
        layout.addWidget(card_io)

        # Section 3: Media & Note Type Bindings
        card_bind, card_bind_l = _create_section_card(
            "Media & Custom Note Types",
            "Manage deterministic image hashing and optional custom Anki note type bindings.",
        )

        row_media = QHBoxLayout()
        self.hash_media_cb = QCheckBox("Hash Media (Rename images with deterministic 8-character hex hash)")
        self.hash_media_cb.setStyleSheet("font-size: 12px;")
        row_media.addWidget(self.hash_media_cb)
        row_media.addWidget(self._help_button("hash_media"))
        row_media.addStretch()
        card_bind_l.addLayout(row_media)

        grid_models = QGridLayout()
        grid_models.setContentsMargins(0, 4, 0, 0)
        grid_models.setHorizontalSpacing(16)
        grid_models.setVerticalSpacing(6)

        lbl_b = QLabel("Basic Note Type:")
        lbl_b.setStyleSheet("font-size: 12px; font-weight: 600; color: rgba(220, 220, 220, 0.95);")
        grid_models.addWidget(lbl_b, 0, 0)

        self.basic_model_combo = QComboBox()
        self.basic_model_combo.setStyleSheet(input_style + " QComboBox { min-width: 240px; }")
        self.basic_model_combo.addItem("Default (M2A Basic)", userData=None)
        if mw is not None and hasattr(mw, "col") and mw.col is not None:
            for m in mw.col.models.all():
                if m.get("type", 0) == 0:
                    self.basic_model_combo.addItem(m["name"], userData=int(m["id"]))
        grid_models.addWidget(self.basic_model_combo, 0, 1)
        grid_models.addWidget(self._help_button("basic_model_id"), 0, 2)

        lbl_c = QLabel("Cloze Note Type:")
        lbl_c.setStyleSheet("font-size: 12px; font-weight: 600; color: rgba(220, 220, 220, 0.95);")
        grid_models.addWidget(lbl_c, 1, 0)

        self.cloze_model_combo = QComboBox()
        self.cloze_model_combo.setStyleSheet(input_style + " QComboBox { min-width: 240px; }")
        self.cloze_model_combo.addItem("Default (M2A Cloze)", userData=None)
        if mw is not None and hasattr(mw, "col") and mw.col is not None:
            for m in mw.col.models.all():
                if m.get("type", 1) == 1:
                    self.cloze_model_combo.addItem(m["name"], userData=int(m["id"]))
        grid_models.addWidget(self.cloze_model_combo, 1, 1)
        grid_models.addWidget(self._help_button("cloze_model_id"), 1, 2)

        grid_models.setColumnStretch(3, 1)
        card_bind_l.addLayout(grid_models)

        layout.addWidget(card_bind)
        layout.addStretch()

        self._on_io_mode_changed()

    def _on_io_mode_changed(self) -> None:
        is_stage = bool(self.image_occlusion_mode_combo.currentData() == "stage")
        if hasattr(self, "staging_folder_row"):
            self.staging_folder_row.setVisible(is_stage)

    def _init_tab_maintenance(self) -> None:
        layout = QVBoxLayout(self.tab_maintenance)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(10)

        btn_style = """
            QPushButton {
                font-size: 12px;
                padding: 5px 12px;
                border-radius: 5px;
                min-height: 22px;
            }
        """
        table_style = """
            QTableWidget {
                background-color: transparent;
                border: 1px solid rgba(255, 255, 255, 0.12);
                border-radius: 6px;
                gridline-color: rgba(255, 255, 255, 0.06);
                font-size: 12px;
            }
            QTableWidget::item {
                background-color: transparent;
            }
            QHeaderView::section {
                background-color: rgba(255, 255, 255, 0.04);
                padding: 5px;
                border: none;
                border-bottom: 1px solid rgba(255, 255, 255, 0.12);
                font-size: 12px;
                font-weight: 600;
            }
        """

        # Section 1: Orphaned Cards in Anki (Deleted from Obsidian)
        card_orph, card_orph_l = _create_section_card(
            "Orphaned Cards in Anki",
            "Cards present in Anki whose source note was removed from your vault.",
        )

        self.orphans_status_label = QLabel("0 orphaned note(s) detected in Anki.")
        self.orphans_status_label.setStyleSheet("font-size: 12px; font-weight: 600;")
        card_orph_l.addWidget(self.orphans_status_label)

        orph_btns = QHBoxLayout()
        orph_btns.setSpacing(8)

        self.view_orphans_btn = QPushButton("View in Anki Browser")
        self.view_orphans_btn.setStyleSheet(btn_style)
        self.view_orphans_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.view_orphans_btn.clicked.connect(self._on_view_orphans)

        self.orphans_btn = self.view_orphans_btn  # Backwards compatibility alias

        self.suspend_orphans_btn = QPushButton("Suspend All Orphaned Cards")
        self.suspend_orphans_btn.setStyleSheet(btn_style)
        self.suspend_orphans_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.suspend_orphans_btn.setToolTip("Suspend cards of orphaned notes to preserve review history and intervals.")
        self.suspend_orphans_btn.clicked.connect(self._on_suspend_orphans)

        self._btn_delete_orphans_style = """
            QPushButton {
                font-size: 12px;
                padding: 5px 12px;
                border-radius: 5px;
                min-height: 22px;
            }
            QPushButton:enabled {
                color: #ff6b6b;
                font-weight: 600;
            }
            QPushButton:disabled {
                color: rgba(180, 180, 180, 0.4);
            }
        """
        self.delete_orphans_btn = QPushButton("Delete Permanently...")
        self.delete_orphans_btn.setStyleSheet(self._btn_delete_orphans_style)
        self.delete_orphans_btn.setEnabled(False)
        self.delete_orphans_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.delete_orphans_btn.setToolTip("Permanently delete orphaned notes from Anki. Review history will be lost.")
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
            "Notes with sync IDs in Obsidian that no longer exist in Anki.",
        )

        # Clean header above table: status on left, selection controls on right
        hdr_ctrl_row = QHBoxLayout()
        hdr_ctrl_row.setSpacing(8)

        self.missing_status_label = QLabel("0 card(s) deleted in Anki.")
        self.missing_status_label.setStyleSheet("font-size: 12px; font-weight: 600;")
        hdr_ctrl_row.addWidget(self.missing_status_label)
        hdr_ctrl_row.addStretch()

        sel_all_btn = QPushButton("Select All")
        sel_all_btn.setStyleSheet(btn_style)
        sel_all_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        sel_all_btn.clicked.connect(lambda: self._on_select_all_missing(True))

        desel_all_btn = QPushButton("Deselect All")
        desel_all_btn.setStyleSheet(btn_style)
        desel_all_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        desel_all_btn.clicked.connect(lambda: self._on_select_all_missing(False))

        hdr_ctrl_row.addWidget(sel_all_btn)
        hdr_ctrl_row.addWidget(desel_all_btn)
        card_del_l.addLayout(hdr_ctrl_row)

        self.missing_table = QTableWidget(0, 4)
        self.missing_table.setHorizontalHeaderLabels(["", "File", "Line", "Question / Preview"])
        self.missing_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.missing_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Interactive)
        self.missing_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.missing_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self.missing_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.missing_table.setMinimumHeight(130)
        self.missing_table.setMaximumHeight(180)
        self.missing_table.setStyleSheet(table_style)
        card_del_l.addWidget(self.missing_table)

        action_row = QHBoxLayout()
        action_row.setSpacing(8)

        self.mark_deleted_btn = QPushButton("Mark Selected as DELETED in Obsidian")
        self.mark_deleted_btn.setStyleSheet(btn_style)
        self.mark_deleted_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.mark_deleted_btn.clicked.connect(self._on_mark_missing_deleted)

        self.resync_missing_btn = QPushButton("Resync / Recreate Selected in Anki")
        self.resync_missing_btn.setStyleSheet(btn_style)
        self.resync_missing_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.resync_missing_btn.clicked.connect(self._on_resync_missing)

        refresh_maint_btn = QPushButton("Refresh Scan")
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


    def set_maintenance_plan(self, plan: SyncPlan) -> None:
        self._current_orphans = list(plan.orphans)
        self._current_missing = list(plan.missing_in_anki)

        # Section 1: Orphans
        count_orphans = len(self._current_orphans)
        suspended_count = 0
        if count_orphans > 0 and mw is not None and getattr(mw, "col", None) is not None:
            try:
                nids = [o.note_id for o in self._current_orphans]
                placeholders = ",".join("?" * len(nids))
                rows = mw.col.db.all(
                    f"SELECT DISTINCT nid FROM cards WHERE nid IN ({placeholders}) AND queue = -1",
                    *nids,
                )
                suspended_count = len(rows)
            except Exception:
                suspended_count = 0

        if suspended_count > 0:
            self.orphans_status_label.setText(
                f"{count_orphans} orphaned note(s) detected in Anki ({suspended_count} suspended)."
            )
        else:
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
        nid_str = ",".join(nids)
        query = f"nid:{nid_str}"
        browser = dialogs.open("Browser", mw)
        browser.search_for(query)
        browser.activateWindow()
        browser.raise_()
        self.lower()

    def _on_suspend_orphans(self) -> None:
        if not self._current_orphans or mw is None or mw.col is None:
            return
        from ..sync_lock import acquire_sync_lock, release_sync_lock
        if not acquire_sync_lock():
            QToolTip.showText(QCursor.pos(), "Another operation is in progress.", self)
            return
        try:
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
        except Exception as exc:
            log_error(f"Failed to suspend orphaned cards: {exc}")
            QMessageBox.critical(self, "Error", f"Failed to suspend orphaned cards: {exc}")
        finally:
            release_sync_lock()

    def _on_delete_orphans(self) -> None:
        if not self._current_orphans or mw is None or mw.col is None:
            return
        from ..sync_lock import acquire_sync_lock, release_sync_lock
        if not acquire_sync_lock():
            QToolTip.showText(QCursor.pos(), "Another operation is in progress.", self)
            return

        count = len(self._current_orphans)
        from anki.notes import NoteId
        nids = [NoteId(o.note_id) for o in self._current_orphans]

        suspended_count = 0
        try:
            placeholders = ",".join("?" * len(nids))
            suspended_rows = mw.col.db.all(
                f"SELECT DISTINCT nid FROM cards WHERE nid IN ({placeholders}) AND queue = -1",
                *nids,
            )
            suspended_count = len(suspended_rows)
        except Exception as exc:
            log_warning(f"Failed to check suspended cards for orphans: {exc}")

        if suspended_count > 0:
            warning_msg = (
                f"WARNING: {suspended_count} of {count} orphaned note(s) contain suspended cards in Anki.\n\n"
                "These cards may have been suspended to preserve review history and learning intervals.\n"
                "Deleting them will permanently erase all review history, learning intervals, and statistics.\n\n"
                f"Are you sure you want to permanently delete all {count} orphaned note(s) "
                f"(including {suspended_count} suspended) from Anki?"
            )
        else:
            warning_msg = (
                f"Are you sure you want to permanently delete {count} orphaned note(s) from Anki?\n\n"
                "All review history, learning intervals, and statistics for these cards will be lost."
            )

        reply = QMessageBox.warning(
            self,
            "Delete Orphaned Cards?",
            warning_msg,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if reply != QMessageBox.StandardButton.Yes:
            release_sync_lock()
            return

        def _op(col) -> int:
            if getattr(self._profile_settings, "backup_before_sync", True):
                from ..backup import create_safety_backup
                create_safety_backup(live_mw=mw, col=col, reason="delete_orphans")
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
        from ..sync_lock import acquire_sync_lock, release_sync_lock
        if not acquire_sync_lock():
            QToolTip.showText(QCursor.pos(), "Another operation is in progress.", self)
            return
        try:
            cards_to_flag = [rec.card for rec in selected if rec.card is not None]
            if cards_to_flag:
                flagged, problems = write_deleted_flags(cards_to_flag)
                if problems:
                    QMessageBox.warning(self, "Some Cards Could Not Be Marked", "\n".join(problems[:10]))
                QToolTip.showText(QCursor.pos(), f"Marked {flagged} card(s) as DELETED in Obsidian.", self)
        finally:
            release_sync_lock()
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
            if getattr(self._profile_settings, "backup_before_sync", True) and plan.has_changes:
                from ..backup import create_safety_backup
                create_safety_backup(live_mw=mw, col=col, reason="recreate_cards")
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

    def _init_tab_shortcuts_and_logs(self) -> None:
        layout = QVBoxLayout(self.tab_logs)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(10)

        input_style = """
            QKeySequenceEdit {
                font-size: 13px;
                padding: 4px 8px;
                border-radius: 5px;
                border: 1px solid rgba(255, 255, 255, 0.15);
                min-height: 22px;
            }
        """
        btn_style = """
            QPushButton {
                font-size: 12px;
                padding: 4px 12px;
                border-radius: 5px;
                min-height: 22px;
            }
        """

        # Section 1: Keyboard Shortcuts
        card_sc, card_sc_l = _create_section_card(
            "Keyboard Shortcuts",
            "Configure global shortcuts for VaultSync operations.",
        )

        grid_sc = QGridLayout()
        grid_sc.setContentsMargins(0, 2, 0, 2)
        grid_sc.setHorizontalSpacing(10)
        grid_sc.setVerticalSpacing(4)

        btn_clear_style = """
            QPushButton {
                font-size: 11px;
                padding: 2px 8px;
                border-radius: 4px;
                min-height: 18px;
                max-width: 48px;
            }
        """

        # 1. Dash
        lbl_dash = QLabel("Open Dashboard:")
        lbl_dash.setStyleSheet("font-size: 12px; font-weight: 600; color: rgba(220, 220, 220, 0.95);")
        grid_sc.addWidget(lbl_dash, 0, 0)
        self.sc_dash_edit = QKeySequenceEdit()
        self.sc_dash_edit.setStyleSheet(input_style)
        self.sc_dash_edit.setMaximumWidth(170)
        btn_c_dash = QPushButton("Clear")
        btn_c_dash.setStyleSheet(btn_clear_style)
        btn_c_dash.clicked.connect(lambda: self.sc_dash_edit.setKeySequence(QKeySequence()))
        r_dash = QHBoxLayout()
        r_dash.addWidget(self.sc_dash_edit)
        r_dash.addWidget(btn_c_dash)
        r_dash.addWidget(self._help_button("shortcuts_open_dashboard"))
        grid_sc.addLayout(r_dash, 0, 1)

        self.sc_dash_warn = QLabel()
        self.sc_dash_warn.setStyleSheet("font-size: 11px; color: #e5c07b; font-weight: 500;")
        self.sc_dash_warn.setVisible(False)
        grid_sc.addWidget(self.sc_dash_warn, 1, 1)

        # 2. Quick
        lbl_quick = QLabel("Quick Sync (New):")
        lbl_quick.setStyleSheet("font-size: 12px; font-weight: 600; color: rgba(220, 220, 220, 0.95);")
        grid_sc.addWidget(lbl_quick, 2, 0)
        self.sc_quick_edit = QKeySequenceEdit()
        self.sc_quick_edit.setStyleSheet(input_style)
        self.sc_quick_edit.setMaximumWidth(170)
        btn_c_quick = QPushButton("Clear")
        btn_c_quick.setStyleSheet(btn_clear_style)
        btn_c_quick.clicked.connect(lambda: self.sc_quick_edit.setKeySequence(QKeySequence()))
        r_quick = QHBoxLayout()
        r_quick.addWidget(self.sc_quick_edit)
        r_quick.addWidget(btn_c_quick)
        r_quick.addWidget(self._help_button("shortcuts_quick_sync"))
        grid_sc.addLayout(r_quick, 2, 1)

        self.sc_quick_warn = QLabel()
        self.sc_quick_warn.setStyleSheet("font-size: 11px; color: #e5c07b; font-weight: 500;")
        self.sc_quick_warn.setVisible(False)
        grid_sc.addWidget(self.sc_quick_warn, 3, 1)

        # 3. Full
        lbl_full = QLabel("Sync and Update:")
        lbl_full.setStyleSheet("font-size: 12px; font-weight: 600; color: rgba(220, 220, 220, 0.95);")
        grid_sc.addWidget(lbl_full, 4, 0)
        self.sc_full_edit = QKeySequenceEdit()
        self.sc_full_edit.setStyleSheet(input_style)
        self.sc_full_edit.setMaximumWidth(170)
        btn_c_full = QPushButton("Clear")
        btn_c_full.setStyleSheet(btn_clear_style)
        btn_c_full.clicked.connect(lambda: self.sc_full_edit.setKeySequence(QKeySequence()))
        r_full = QHBoxLayout()
        r_full.addWidget(self.sc_full_edit)
        r_full.addWidget(btn_c_full)
        r_full.addWidget(self._help_button("shortcuts_full_sync"))
        grid_sc.addLayout(r_full, 4, 1)

        self.sc_full_warn = QLabel()
        self.sc_full_warn.setStyleSheet("font-size: 11px; color: #e5c07b; font-weight: 500;")
        self.sc_full_warn.setVisible(False)
        grid_sc.addWidget(self.sc_full_warn, 5, 1)

        sc_container = QHBoxLayout()
        sc_container.addLayout(grid_sc)
        sc_container.addStretch()
        card_sc_l.addLayout(sc_container)

        btn_reset_sc = QPushButton("Reset Shortcuts to Defaults")
        btn_reset_sc.setStyleSheet(btn_style)
        btn_reset_sc.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_reset_sc.clicked.connect(self._reset_shortcuts_to_defaults)
        r_rst = QHBoxLayout()
        r_rst.addWidget(btn_reset_sc)
        r_rst.addStretch()
        card_sc_l.addLayout(r_rst)
        layout.addWidget(card_sc)

        self.sc_dash_edit.keySequenceChanged.connect(self._validate_shortcuts)
        self.sc_quick_edit.keySequenceChanged.connect(self._validate_shortcuts)
        self.sc_full_edit.keySequenceChanged.connect(self._validate_shortcuts)

        # Section 2: Session Logs
        card_log, card_log_l = _create_section_card(
            "Session Logs",
            "History of sync and maintenance operations (newest entries on top).",
        )

        log_hdr = QHBoxLayout()
        log_hdr.addStretch()
        open_folder_btn = QPushButton("Open Folder")
        open_folder_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        open_folder_btn.setStyleSheet(btn_style)
        open_folder_btn.clicked.connect(self._open_log_folder)
        log_hdr.addWidget(open_folder_btn)

        clear_btn = QPushButton("Clear Logs")
        clear_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        clear_btn.setStyleSheet(btn_style)
        clear_btn.clicked.connect(self._clear_logs)
        log_hdr.addWidget(clear_btn)
        card_log_l.addLayout(log_hdr)

        self.log_viewer = QPlainTextEdit()
        self.log_edit = self.log_viewer
        self.log_viewer.setReadOnly(True)
        self.log_viewer.setMinimumHeight(110)
        self.log_viewer.setStyleSheet("""
            QPlainTextEdit {
                font-family: monospace;
                font-size: 12px;
                border: 1px solid rgba(255, 255, 255, 0.12);
                border-radius: 6px;
                background-color: rgba(0, 0, 0, 0.2);
                padding: 6px;
            }
        """)
        card_log_l.addWidget(self.log_viewer)
        layout.addWidget(card_log)

        self._refresh_logs()
        layout.addStretch()

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
                warn_lbl.setText(f"Conflicts with '{mutual_conflict}'. Shortcut will be disabled if saved.")
                warn_lbl.setVisible(True)
                continue

            # Check conflict with external Anki or add-on actions
            conflict = find_shortcut_conflict(seq_str, allowed_actions=allowed)
            if conflict:
                warn_lbl.setText(f"Already taken by '{conflict}'. Shortcut will be disabled if saved.")
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
        self.backup_before_sync_cb.setChecked(getattr(self._profile_settings, "backup_before_sync", True))

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
            "backup_before_sync": self.backup_before_sync_cb.isChecked(),
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
        is_onboarded = (
            True if (vault_path and vault_path.is_dir())
            else getattr(self._profile_settings, "onboarding_completed", False)
        )
        new_settings = AddonProfileSettings(
            profile_name=self._profile_name,
            vault_path=vault_path,
            occlusion_dir=occ_path,
            auto_close_on_success=self.auto_close_cb.isChecked(),
            show_diff_before_update=self.show_diff_cb.isChecked(),
            sync_on_startup=self.sync_on_startup_cb.isChecked(),
            backup_before_sync=self.backup_before_sync_cb.isChecked(),
            basic_model_id=basic_id,
            cloze_model_id=cloze_id,
            shortcuts=new_shortcuts,
            onboarding_completed=is_onboarded,
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
