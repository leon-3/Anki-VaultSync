from __future__ import annotations

import fnmatch
import html
import os
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
from .help_tooltips import TOOLTIPS, format_tooltip_box
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





class InfoHintLabel(QLabel):
    """Subtle, muted typographic hint indicator 'ⓘ' with tooltip and hover effect."""

    def __init__(self, tooltip_key: str, parent: QWidget | None = None) -> None:
        super().__init__("ⓘ", parent)
        self.setToolTip(format_tooltip_box(tooltip_key))
        self.setCursor(Qt.CursorShape.WhatsThisCursor)
        self.setStyleSheet("""
            QLabel {
                font-size: 11px;
                color: rgba(180, 180, 180, 0.45);
                font-weight: normal;
                padding: 0 1px;
                border: none;
                background: transparent;
            }
            QLabel:hover {
                color: #2196F3;
            }
        """)

    def enterEvent(self, event) -> None:
        super().enterEvent(event)
        tip = self.toolTip()
        if tip:
            QToolTip.showText(QCursor.pos(), tip, self)


def _wrap_with_info(cb: QCheckBox, key: str, add_stretch: bool = True) -> QHBoxLayout:
    """Wraps a checkbox with an attached subtle inline 'ⓘ' typographic hint."""
    row = QHBoxLayout()
    row.setContentsMargins(0, 0, 0, 0)
    row.setSpacing(4)
    row.addWidget(cb)
    hint = InfoHintLabel(key)
    row.addWidget(hint)
    if add_stretch:
        row.addStretch()
    return row

_CHECKMARK_SVG_PATH = Path(__file__).parent / "assets" / "checkmark.svg"

def _is_path_ignored(rel_parts: tuple[str, ...], name: str, ignore_patterns: list[str]) -> bool:
    """Check if a folder or file should be ignored during vault traversal."""
    if name.startswith("."):
        return True
    rel_str = "/".join(rel_parts)
    name_lower = name.lower()
    rel_str_lower = rel_str.lower()

    for pat in ignore_patterns:
        pat_clean = pat.strip().replace("\\", "/").strip()
        if not pat_clean:
            continue
        pat_norm = pat_clean.strip("/")
        pat_lower = pat_norm.lower()

        # Exact match on directory name or full relative path
        if name_lower == pat_lower or rel_str_lower == pat_lower:
            return True

        # Wildcard glob match on name or relative path
        if fnmatch.fnmatch(name_lower, pat_lower) or fnmatch.fnmatch(rel_str_lower, pat_lower):
            return True

        # Wildcard prefix, e.g. "templates/*" or "archive/*"
        if pat_clean.endswith("/*"):
            base = pat_clean[:-2].strip("/").lower()
            if base in (name_lower, rel_str_lower) or fnmatch.fnmatch(name_lower, base) or fnmatch.fnmatch(rel_str_lower, base):
                return True

        # If pattern is a directory, any path inside it is ignored
        if rel_str_lower.startswith(pat_lower + "/"):
            return True

        # If pattern has no slashes, any matching segment in the path ignores the path
        if "/" not in pat_norm:
            if any(part.lower() == pat_lower or fnmatch.fnmatch(part.lower(), pat_lower) for part in rel_parts):
                return True

    return False


class ConfigWindow(QDialog):
    """Modern, tabbed settings configurator for Anki-VaultSync."""

    TAB_GENERAL = 0
    TAB_DECK_RULES = 1
    TAB_TAG_RULES = 2
    TAB_CARD_TYPES = 3
    TAB_DELETED_CARDS = 4
    TAB_SHORTCUTS_LOGS = 5

    # Backwards-compatibility aliases
    TAB_DECK_ROUTING = 1
    TAB_TAG_ROUTING = 2
    TAB_DECKS = 1
    TAB_DECKS_TAGS = 1
    TAB_ROUTING = 1
    TAB_TAGS = 2
    TAB_TAG_ALIASING = 2
    TAB_RULES = 3
    TAB_ADVANCED = 3
    TAB_MAINTENANCE = 4
    TAB_DELETED = 4
    TAB_LOGS = 5

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
        self.resize(780, 620)
        self.setWindowModality(Qt.WindowModality.NonModal)
        self.setStyleSheet("""
            QCheckBox {
                font-size: 12px;
                spacing: 8px;
                color: palette(window-text);
            }
            QCheckBox::indicator {
                width: 16px;
                height: 16px;
                border: 1.5px solid rgba(150, 150, 150, 0.75);
                border-radius: 4px;
                background-color: rgba(130, 130, 130, 0.12);
            }
            QCheckBox::indicator:hover {
                border: 1.5px solid #2196F3;
                background-color: rgba(33, 150, 243, 0.15);
            }
            QCheckBox::indicator:checked {
                background-color: #2196F3;
                border: 1.5px solid #1E88E5;
                image: url("__CHECKMARK_SVG__");
            }
            QCheckBox::indicator:checked:hover {
                background-color: #1976D2;
                border: 1.5px solid #1565C0;
            }
            QCheckBox::indicator:disabled {
                border: 1.5px solid rgba(130, 130, 130, 0.3);
                background-color: rgba(130, 130, 130, 0.05);
            }
            QToolTip {
                background-color: #202226;
                color: #e6e6e6;
                border: 1px solid #444950;
                border-radius: 6px;
                padding: 8px 12px;
            }
        """.replace("__CHECKMARK_SVG__", _CHECKMARK_SVG_PATH.resolve().as_posix()))

        self._manager = profile_manager
        self._profile_name = profile_name
        self._updating_routing_table: bool = False
        self._updating_alias_table: bool = False
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
                    or (target in ("deck", "decks", "deck rules", "deck routing", "decks & tags", "routing") and idx == self.TAB_DECK_RULES)
                    or (target in ("tag", "tags", "tag rules", "tag routing", "aliases", "tag aliases") and idx == self.TAB_TAG_RULES)
                    or (target in ("maintenance", "orphans", "deleted", "deleted cards") and idx == self.TAB_DELETED_CARDS)
                    or (target in ("card types", "card types & media", "rules", "media") and idx == self.TAB_CARD_TYPES)
                    or (target in ("shortcuts", "logs", "shortcuts & logs") and idx == self.TAB_SHORTCUTS_LOGS)
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

    def _help_button(self, key: str) -> QWidget:
        w = QWidget()
        w.setVisible(False)
        return w

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

        self.error_banner = QLabel()
        self.error_banner.setWordWrap(True)
        self.error_banner.setStyleSheet(
            "background-color: #fff3cd; color: #856404; border: 1px solid #ffeeba; "
            "border-radius: 6px; padding: 10px; font-size: 12px; font-family: sans-serif;"
        )
        if self._vault_cfg_error:
            self.error_banner.setText(
                f"<b>Warning: Failed to parse vault configuration (m2a.toml):</b><br>"
                f"{html.escape(self._vault_cfg_error)}<br>"
                f"<small>Default settings loaded. Please verify your m2a.toml syntax.</small>"
            )
            self.error_banner.setVisible(True)
        else:
            self.error_banner.setVisible(False)
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

        # Tab 2: Deck Rules
        self.tab_deck_rules = QWidget()
        self.tab_deck_routing = self.tab_deck_rules
        self.tab_routing = self.tab_deck_rules
        self._init_tab_deck_rules()
        self.tabs.addTab(self.tab_deck_rules, "Deck Rules")

        # Tab 3: Tag Rules
        self.tab_tag_rules = QWidget()
        self.tab_tag_routing = self.tab_tag_rules
        self.tab_tag_aliasing = self.tab_tag_rules
        self._init_tab_tag_rules()
        self.tabs.addTab(self.tab_tag_rules, "Tag Rules")

        # Tab 3: Card Types && Media
        self.tab_rules = QWidget()
        self.tab_advanced = self.tab_rules
        self.tab_card_types = self.tab_rules
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

        # Top Row: 2-Column Split (2/3 Vault Location | 1/3 Ignored Files)
        top_row = QHBoxLayout()
        top_row.setSpacing(10)

        # Left (2/3 width): Vault Location
        card_dirs, card_dirs_l = _create_section_card(
            "Vault Location",
            "Specify the local root folder of your Obsidian vault.",
        )

        card_dirs_l.addStretch(1)
        lbl_vault_row = QHBoxLayout()
        lbl_vault_row.setSpacing(6)
        lbl_vault = QLabel("Obsidian Vault Folder:")
        lbl_vault.setStyleSheet("font-size: 12px; font-weight: 600; color: rgba(220, 220, 220, 0.95);")
        hint_vault = InfoHintLabel("vault_path")
        lbl_vault_row.addWidget(lbl_vault)
        lbl_vault_row.addWidget(hint_vault)
        lbl_vault_row.addStretch()
        card_dirs_l.addLayout(lbl_vault_row)
        card_dirs_l.addSpacing(4)

        vault_row = QHBoxLayout()
        vault_row.setSpacing(8)
        self.vault_edit = QLineEdit()
        self.vault_edit.setStyleSheet(input_style)
        self.vault_edit.setPlaceholderText("Select Obsidian vault folder...")
        self.vault_edit.setToolTip(format_tooltip_box("vault_path"))
        _dim_placeholder(self.vault_edit)
        self.vault_edit.textChanged.connect(self._on_vault_text_changed)
        self.vault_edit.editingFinished.connect(lambda: self._load_vault_from_path(self.vault_edit.text()))
        vault_browse = QPushButton("Browse...")
        vault_browse.setStyleSheet(btn_style)
        vault_browse.setCursor(Qt.CursorShape.PointingHandCursor)
        vault_browse.clicked.connect(self._browse_vault)
        vault_row.addWidget(self.vault_edit, 1)
        vault_row.addWidget(vault_browse)
        card_dirs_l.addLayout(vault_row)
        card_dirs_l.addStretch(1)
        top_row.addWidget(card_dirs, stretch=2)

        # Right (1/3 width): Ignored Directories & Files
        card_ign, card_ign_l = _create_section_card(
            "Ignored Directories & Files",
            "Skip during scanning (one per line):",
        )
        self.ignore_edit = QPlainTextEdit()
        self.ignore_edit.setPlaceholderText("e.g. .obsidian\ntemplates/*\narchive")
        self.ignore_edit.setToolTip(format_tooltip_box("ignore"))
        _dim_placeholder(self.ignore_edit)
        self.ignore_edit.setFixedHeight(88)
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
        self.ignore_edit.textChanged.connect(self._refresh_folder_combos)
        card_ign_l.addWidget(self.ignore_edit)
        top_row.addWidget(card_ign, stretch=1)

        layout.addLayout(top_row)

        # Section 2: Sync Automation & Safety (2-Column Grid with aligned info icons)
        card_sync, card_sync_l = _create_section_card(
            "Sync Behavior & Safety",
            "Configure prompts, safety backups, and automated background sync.",
        )

        sync_cols = QHBoxLayout()
        sync_cols.setContentsMargins(0, 2, 0, 2)

        left_grid = QGridLayout()
        left_grid.setHorizontalSpacing(8)
        left_grid.setVerticalSpacing(10)

        # Row 0, Left: Safety Backup
        self.backup_before_sync_cb = QCheckBox("Create Collection Backup Before Sync")
        self.backup_before_sync_cb.setToolTip(format_tooltip_box("backup_before_sync"))
        h_backup = InfoHintLabel("backup_before_sync")
        left_grid.addWidget(self.backup_before_sync_cb, 0, 0)
        left_grid.addWidget(h_backup, 0, 1)

        # Row 1, Left: Close Dashboard on successful sync
        self.auto_close_cb = QCheckBox("Close Dashboard on Successful Sync")
        self.auto_close_cb.setToolTip(format_tooltip_box("auto_close_on_success"))
        h_close = InfoHintLabel("auto_close_on_success")
        left_grid.addWidget(self.auto_close_cb, 1, 0)
        left_grid.addWidget(h_close, 1, 1)

        right_grid = QGridLayout()
        right_grid.setHorizontalSpacing(8)
        right_grid.setVerticalSpacing(10)

        # Row 0, Right: Show Diff Preview Before Updating (Swapped to row 0 alongside backup)
        self.show_diff_cb = QCheckBox("Show Diff Preview Before Updating")
        self.show_diff_cb.setToolTip(format_tooltip_box("show_diff_before_update"))
        h_diff = InfoHintLabel("show_diff_before_update")
        right_grid.addWidget(self.show_diff_cb, 0, 0)
        right_grid.addWidget(h_diff, 0, 1)

        # Row 1, Right: Auto-sync on Anki Startup (Background)
        self.sync_on_startup_cb = QCheckBox("Auto-sync on Anki Startup (Background)")
        self.sync_on_startup_cb.setToolTip(format_tooltip_box("sync_on_startup"))
        h_startup = InfoHintLabel("sync_on_startup")
        right_grid.addWidget(self.sync_on_startup_cb, 1, 0)
        right_grid.addWidget(h_startup, 1, 1)

        sync_cols.addLayout(left_grid)
        sync_cols.addSpacing(32)
        sync_cols.addLayout(right_grid)
        sync_cols.addStretch()

        card_sync_l.addLayout(sync_cols)
        layout.addWidget(card_sync)
        layout.addStretch()

    def _get_available_vault_folders(self) -> list[str]:
        folders: list[str] = []
        vault_str = self.vault_edit.text().strip() if hasattr(self, "vault_edit") else ""
        if not vault_str:
            return folders
        vault_path = Path(vault_str).expanduser().resolve()
        if not vault_path.is_dir():
            return folders

        ignore_lines: list[str] = []
        if hasattr(self, "ignore_edit") and self.ignore_edit is not None:
            text = self.ignore_edit.toPlainText().strip()
            if text:
                ignore_lines = [line.strip() for line in text.splitlines() if line.strip()]
        if not ignore_lines and self._vault_cfg is not None and self._vault_cfg.ignore:
            ignore_lines = [pat.strip() for pat in self._vault_cfg.ignore if pat.strip()]
        if not ignore_lines:
            try:
                from markdown2anki.config import DEFAULT_IGNORE
                ignore_lines = list(DEFAULT_IGNORE)
            except Exception:
                ignore_lines = [".git", ".obsidian", "Archive", "templates"]

        try:
            for root, dirs, _ in os.walk(vault_path, topdown=True):
                rel_root = Path(root).relative_to(vault_path).as_posix()
                root_parts = tuple(rel_root.split("/")) if rel_root != "." else ()

                # Prune ignored directories in-place so os.walk does not descend into them
                dirs[:] = [
                    d for d in dirs
                    if not _is_path_ignored(root_parts + (d,), d, ignore_lines)
                ]

                for d in dirs:
                    p = Path(root) / d
                    try:
                        rel = p.relative_to(vault_path).as_posix()
                        folders.append(rel)
                    except ValueError:
                        pass
        except Exception:
            pass

        # Sort: primary by level (nesting depth count of "/"), secondary alphabetical case-insensitive
        folders.sort(key=lambda f: (f.count("/"), f.lower()))
        return folders

    def _create_folder_combo_for_table(self, selected: str = "", is_deck_table: bool = True) -> QComboBox:
        combo_style = """
            QComboBox {
                font-size: 12px;
                padding: 2px 6px;
                border: 1px solid rgba(255, 255, 255, 0.12);
                border-radius: 4px;
                background-color: rgba(30, 30, 30, 0.95);
                color: #ffffff;
            }
            QComboBox::drop-down {
                border: none;
                width: 18px;
            }
            QComboBox QAbstractItemView {
                background-color: #1e1e1e;
                color: #ffffff;
                selection-background-color: #2196F3;
            }
        """
        combo = QComboBox()
        combo.setStyleSheet(combo_style)
        combo.setEditable(True)
        combo.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        if combo.completer():
            combo.completer().setFilterMode(Qt.MatchFlag.MatchContains)
        if combo.lineEdit():
            combo.lineEdit().setPlaceholderText("Select folder or type path...")

        folders = self._get_available_vault_folders()
        for f in folders:
            combo.addItem(f)

        if selected:
            idx = combo.findText(selected)
            if idx >= 0:
                combo.setCurrentIndex(idx)
            else:
                combo.setCurrentText(selected)
        elif combo.count() > 0:
            combo.setCurrentIndex(0)
        else:
            combo.setCurrentText("folder_path")

        if is_deck_table:
            combo.currentTextChanged.connect(lambda text, c=combo: self._on_routing_folder_combo_changed(c, text))
        else:
            combo.currentTextChanged.connect(lambda text, c=combo: self._on_alias_folder_combo_changed(c, text))
        return combo

    def _refresh_folder_combos(self) -> None:
        folders = self._get_available_vault_folders()
        for tbl in (getattr(self, "routing_table", None), getattr(self, "alias_table", None)):
            if tbl is None:
                continue
            for r in range(tbl.rowCount()):
                w = tbl.cellWidget(r, 0)
                if isinstance(w, QComboBox):
                    curr = w.currentText()
                    w.blockSignals(True)
                    w.clear()
                    for f in folders:
                        w.addItem(f)
                    idx = w.findText(curr)
                    if idx >= 0:
                        w.setCurrentIndex(idx)
                    else:
                        w.setCurrentText(curr)
                    w.blockSignals(False)
                    item = tbl.item(r, 0)
                    if item and item.text() != w.currentText():
                        item.setText(w.currentText())

    def _get_available_deck_names(self) -> list[str]:
        names: list[str] = []
        if mw is not None and hasattr(mw, "col") and mw.col is not None:
            try:
                for d in mw.col.decks.all_names_and_ids():
                    try:
                        if hasattr(mw.col.decks, "is_filtered") and mw.col.decks.is_filtered(d.id):
                            continue
                        deck = mw.col.decks.get(d.id)
                        if deck and deck.get("dyn", False):
                            continue
                    except Exception:
                        pass
                    names.append(d.name)
            except Exception:
                pass
        return names

    def _get_available_tag_names(self) -> list[str]:
        tags: list[str] = []
        if mw is not None and hasattr(mw, "col") and mw.col is not None:
            try:
                if hasattr(mw.col, "tags") and hasattr(mw.col.tags, "all"):
                    tags = list(mw.col.tags.all())
            except Exception:
                pass
        return sorted(tags, key=str.lower)

    def _create_deck_combo_for_table(self, selected: str = "") -> QComboBox:
        combo_style = """
            QComboBox {
                font-size: 12px;
                padding: 2px 6px;
                border: 1px solid rgba(255, 255, 255, 0.12);
                border-radius: 4px;
                background-color: rgba(30, 30, 30, 0.95);
                color: #ffffff;
            }
            QComboBox::drop-down {
                border: none;
                width: 18px;
            }
            QComboBox QAbstractItemView {
                background-color: #1e1e1e;
                color: #ffffff;
                selection-background-color: #2196F3;
            }
        """
        combo = QComboBox()
        combo.setStyleSheet(combo_style)
        combo.setEditable(True)
        combo.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        if combo.completer():
            combo.completer().setFilterMode(Qt.MatchFlag.MatchContains)
        if combo.lineEdit():
            combo.lineEdit().setPlaceholderText("Select or type deck...")

        for name in self._get_available_deck_names():
            combo.addItem(name)
        if selected:
            idx = combo.findText(selected)
            if idx >= 0:
                combo.setCurrentIndex(idx)
            else:
                combo.setCurrentText(selected)
        elif combo.count() > 0:
            combo.setCurrentIndex(0)
        else:
            combo.setCurrentText("Default")

        combo.currentTextChanged.connect(lambda text, c=combo: self._on_routing_combo_changed(c, text))
        return combo

    def _create_tag_combo_for_table(self, selected: str = "") -> QComboBox:
        combo_style = """
            QComboBox {
                font-size: 12px;
                padding: 2px 6px;
                border: 1px solid rgba(255, 255, 255, 0.12);
                border-radius: 4px;
                background-color: rgba(30, 30, 30, 0.95);
                color: #ffffff;
            }
            QComboBox::drop-down {
                border: none;
                width: 18px;
            }
            QComboBox QAbstractItemView {
                background-color: #1e1e1e;
                color: #ffffff;
                selection-background-color: #2196F3;
            }
        """
        combo = QComboBox()
        combo.setStyleSheet(combo_style)
        combo.setEditable(True)
        combo.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        if combo.completer():
            combo.completer().setFilterMode(Qt.MatchFlag.MatchContains)
        if combo.lineEdit():
            combo.lineEdit().setPlaceholderText("Select or type tag (or blank)...")

        # Empty entry first to allow removing or suppressing folder from tags
        combo.addItem("")
        for name in self._get_available_tag_names():
            if name:
                combo.addItem(name)

        if selected:
            idx = combo.findText(selected)
            if idx >= 0:
                combo.setCurrentIndex(idx)
            else:
                combo.setCurrentText(selected)
        else:
            combo.setCurrentIndex(0)

        combo.currentTextChanged.connect(lambda text, c=combo: self._on_alias_combo_changed(c, text))
        return combo

    def _set_routing_row(self, row: int, folder: str, deck: str) -> None:
        self._updating_routing_table = True
        try:
            self.routing_table.setItem(row, 0, QTableWidgetItem(folder))
            self.routing_table.setItem(row, 1, QTableWidgetItem(deck))
            folder_combo = self._create_folder_combo_for_table(folder, is_deck_table=True)
            self.routing_table.setCellWidget(row, 0, folder_combo)
            deck_combo = self._create_deck_combo_for_table(deck)
            self.routing_table.setCellWidget(row, 1, deck_combo)
        finally:
            self._updating_routing_table = False

    def _set_alias_row(self, row: int, folder: str, alias: str) -> None:
        self._updating_alias_table = True
        try:
            self.alias_table.setItem(row, 0, QTableWidgetItem(folder))
            self.alias_table.setItem(row, 1, QTableWidgetItem(alias))
            folder_combo = self._create_folder_combo_for_table(folder, is_deck_table=False)
            self.alias_table.setCellWidget(row, 0, folder_combo)
            tag_combo = self._create_tag_combo_for_table(alias)
            self.alias_table.setCellWidget(row, 1, tag_combo)
        finally:
            self._updating_alias_table = False

    def _init_tab_deck_routing(self) -> None:
        self._init_tab_deck_rules()

    def _init_tab_deck_rules(self) -> None:
        layout = QVBoxLayout(self.tab_deck_rules)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(10)

        input_style = """
            QLineEdit, QComboBox {
                font-size: 12px;
                padding: 5px 8px;
                border: 1px solid rgba(255, 255, 255, 0.12);
                border-radius: 4px;
                background-color: transparent;
            }
        """
        btn_style = """
            QPushButton {
                font-size: 12px;
                padding: 4px 10px;
                border-radius: 4px;
                min-height: 22px;
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

        # Section 1: Default Target Deck (compact first point)
        card_default, card_default_l = _create_section_card(
            "Default Target Deck",
            "Fallback Anki deck for flashcards that do not match any folder rule below.",
        )
        deck_row = QHBoxLayout()
        deck_row.setSpacing(8)
        lbl_deck = QLabel("Default Target Deck: &nbsp;&nbsp;<span style='color: rgba(180, 180, 180, 0.45); font-size: 11px;'>ⓘ</span>")
        lbl_deck.setStyleSheet("font-size: 12px; font-weight: 600; color: rgba(220, 220, 220, 0.95);")
        lbl_deck.setToolTip(format_tooltip_box("default_deck"))
        lbl_deck.setCursor(Qt.CursorShape.WhatsThisCursor)
        deck_row.addWidget(lbl_deck)

        self.deck_combo = QComboBox()
        self.deck_combo.setStyleSheet(input_style + " QComboBox { min-width: 220px; }")
        self.deck_combo.setEditable(True)
        self.deck_combo.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        if self.deck_combo.completer():
            self.deck_combo.completer().setFilterMode(Qt.MatchFlag.MatchContains)
        if self.deck_combo.lineEdit():
            self.deck_combo.lineEdit().setPlaceholderText("Select or type default deck...")
        self.deck_combo.setToolTip(format_tooltip_box("default_deck"))
        for d in self._get_available_deck_names():
            self.deck_combo.addItem(d)
        deck_row.addWidget(self.deck_combo, 1)
        card_default_l.addLayout(deck_row)
        layout.addWidget(card_default)

        # Section 2: Folder Deck Rules (2/3 Table | 1/3 Explanation Layout)
        card_rules, card_rules_l = _create_section_card(
            "Deck Rules & Folder Mapping",
            "Assign specific vault directories to designated target Anki decks.",
        )

        # 2/3 (Table) | 1/3 (Explanation) Split aligned directly with the table
        table_row = QHBoxLayout()
        table_row.setSpacing(14)

        self.routing_table = QTableWidget(0, 2)
        self.routing_table.setHorizontalHeaderLabels(["Vault Folder / Path", "Target Anki Deck"])
        self.routing_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.routing_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.routing_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.routing_table.setMinimumHeight(200)
        self.routing_table.setStyleSheet(table_style)
        self.routing_table.setToolTip(format_tooltip_box("deck_routing"))
        self.routing_table.itemChanged.connect(self._on_routing_item_changed)
        table_row.addWidget(self.routing_table, stretch=2)

        # Right Column (1/3 width): Compact Explanation Panel matching table style
        info_panel = QFrame()
        info_panel.setStyleSheet("""
            QFrame {
                background-color: rgba(255, 255, 255, 0.02);
                border: 1px solid rgba(255, 255, 255, 0.12);
                border-radius: 6px;
                padding: 6px 10px;
            }
            QLabel {
                font-size: 11px;
                line-height: 135%;
                color: rgba(220, 220, 220, 0.90);
                border: none;
                background: transparent;
            }
        """)
        info_l = QVBoxLayout(info_panel)
        info_l.setContentsMargins(2, 2, 2, 2)
        info_l.setSpacing(4)
        info_title = QLabel("How Deck Rules work:")
        info_title.setStyleSheet("font-size: 11.5px; font-weight: 600; color: rgba(245, 245, 245, 0.95);")
        info_text = QLabel(
            "<div style='margin-bottom: 6px; color: rgba(200, 200, 200, 0.85);'>"
            "Direct cards from specific vault folders into dedicated Anki target decks automatically."
            "</div>"
            "<div style='margin-bottom: 5px;'>"
            "• <b>Folder Mapping:</b> Notes in a mapped folder go to that folder's target Anki deck."
            "</div>"
            "<div style='margin-bottom: 5px;'>"
            "• <b>Inheritance:</b> Subfolders inherit the parent rule unless a specific rule is set."
            "</div>"
            "<div>"
            "• <b>Default Deck:</b> Unmatched notes go to the Default Target Deck above."
            "</div>"
        )
        info_text.setWordWrap(True)
        info_l.addWidget(info_title)
        info_l.addWidget(info_text)
        info_l.addStretch()

        table_row.addWidget(info_panel, stretch=1)
        card_rules_l.addLayout(table_row)

        card_rules_l.addSpacing(12)
        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)
        add_btn = QPushButton("Add Rule")
        add_btn.setStyleSheet(btn_style)
        add_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        add_btn.clicked.connect(self._add_routing_row)

        remove_btn = QPushButton("Remove Selected")
        remove_btn.setStyleSheet(btn_style)
        remove_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        remove_btn.clicked.connect(self._remove_routing_row)

        btn_row.addWidget(add_btn)
        btn_row.addWidget(remove_btn)
        btn_row.addStretch()
        card_rules_l.addLayout(btn_row)

        layout.addWidget(card_rules)
        layout.addStretch()

    def _init_tab_tag_routing(self) -> None:
        self._init_tab_tag_rules()

    def _init_tab_tag_rules(self) -> None:
        layout = QVBoxLayout(self.tab_tag_rules)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(10)

        btn_style = """
            QPushButton {
                font-size: 12px;
                padding: 4px 10px;
                border-radius: 4px;
                min-height: 22px;
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

        card_tag, card_tag_l = _create_section_card(
            "Tag Rules & Folder Aliases",
            "Control how directory hierarchy converts into hierarchical Anki tags.",
        )

        row_tsettings = QHBoxLayout()
        row_tsettings.setSpacing(12)

        lbl_tag = QLabel("Base Tag Prefix: &nbsp;&nbsp;<span style='color: rgba(180, 180, 180, 0.45); font-size: 11px;'>ⓘ</span>")
        lbl_tag.setStyleSheet("font-size: 12px; font-weight: 600; color: rgba(220, 220, 220, 0.95);")
        lbl_tag.setToolTip(format_tooltip_box("base_tag"))
        lbl_tag.setCursor(Qt.CursorShape.WhatsThisCursor)
        row_tsettings.addWidget(lbl_tag)

        self.tag_edit = QLineEdit()
        self.tag_edit.setPlaceholderText("Optional: e.g. vaultsync")
        self.tag_edit.setMaximumWidth(200)
        self.tag_edit.setToolTip(format_tooltip_box("base_tag"))
        _dim_placeholder(self.tag_edit)
        row_tsettings.addWidget(self.tag_edit)

        self.pad_single_digits_cb = QCheckBox("Pad Folder Numbers (01, 02...)")
        self.pad_single_digits_cb.setToolTip(format_tooltip_box("pad_single_digit_tags"))
        row_tsettings.addLayout(_wrap_with_info(self.pad_single_digits_cb, "pad_single_digit_tags", add_stretch=False))
        row_tsettings.addStretch()
        card_tag_l.addLayout(row_tsettings)
        card_tag_l.addSpacing(6)

        # 2/3 (Table) | 1/3 (Explanation) Split aligned directly with the table
        table_row = QHBoxLayout()
        table_row.setSpacing(14)

        self.alias_table = QTableWidget(0, 2)
        self.alias_table.setHorizontalHeaderLabels(["Vault Folder / Path", "Tag Alias / Replacement"])
        self.alias_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.alias_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.alias_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.alias_table.setMinimumHeight(200)
        self.alias_table.setStyleSheet(table_style)
        self.alias_table.setToolTip(format_tooltip_box("tag_aliases"))
        self.alias_table.itemChanged.connect(self._on_alias_item_changed)
        table_row.addWidget(self.alias_table, stretch=2)

        # Right Column (1/3 width): Compact Explanation Panel matching table style
        info_panel = QFrame()
        info_panel.setStyleSheet("""
            QFrame {
                background-color: rgba(255, 255, 255, 0.02);
                border: 1px solid rgba(255, 255, 255, 0.12);
                border-radius: 6px;
                padding: 6px 10px;
            }
            QLabel {
                font-size: 11px;
                line-height: 135%;
                color: rgba(220, 220, 220, 0.90);
                border: none;
                background: transparent;
            }
        """)
        info_l = QVBoxLayout(info_panel)
        info_l.setContentsMargins(2, 2, 2, 2)
        info_l.setSpacing(4)
        info_title = QLabel("How Tag Rules work:")
        info_title.setStyleSheet("font-size: 11.5px; font-weight: 600; color: rgba(245, 245, 245, 0.95);")
        info_text = QLabel(
            "<div style='margin-bottom: 6px; color: rgba(200, 200, 200, 0.85);'>"
            "Transform vault folder paths into clean, customizable hierarchical tags in Anki."
            "</div>"
            "<div style='margin-bottom: 5px;'>"
            "• <b>Hierarchical Tags:</b> Vault folders convert to nested tags (<code>Folder::Subfolder</code>)."
            "</div>"
            "<div style='margin-bottom: 5px;'>"
            "• <b>Folder Aliases:</b> Rename directory levels, or leave blank to exclude from tags."
            "</div>"
            "<div>"
            "• <b>Base Prefix:</b> Optional root tag prepended before all generated tags."
            "</div>"
        )
        info_text.setWordWrap(True)
        info_l.addWidget(info_title)
        info_l.addWidget(info_text)
        info_l.addStretch()

        table_row.addWidget(info_panel, stretch=1)
        card_tag_l.addLayout(table_row)

        card_tag_l.addSpacing(12)
        alias_btn_row = QHBoxLayout()
        alias_btn_row.setSpacing(8)
        add_alias_btn = QPushButton("Add Tag Alias")
        add_alias_btn.setStyleSheet(btn_style)
        add_alias_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        add_alias_btn.clicked.connect(self._add_alias_row)

        remove_alias_btn = QPushButton("Remove Selected")
        remove_alias_btn.setStyleSheet(btn_style)
        remove_alias_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        remove_alias_btn.clicked.connect(self._remove_alias_row)

        alias_btn_row.addWidget(add_alias_btn)
        alias_btn_row.addWidget(remove_alias_btn)
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
        row_c.setSpacing(24)
        self.cloze_cb = QCheckBox("Convert Cloze Syntax ({{c1::...}})")
        self.cloze_cb.setToolTip(format_tooltip_box("cloze_notes"))
        row_c.addLayout(_wrap_with_info(self.cloze_cb, "cloze_notes", add_stretch=False))

        self.suspend_new_clozes_cb = QCheckBox("Suspend Newly Created Cloze Cards")
        self.suspend_new_clozes_cb.setToolTip(format_tooltip_box("suspend_new_clozes"))
        row_c.addLayout(_wrap_with_info(self.suspend_new_clozes_cb, "suspend_new_clozes", add_stretch=False))
        row_c.addStretch()
        card_cloze_l.addLayout(row_c)
        layout.addWidget(card_cloze)

        # Section 2: Image Occlusion Settings
        card_io, card_io_l = _create_section_card(
            "Image Occlusion Settings",
            "Choose native Anki image occlusion or legacy folder staging.",
        )

        row_mode = QHBoxLayout()
        row_mode.setSpacing(6)
        lbl_iom = QLabel("Mode:")
        lbl_iom.setStyleSheet("font-size: 12px; font-weight: 600; color: rgba(220, 220, 220, 0.95);")
        hint_iom = InfoHintLabel("image_occlusion_mode")
        row_mode.addWidget(lbl_iom)
        row_mode.addWidget(hint_iom)
        row_mode.addSpacing(4)

        self.image_occlusion_mode_combo = QComboBox()
        self.image_occlusion_mode_combo.setStyleSheet(input_style + " QComboBox { min-width: 280px; }")
        self.image_occlusion_mode_combo.addItem("Direct Native Image Occlusions (Recommended)", userData="native")
        self.image_occlusion_mode_combo.addItem("Folder Staging (Legacy / Manual)", userData="stage")
        self.image_occlusion_mode_combo.setToolTip(format_tooltip_box("image_occlusion_mode"))
        self.image_occlusion_mode_combo.currentIndexChanged.connect(self._on_io_mode_changed)
        row_mode.addWidget(self.image_occlusion_mode_combo)
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
        self.occlusion_edit.setToolTip(format_tooltip_box("occlusion_dir"))
        _dim_placeholder(self.occlusion_edit)
        occ_browse = QPushButton("Browse...")
        occ_browse.setStyleSheet(btn_style)
        occ_browse.setCursor(Qt.CursorShape.PointingHandCursor)
        occ_browse.clicked.connect(self._browse_occlusion)
        sf_layout.addWidget(self.occlusion_edit, 1)
        sf_layout.addWidget(occ_browse)
        card_io_l.addWidget(self.staging_folder_row)

        row_io_opts = QHBoxLayout()
        row_io_opts.setSpacing(16)
        self.suspend_new_occlusions_cb = QCheckBox("Suspend New Occlusions Until Masked")
        self.suspend_new_occlusions_cb.setToolTip(format_tooltip_box("suspend_new_occlusions"))
        row_io_opts.addLayout(_wrap_with_info(self.suspend_new_occlusions_cb, "suspend_new_occlusions", add_stretch=False))

        self.re_stage_cb = QCheckBox("Re-export Existing Occlusion Images")
        self.re_stage_cb.setToolTip(format_tooltip_box("re_stage_occlusions"))
        row_io_opts.addLayout(_wrap_with_info(self.re_stage_cb, "re_stage_occlusions", add_stretch=False))

        self.freeze_image_occlusions_cb = QCheckBox("Freeze Occlusion Notes (Skip Updates)")
        self.freeze_image_occlusions_cb.setToolTip(format_tooltip_box("freeze_image_occlusions"))
        row_io_opts.addLayout(_wrap_with_info(self.freeze_image_occlusions_cb, "freeze_image_occlusions", add_stretch=False))

        self.auto_unflag_occlusions_cb = QCheckBox("Auto-unflag Completed Occlusions During Sync")
        self.auto_unflag_occlusions_cb.setToolTip(format_tooltip_box("auto_unflag_occlusions"))
        row_io_opts.addLayout(_wrap_with_info(self.auto_unflag_occlusions_cb, "auto_unflag_occlusions", add_stretch=False))
        row_io_opts.addStretch()
        card_io_l.addLayout(row_io_opts)
        layout.addWidget(card_io)

        # Section 3: Media & Note Type Bindings
        card_bind, card_bind_l = _create_section_card(
            "Media & Custom Note Types",
            "Manage deterministic image hashing and optional custom Anki note type bindings.",
        )

        self.hash_media_cb = QCheckBox("Hash Media (Rename images with deterministic 8-character hex hash)")
        self.hash_media_cb.setToolTip(format_tooltip_box("hash_media"))
        card_bind_l.addLayout(_wrap_with_info(self.hash_media_cb, "hash_media"))

        grid_models = QGridLayout()
        grid_models.setContentsMargins(0, 4, 0, 0)
        grid_models.setHorizontalSpacing(16)
        grid_models.setVerticalSpacing(8)

        lbl_bm = QLabel("Basic Note Type: &nbsp;&nbsp;<span style='color: rgba(180, 180, 180, 0.45); font-size: 11px;'>ⓘ</span>")
        lbl_bm.setStyleSheet("font-size: 12px; font-weight: 600; color: rgba(220, 220, 220, 0.95);")
        lbl_bm.setToolTip(format_tooltip_box("basic_model_id"))
        lbl_bm.setCursor(Qt.CursorShape.WhatsThisCursor)
        grid_models.addWidget(lbl_bm, 0, 0)

        self.basic_model_combo = QComboBox()
        self.basic_model_combo.setStyleSheet(input_style + " QComboBox { min-width: 220px; }")
        self.basic_model_combo.setToolTip(format_tooltip_box("basic_model_id"))
        grid_models.addWidget(self.basic_model_combo, 0, 1)

        lbl_cm = QLabel("Cloze Note Type: &nbsp;&nbsp;<span style='color: rgba(180, 180, 180, 0.45); font-size: 11px;'>ⓘ</span>")
        lbl_cm.setStyleSheet("font-size: 12px; font-weight: 600; color: rgba(220, 220, 220, 0.95);")
        lbl_cm.setToolTip(format_tooltip_box("cloze_model_id"))
        lbl_cm.setCursor(Qt.CursorShape.WhatsThisCursor)
        grid_models.addWidget(lbl_cm, 1, 0)

        self.cloze_model_combo = QComboBox()
        self.cloze_model_combo.setStyleSheet(input_style + " QComboBox { min-width: 220px; }")
        self.cloze_model_combo.setToolTip(format_tooltip_box("cloze_model_id"))
        grid_models.addWidget(self.cloze_model_combo, 1, 1)
        grid_models.setColumnStretch(2, 1)

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
        action_row.addStretch()
        card_del_l.addLayout(action_row)
        layout.addWidget(card_del)

        # Section 3: Image Occlusions
        card_io_maint, card_io_maint_l = _create_section_card(
            "Image Occlusion Status in Anki",
            "Notes with TODO_PROCESS_OCCLUSION awaiting masks or ready to unsuspend.",
        )
        self.io_maint_status_label = QLabel("0 image occlusion note(s) awaiting masks.")
        self.io_maint_status_label.setStyleSheet("font-size: 12px; font-weight: 600;")
        card_io_maint_l.addWidget(self.io_maint_status_label)

        io_maint_btns = QHBoxLayout()
        io_maint_btns.setSpacing(8)

        self.view_unmasked_maint_btn = QPushButton("View in Anki Browser")
        self.view_unmasked_maint_btn.setStyleSheet(btn_style)
        self.view_unmasked_maint_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.view_unmasked_maint_btn.clicked.connect(self._on_view_unmasked_browser)

        self.unflag_maint_btn = QPushButton("Unflag Completed Occlusions Now")
        self.unflag_maint_btn.setStyleSheet(btn_style)
        self.unflag_maint_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.unflag_maint_btn.setEnabled(False)
        self.unflag_maint_btn.clicked.connect(self._on_unflag_maint_clicked)

        io_maint_btns.addWidget(self.view_unmasked_maint_btn)
        io_maint_btns.addWidget(self.unflag_maint_btn)
        io_maint_btns.addStretch()
        card_io_maint_l.addLayout(io_maint_btns)
        layout.addWidget(card_io_maint)

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

        # Section 3: Image Occlusions
        if mw is not None and getattr(mw, "col", None) is not None:
            from ..worker import get_occlusion_status
            occ_status = get_occlusion_status(mw.col)
            if occ_status.total > 0:
                if occ_status.ready_to_unflag > 0:
                    self.io_maint_status_label.setText(
                        f"{occ_status.total} note(s) found with TODO_PROCESS_OCCLUSION ({occ_status.awaiting_masks} awaiting masks, {occ_status.ready_to_unflag} ready to unflag)."
                    )
                else:
                    self.io_maint_status_label.setText(
                        f"{occ_status.total} note(s) awaiting masks in Anki."
                    )
            else:
                self.io_maint_status_label.setText("No image occlusion notes awaiting masks in Anki.")
            self.view_unmasked_maint_btn.setEnabled(occ_status.total > 0)
            self.unflag_maint_btn.setEnabled(occ_status.ready_to_unflag > 0)

    def _on_view_unmasked_browser(self) -> None:
        if mw is not None and getattr(mw, "col", None) is not None:
            browser = dialogs.open("Browser", mw)
            browser.search_for(f"tag:{OCCLUSION_TAG}")

    def _on_unflag_maint_clicked(self) -> None:
        if mw is not None and getattr(mw, "col", None) is not None:
            from ..worker import unflag_completed_occlusions
            rep = unflag_completed_occlusions(mw.col, dry_run=False)
            if rep.unflagged_notes_count > 0:
                tooltip(f"✔ Unsuspended {rep.unflagged_notes_count} note(s) ({rep.unsuspended_cards_count} cards) and removed {OCCLUSION_TAG}.")
            else:
                tooltip("No completed occlusions to unflag.")
            self._refresh_maintenance_data()

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
        lbl_dash = QLabel("Open Dashboard: &nbsp;&nbsp;<span style='color: rgba(180, 180, 180, 0.45); font-size: 11px;'>ⓘ</span>")
        lbl_dash.setStyleSheet("font-size: 12px; font-weight: 600; color: rgba(220, 220, 220, 0.95);")
        lbl_dash.setToolTip(format_tooltip_box("shortcuts_open_dashboard"))
        lbl_dash.setCursor(Qt.CursorShape.WhatsThisCursor)
        grid_sc.addWidget(lbl_dash, 0, 0)
        self.sc_dash_edit = QKeySequenceEdit()
        self.sc_dash_edit.setStyleSheet(input_style)
        self.sc_dash_edit.setMaximumWidth(170)
        self.sc_dash_edit.setToolTip(format_tooltip_box("shortcuts_open_dashboard"))
        btn_c_dash = QPushButton("Clear")
        btn_c_dash.setStyleSheet(btn_clear_style)
        btn_c_dash.clicked.connect(lambda: self.sc_dash_edit.setKeySequence(QKeySequence()))
        r_dash = QHBoxLayout()
        r_dash.addWidget(self.sc_dash_edit)
        r_dash.addWidget(btn_c_dash)
        grid_sc.addLayout(r_dash, 0, 1)

        self.sc_dash_warn = QLabel()
        self.sc_dash_warn.setStyleSheet("font-size: 11px; color: #e5c07b; font-weight: 500;")
        self.sc_dash_warn.setVisible(False)
        grid_sc.addWidget(self.sc_dash_warn, 1, 1)

        # 2. Quick
        lbl_quick = QLabel("Quick Sync (New): &nbsp;&nbsp;<span style='color: rgba(180, 180, 180, 0.45); font-size: 11px;'>ⓘ</span>")
        lbl_quick.setStyleSheet("font-size: 12px; font-weight: 600; color: rgba(220, 220, 220, 0.95);")
        lbl_quick.setToolTip(format_tooltip_box("shortcuts_quick_sync"))
        lbl_quick.setCursor(Qt.CursorShape.WhatsThisCursor)
        grid_sc.addWidget(lbl_quick, 2, 0)
        self.sc_quick_edit = QKeySequenceEdit()
        self.sc_quick_edit.setStyleSheet(input_style)
        self.sc_quick_edit.setMaximumWidth(170)
        self.sc_quick_edit.setToolTip(format_tooltip_box("shortcuts_quick_sync"))
        btn_c_quick = QPushButton("Clear")
        btn_c_quick.setStyleSheet(btn_clear_style)
        btn_c_quick.clicked.connect(lambda: self.sc_quick_edit.setKeySequence(QKeySequence()))
        r_quick = QHBoxLayout()
        r_quick.addWidget(self.sc_quick_edit)
        r_quick.addWidget(btn_c_quick)
        grid_sc.addLayout(r_quick, 2, 1)

        self.sc_quick_warn = QLabel()
        self.sc_quick_warn.setStyleSheet("font-size: 11px; color: #e5c07b; font-weight: 500;")
        self.sc_quick_warn.setVisible(False)
        grid_sc.addWidget(self.sc_quick_warn, 3, 1)

        # 3. Full
        lbl_full = QLabel("Sync and Update: &nbsp;&nbsp;<span style='color: rgba(180, 180, 180, 0.45); font-size: 11px;'>ⓘ</span>")
        lbl_full.setStyleSheet("font-size: 12px; font-weight: 600; color: rgba(220, 220, 220, 0.95);")
        lbl_full.setToolTip(format_tooltip_box("shortcuts_full_sync"))
        lbl_full.setCursor(Qt.CursorShape.WhatsThisCursor)
        grid_sc.addWidget(lbl_full, 4, 0)
        self.sc_full_edit = QKeySequenceEdit()
        self.sc_full_edit.setStyleSheet(input_style)
        self.sc_full_edit.setMaximumWidth(170)
        self.sc_full_edit.setToolTip(format_tooltip_box("shortcuts_full_sync"))
        btn_c_full = QPushButton("Clear")
        btn_c_full.setStyleSheet(btn_clear_style)
        btn_c_full.clicked.connect(lambda: self.sc_full_edit.setKeySequence(QKeySequence()))
        r_full = QHBoxLayout()
        r_full.addWidget(self.sc_full_edit)
        r_full.addWidget(btn_c_full)
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

    def _on_vault_text_changed(self, text: str) -> None:
        raw = text.strip()
        if not raw:
            return
        p = Path(raw).expanduser()
        try:
            if p.is_dir() and (p / "m2a.toml").is_file():
                if self._vault_cfg is not None:
                    try:
                        if self._vault_cfg.vault.resolve() == p.resolve():
                            return
                    except Exception:
                        pass
                self._load_vault_from_path(p)
            elif p.is_dir():
                self._refresh_folder_combos()
        except Exception:
            pass

    def _load_vault_from_path(self, dir_path: str | Path) -> None:
        raw = str(dir_path).strip()
        if not raw:
            self._vault_cfg = None
            self._vault_cfg_error = None
            if hasattr(self, "error_banner") and self.error_banner is not None:
                self.error_banner.setVisible(False)
            self._populate_vault_fields()
            return

        p = Path(raw).expanduser().resolve()
        if not p.is_dir():
            return

        toml_file = p / "m2a.toml"
        if toml_file.is_file():
            try:
                self._vault_cfg = self._manager.load_vault(p)
                self._vault_cfg_error = None
                if hasattr(self, "error_banner") and self.error_banner is not None:
                    self.error_banner.setVisible(False)
                self._populate_vault_fields()
            except Exception as exc:
                self._vault_cfg = None
                self._vault_cfg_error = str(exc)
                if hasattr(self, "error_banner") and self.error_banner is not None:
                    self.error_banner.setText(
                        f"<b>Warning: Failed to parse vault configuration (m2a.toml):</b><br>"
                        f"{html.escape(self._vault_cfg_error)}<br>"
                        f"<small>Default settings loaded. Please verify your m2a.toml syntax.</small>"
                    )
                    self.error_banner.setVisible(True)
                self._populate_vault_fields()
        else:
            self._vault_cfg = None
            self._vault_cfg_error = None
            if hasattr(self, "error_banner") and self.error_banner is not None:
                self.error_banner.setVisible(False)
            self._populate_vault_fields()

    def _browse_vault(self) -> None:
        dir_path = QFileDialog.getExistingDirectory(self, "Select Obsidian Vault Folder")
        if dir_path:
            self.vault_edit.setText(dir_path)
            self._load_vault_from_path(dir_path)

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
        fallback_deck = ""
        if hasattr(self, "deck_combo") and self.deck_combo is not None:
            fallback_deck = self.deck_combo.currentText().strip()
        if not fallback_deck:
            avail = self._get_available_deck_names()
            fallback_deck = avail[0] if avail else "Default"
        folders = self._get_available_vault_folders()
        fallback_folder = folders[0] if folders else "folder_path"
        self._set_routing_row(row, fallback_folder, fallback_deck)

    def _remove_routing_row(self) -> None:
        curr = self.routing_table.currentRow()
        if curr >= 0:
            self.routing_table.removeRow(curr)

    def _on_routing_folder_combo_changed(self, combo: QComboBox, text: str) -> None:
        if getattr(self, "_updating_routing_table", False):
            return
        for r in range(self.routing_table.rowCount()):
            if self.routing_table.cellWidget(r, 0) is combo:
                self._updating_routing_table = True
                try:
                    item = self.routing_table.item(r, 0)
                    if item:
                        item.setText(text)
                finally:
                    self._updating_routing_table = False
                break

    def _on_routing_combo_changed(self, combo: QComboBox, text: str) -> None:
        if getattr(self, "_updating_routing_table", False):
            return
        for r in range(self.routing_table.rowCount()):
            if self.routing_table.cellWidget(r, 1) is combo:
                self._updating_routing_table = True
                try:
                    item = self.routing_table.item(r, 1)
                    if item:
                        item.setText(text)
                finally:
                    self._updating_routing_table = False
                break

    def _on_routing_item_changed(self, item: QTableWidgetItem) -> None:
        if getattr(self, "_updating_routing_table", False):
            return
        col = item.column()
        if col in (0, 1):
            widget = self.routing_table.cellWidget(item.row(), col)
            if isinstance(widget, QComboBox) and widget.currentText() != item.text():
                self._updating_routing_table = True
                try:
                    widget.setCurrentText(item.text())
                finally:
                    self._updating_routing_table = False

    def _add_alias_row(self) -> None:
        row = self.alias_table.rowCount()
        self.alias_table.insertRow(row)
        folders = self._get_available_vault_folders()
        fallback_folder = folders[0] if folders else "folder_path"
        self._set_alias_row(row, fallback_folder, "tag_alias")

    def _remove_alias_row(self) -> None:
        curr = self.alias_table.currentRow()
        if curr >= 0:
            self.alias_table.removeRow(curr)

    def _on_alias_folder_combo_changed(self, combo: QComboBox, text: str) -> None:
        if getattr(self, "_updating_alias_table", False):
            return
        for r in range(self.alias_table.rowCount()):
            if self.alias_table.cellWidget(r, 0) is combo:
                self._updating_alias_table = True
                try:
                    item = self.alias_table.item(r, 0)
                    if item:
                        item.setText(text)
                finally:
                    self._updating_alias_table = False
                break

    def _on_alias_combo_changed(self, combo: QComboBox, text: str) -> None:
        if getattr(self, "_updating_alias_table", False):
            return
        for r in range(self.alias_table.rowCount()):
            if self.alias_table.cellWidget(r, 1) is combo:
                self._updating_alias_table = True
                try:
                    item = self.alias_table.item(r, 1)
                    if item:
                        item.setText(text)
                finally:
                    self._updating_alias_table = False
                break

    def _on_alias_item_changed(self, item: QTableWidgetItem) -> None:
        if getattr(self, "_updating_alias_table", False):
            return
        col = item.column()
        if col in (0, 1):
            widget = self.alias_table.cellWidget(item.row(), col)
            if isinstance(widget, QComboBox) and widget.currentText() != item.text():
                self._updating_alias_table = True
                try:
                    widget.setCurrentText(item.text())
                finally:
                    self._updating_alias_table = False

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
        elif getattr(self, "_vault_cfg", None) and getattr(self._vault_cfg, "output_dir", None) and str(self._vault_cfg.output_dir) != ".":
            self.occlusion_edit.setText(str(self._vault_cfg.output_dir))

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
        elif getattr(self, "_vault_cfg", None) and getattr(self._vault_cfg, "anki_basic_model", None):
            idx = self.basic_model_combo.findText(self._vault_cfg.anki_basic_model)
            self.basic_model_combo.setCurrentIndex(idx if idx >= 0 else 0)
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
        elif getattr(self, "_vault_cfg", None) and getattr(self._vault_cfg, "anki_cloze_model", None):
            idx = self.cloze_model_combo.findText(self._vault_cfg.anki_cloze_model)
            self.cloze_model_combo.setCurrentIndex(idx if idx >= 0 else 0)
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
            self.auto_unflag_occlusions_cb.setChecked(False)
            self.routing_table.setRowCount(0)
            self.alias_table.setRowCount(0)
            from markdown2anki.config import DEFAULT_IGNORE
            self.ignore_edit.blockSignals(True)
            self.ignore_edit.setPlainText("\n".join(DEFAULT_IGNORE))
            self.ignore_edit.blockSignals(False)
            self._refresh_folder_combos()
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
        self.auto_unflag_occlusions_cb.setChecked(getattr(self._vault_cfg, "auto_unflag_occlusions", False))

        self.re_stage_occlusions = self._vault_cfg.re_stage_occlusions
        self.re_stage_cb.setChecked(self._vault_cfg.re_stage_occlusions)

        # Ignore globs (set first so folder dropdowns have ignore rules immediately)
        ignore_lines = "\n".join(self._vault_cfg.ignore or [])
        self.ignore_edit.blockSignals(True)
        self.ignore_edit.setPlainText(ignore_lines)
        self.ignore_edit.blockSignals(False)

        # Deck rules
        routing = self._vault_cfg.deck_routing or {}
        self.routing_table.setRowCount(len(routing))
        for r, (folder, target_deck) in enumerate(routing.items()):
            self._set_routing_row(r, folder, target_deck)

        # Tag aliases
        aliases = self._vault_cfg.tag_aliases or {}
        self.alias_table.setRowCount(len(aliases))
        for r, (folder, alias) in enumerate(aliases.items()):
            self._set_alias_row(r, folder, alias)

        self._refresh_folder_combos()

    def _get_routing_table_data(self) -> list[tuple[str, str]]:
        rules: list[tuple[str, str]] = []
        for r in range(self.routing_table.rowCount()):
            f_widget = self.routing_table.cellWidget(r, 0)
            if isinstance(f_widget, QComboBox):
                f_text = f_widget.currentText().strip()
            else:
                f_item = self.routing_table.item(r, 0)
                f_text = f_item.text().strip() if f_item else ""

            d_widget = self.routing_table.cellWidget(r, 1)
            if isinstance(d_widget, QComboBox):
                d_text = d_widget.currentText().strip()
            else:
                d_item = self.routing_table.item(r, 1)
                d_text = d_item.text().strip() if d_item else ""

            rules.append((f_text, d_text))
        return rules

    def _get_alias_table_data(self) -> list[tuple[str, str]]:
        rules: list[tuple[str, str]] = []
        for r in range(self.alias_table.rowCount()):
            f_widget = self.alias_table.cellWidget(r, 0)
            if isinstance(f_widget, QComboBox):
                f_text = f_widget.currentText().strip()
            else:
                f_item = self.alias_table.item(r, 0)
                f_text = f_item.text().strip() if f_item else ""

            a_widget = self.alias_table.cellWidget(r, 1)
            if isinstance(a_widget, QComboBox):
                a_text = a_widget.currentText().strip()
            else:
                a_item = self.alias_table.item(r, 1)
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
            "auto_unflag_occlusions": self.auto_unflag_occlusions_cb.isChecked(),
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
            f_widget = self.routing_table.cellWidget(r, 0)
            if isinstance(f_widget, QComboBox):
                raw_folder = f_widget.currentText().strip()
            else:
                f_item = self.routing_table.item(r, 0)
                raw_folder = f_item.text().strip() if f_item else ""

            d_widget = self.routing_table.cellWidget(r, 1)
            if isinstance(d_widget, QComboBox):
                target_deck = d_widget.currentText().strip()
            else:
                d_item = self.routing_table.item(r, 1)
                target_deck = d_item.text().strip() if d_item else ""

            if not raw_folder:
                continue
            norm_key = _norm_path_key(raw_folder)
            if norm_key in deck_routing:
                QMessageBox.warning(
                    self,
                    "Duplicate Deck Rule",
                    f"The folder path '{norm_key}' is configured more than once in Deck Rules.\n"
                    "Please remove or merge duplicate rules before saving.",
                )
                return
            deck_routing[norm_key] = target_deck

        tag_aliases: dict[str, str] = {}
        for r in range(self.alias_table.rowCount()):
            f_widget = self.alias_table.cellWidget(r, 0)
            if isinstance(f_widget, QComboBox):
                raw_folder = f_widget.currentText().strip()
            else:
                f_item = self.alias_table.item(r, 0)
                raw_folder = f_item.text().strip() if f_item else ""

            a_widget = self.alias_table.cellWidget(r, 1)
            if isinstance(a_widget, QComboBox):
                alias_val = a_widget.currentText().strip()
            else:
                a_item = self.alias_table.item(r, 1)
                alias_val = a_item.text().strip() if a_item else ""

            if not raw_folder:
                continue
            norm_key = _norm_path_key(raw_folder)
            if norm_key in tag_aliases:
                QMessageBox.warning(
                    self,
                    "Duplicate Tag Rule",
                    f"The folder path '{norm_key}' is configured more than once in Tag Rules.\n"
                    "Please remove or merge duplicate rules before saving.",
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
            if (vault_path / "m2a.toml").is_file() and getattr(self, "_vault_cfg_error", None):
                QMessageBox.warning(
                    self,
                    "Invalid Vault Configuration",
                    f"Cannot save settings because the existing m2a.toml failed to parse:\n"
                    f"{self._vault_cfg_error}\n\n"
                    "Please fix or remove the invalid m2a.toml file before saving.",
                )
                return
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
                cfg.auto_unflag_occlusions = self.auto_unflag_occlusions_cb.isChecked()
                cfg.re_stage_occlusions = self.re_stage_cb.isChecked()
                cfg.deck_routing = deck_routing
                cfg.tag_aliases = tag_aliases

                # Synchronize note types and output directory with m2a.toml
                basic_model_name = self.basic_model_combo.currentText().strip()
                if basic_model_name:
                    cfg.anki_basic_model = basic_model_name
                cloze_model_name = self.cloze_model_combo.currentText().strip()
                if cloze_model_name:
                    cfg.anki_cloze_model = cloze_model_name
                if occ_path:
                    cfg.output_dir = occ_path

                # Collect ignore
                ignore_text = self.ignore_edit.toPlainText().strip()
                cfg.ignore = [line.strip() for line in ignore_text.splitlines() if line.strip()]

                self._manager.save_vault(vault_path, cfg)
            except Exception as exc:
                QMessageBox.warning(self, "Warning", f"Could not update m2a.toml: {exc}")

        self._initial_state = self._snapshot_state()
        self.accept()
