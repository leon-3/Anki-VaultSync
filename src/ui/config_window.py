from __future__ import annotations

import re
from pathlib import Path

from aqt import dialogs, mw
from aqt.qt import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QComboBox,
    QCursor,
    QDesktopServices,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QToolTip,
    QUrl,
    Qt,
    QVBoxLayout,
    QWidget,
)
from ..logger import clear_log, get_log_file_path, read_log_text

from ..bridge import ensure_models
from ..worker import build_sync_plan
from ..profile_config import AddonProfileSettings, ProfileConfigManager
from .help_tooltips import TOOLTIPS


class HelpBadge(QLabel):
    """Clean, circular help badge that shows a tooltip on hover (zero delay) and on click."""

    def __init__(self, tip_text: str, parent=None) -> None:
        super().__init__("?", parent)
        self.tip_text = tip_text
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setFixedSize(18, 18)
        self.setToolTip(tip_text)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setStyleSheet("""
            QLabel {
                border: 1px solid rgba(130, 130, 130, 0.7);
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
        QToolTip.showText(QCursor.pos(), self.tip_text, self)

    def mousePressEvent(self, event) -> None:
        super().mousePressEvent(event)
        QToolTip.showText(QCursor.pos(), self.tip_text, self)


class ConfigWindow(QDialog):
    """Tabbed settings configurator for Anki-VaultSync."""

    def __init__(
        self,
        profile_manager: ProfileConfigManager,
        profile_name: str,
        parent=None,
        initial_tab: int | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("VaultSync Settings")
        self.resize(720, 580)

        self._manager = profile_manager
        self._profile_name = profile_name
        self._profile_settings = self._manager.load(profile_name)

        self._vault_cfg = None
        if self._profile_settings.vault_path and self._profile_settings.vault_path.is_dir():
            try:
                self._vault_cfg = self._manager.load_vault(self._profile_settings.vault_path)
            except Exception:
                self._vault_cfg = None

        self._init_ui()
        self._populate_fields()
        if initial_tab is not None and 0 <= initial_tab < self.tabs.count():
            self.tabs.setCurrentIndex(initial_tab)

    def _help_button(self, key: str) -> HelpBadge:
        tip_text = TOOLTIPS.get(key, "No help available.")
        return HelpBadge(tip_text)

    def _init_ui(self) -> None:
        main_layout = QVBoxLayout(self)

        self.tabs = QTabWidget()
        main_layout.addWidget(self.tabs)

        # Tab 1: General
        self.tab_general = QWidget()
        self._init_tab_general()
        self.tabs.addTab(self.tab_general, "General")

        # Tab 2: Decks and Routing
        self.tab_routing = QWidget()
        self._init_tab_routing()
        self.tabs.addTab(self.tab_routing, "Decks and Routing")

        # Tab 3: Card Rules and Media
        self.tab_rules = QWidget()
        self._init_tab_rules()
        self.tabs.addTab(self.tab_rules, "Card Rules and Media")

        # Tab 4: Advanced
        self.tab_advanced = QWidget()
        self._init_tab_advanced()
        self.tabs.addTab(self.tab_advanced, "Advanced")

        # Tab 5: Logs
        self.tab_logs = QWidget()
        self._init_tab_logs()
        self.tabs.addTab(self.tab_logs, "Logs")

        # Dialog Buttons
        button_box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        button_box.accepted.connect(self._on_save)
        button_box.rejected.connect(self.reject)
        main_layout.addWidget(button_box)

    def _init_tab_general(self) -> None:
        layout = QVBoxLayout(self.tab_general)

        # Vault Path
        layout.addWidget(QLabel("Vault Directory:"))
        vault_row = QHBoxLayout()
        self.vault_edit = QLineEdit()
        vault_browse = QPushButton("Browse...")
        vault_browse.clicked.connect(self._browse_vault)
        vault_row.addWidget(self.vault_edit)
        vault_row.addWidget(vault_browse)
        vault_row.addWidget(self._help_button("vault_path"))
        layout.addLayout(vault_row)

        # Image Occlusion Export Folder
        layout.addWidget(QLabel("Image Occlusion Export Folder:"))
        occ_row = QHBoxLayout()
        self.occlusion_edit = QLineEdit()
        occ_browse = QPushButton("Browse...")
        occ_browse.clicked.connect(self._browse_occlusion)
        occ_row.addWidget(self.occlusion_edit)
        occ_row.addWidget(occ_browse)
        occ_row.addWidget(self._help_button("occlusion_dir"))
        layout.addLayout(occ_row)

        # Default Deck Fallback
        layout.addWidget(QLabel("Default Target Deck (Fallback):"))
        deck_row = QHBoxLayout()
        self.deck_combo = QComboBox()
        self.deck_combo.setEditable(True)
        if mw is not None and hasattr(mw, "col") and mw.col is not None:
            for d in mw.col.decks.all_names_and_ids():
                self.deck_combo.addItem(d.name)
        deck_row.addWidget(self.deck_combo)
        deck_row.addWidget(self._help_button("default_deck"))
        layout.addLayout(deck_row)

        # Base Tag Prefix
        layout.addWidget(QLabel("Base Tag Prefix:"))
        tag_row = QHBoxLayout()
        self.tag_edit = QLineEdit()
        tag_row.addWidget(self.tag_edit)
        tag_row.addWidget(self._help_button("base_tag"))
        layout.addLayout(tag_row)

        layout.addStretch()

    def _init_tab_routing(self) -> None:
        layout = QVBoxLayout(self.tab_routing)

        desc = QLabel(
            "<b>Subdirectory Routing Rules:</b><br>"
            "Map subfolders in your Obsidian vault directly to specific target Anki decks.<br>"
            "<i>(Relative folder paths match from vault root.)</i>"
        )
        desc.setWordWrap(True)
        layout.addWidget(desc)

        self.routing_table = QTableWidget(0, 2)
        self.routing_table.setHorizontalHeaderLabels(["Vault Folder", "Target Deck"])
        self.routing_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.routing_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.routing_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        layout.addWidget(self.routing_table)

        btn_row = QHBoxLayout()
        add_btn = QPushButton("Add Routing Rule")
        add_btn.clicked.connect(self._add_routing_row)
        remove_btn = QPushButton("Remove Selected Rule")
        remove_btn.clicked.connect(self._remove_routing_row)
        btn_row.addWidget(add_btn)
        btn_row.addWidget(remove_btn)
        btn_row.addWidget(self._help_button("decks"))
        btn_row.addStretch()
        layout.addLayout(btn_row)

    def _init_tab_rules(self) -> None:
        layout = QVBoxLayout(self.tab_rules)

        self.hash_media_cb = QCheckBox("Hash Media (Rename media files to deterministic content hashes)")
        row1 = QHBoxLayout()
        row1.addWidget(self.hash_media_cb)
        row1.addWidget(self._help_button("hash_media"))
        row1.addStretch()
        layout.addLayout(row1)

        self.cloze_cb = QCheckBox("Enable Cloze Syntax Processing")
        row2 = QHBoxLayout()
        row2.addWidget(self.cloze_cb)
        row2.addWidget(self._help_button("cloze_notes"))
        row2.addStretch()
        layout.addLayout(row2)

        self.re_stage_cb = QCheckBox("Re-stage Already Flagged Image Occlusions")
        row3 = QHBoxLayout()
        row3.addWidget(self.re_stage_cb)
        row3.addWidget(self._help_button("re_stage_occlusions"))
        row3.addStretch()
        layout.addLayout(row3)

        layout.addSpacing(12)
        layout.addWidget(QLabel("Ignore Patterns (one per line, e.g. .obsidian, templates):"))
        self.ignore_edit = QPlainTextEdit()
        self.ignore_edit.setMaximumHeight(120)
        layout.addWidget(self.ignore_edit)

        layout.addStretch()

    def _init_tab_advanced(self) -> None:
        layout = QVBoxLayout(self.tab_advanced)

        self.show_diff_cb = QCheckBox("Show Diff Preview Before Updating Modified Cards")
        row1 = QHBoxLayout()
        row1.addWidget(self.show_diff_cb)
        row1.addWidget(self._help_button("show_diff_before_update"))
        row1.addStretch()
        layout.addLayout(row1)

        layout.addSpacing(12)
        layout.addWidget(QLabel("<b>Note Type Bindings:</b>"))

        # Basic Note Type selector
        layout.addWidget(QLabel("Basic Note Type:"))
        b_row = QHBoxLayout()
        self.basic_model_combo = QComboBox()
        self.basic_model_combo.addItem("Default (M2A Basic)", userData=None)
        if mw is not None and hasattr(mw, "col") and mw.col is not None:
            for m in mw.col.models.all():
                if m.get("type", 0) == 0:  # standard model
                    self.basic_model_combo.addItem(m["name"], userData=int(m["id"]))
        b_row.addWidget(self.basic_model_combo)
        b_row.addWidget(self._help_button("basic_model_id"))
        layout.addLayout(b_row)

        # Cloze Note Type selector
        layout.addWidget(QLabel("Cloze Note Type:"))
        c_row = QHBoxLayout()
        self.cloze_model_combo = QComboBox()
        self.cloze_model_combo.addItem("Default (M2A Cloze)", userData=None)
        if mw is not None and hasattr(mw, "col") and mw.col is not None:
            for m in mw.col.models.all():
                if m.get("type", 0) == 1:  # cloze model
                    self.cloze_model_combo.addItem(m["name"], userData=int(m["id"]))
        c_row.addWidget(self.cloze_model_combo)
        c_row.addWidget(self._help_button("cloze_model_id"))
        layout.addLayout(c_row)

        layout.addSpacing(15)
        repair_btn = QPushButton("Repair / Re-apply M2A Note Type Templates")
        repair_btn.clicked.connect(self._repair_templates)
        row3 = QHBoxLayout()
        row3.addWidget(repair_btn)
        row3.addWidget(self._help_button("repair_templates"))
        row3.addStretch()
        layout.addLayout(row3)

        self.orphans_btn = QPushButton("Find & View Orphaned Cards in Browser")
        self.orphans_btn.clicked.connect(self._view_orphans_in_browser)
        row4 = QHBoxLayout()
        row4.addWidget(self.orphans_btn)
        row4.addWidget(self._help_button("orphaned_cards"))
        row4.addStretch()
        layout.addLayout(row4)

        layout.addStretch()

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

        try:
            plan = build_sync_plan(mw.col, vault_path, basic_model_id=basic_id, cloze_model_id=cloze_id)
            if not plan.orphans:
                QMessageBox.information(
                    self, "No Orphans Found", "All cards in Anki match cards in your Obsidian vault. Zero orphaned cards!"
                )
                return

            nids = [str(o.note_id) for o in plan.orphans]
            query = f"nid:{','.join(nids)}"
            browser = dialogs.open("Browser", mw)
            browser.search_for(query)
        except Exception as exc:
            QMessageBox.critical(self, "Error", f"Failed to detect orphaned cards: {exc}")

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

    def _add_routing_row(self) -> None:
        row = self.routing_table.rowCount()
        self.routing_table.insertRow(row)
        self.routing_table.setItem(row, 0, QTableWidgetItem("folder_name"))
        self.routing_table.setItem(row, 1, QTableWidgetItem("Target Deck"))

    def _remove_routing_row(self) -> None:
        curr = self.routing_table.currentRow()
        if curr >= 0:
            self.routing_table.removeRow(curr)

    def _repair_templates(self) -> None:
        if mw is None or not hasattr(mw, "col") or mw.col is None:
            QMessageBox.warning(self, "Unavailable", "Anki collection is not currently open.")
            return
        try:
            basic_id = self._get_selected_model_id(self.basic_model_combo)
            cloze_id = self._get_selected_model_id(self.cloze_model_combo)
            ensure_models(
                mw.col,
                repair_templates=True,
                basic_model_id=basic_id,
                cloze_model_id=cloze_id,
            )
            mw.col.save()
            mw.reset()
            QMessageBox.information(self, "Success", "Note type templates repaired successfully.")
        except Exception as exc:
            QMessageBox.critical(self, "Error", f"Failed to repair templates: {exc}")

    def _populate_fields(self) -> None:
        if self._profile_settings.vault_path:
            self.vault_edit.setText(str(self._profile_settings.vault_path))
        if self._profile_settings.occlusion_dir:
            self.occlusion_edit.setText(str(self._profile_settings.occlusion_dir))

        self.show_diff_cb.setChecked(self._profile_settings.show_diff_before_update)

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
                if mw is not None and hasattr(mw, "col") and mw.col is not None:
                    try:
                        m = mw.col.models.get(self._profile_settings.basic_model_id)
                        if m is not None:
                            name = m["name"]
                    except Exception:
                        pass
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
                if mw is not None and hasattr(mw, "col") and mw.col is not None:
                    try:
                        m = mw.col.models.get(self._profile_settings.cloze_model_id)
                        if m is not None:
                            name = m["name"]
                    except Exception:
                        pass
                display_text = name or f"Note Type ({self._profile_settings.cloze_model_id})"
                self.cloze_model_combo.addItem(display_text, userData=self._profile_settings.cloze_model_id)
                self.cloze_model_combo.setCurrentIndex(self.cloze_model_combo.count() - 1)
        else:
            self.cloze_model_combo.setCurrentIndex(0)

        self._populate_vault_fields()

    def _populate_vault_fields(self) -> None:
        if self._vault_cfg is None:
            self.hash_media_cb.setChecked(True)
            self.cloze_cb.setChecked(True)
            return

        self.deck_combo.setCurrentText(self._vault_cfg.deck)
        self.tag_edit.setText(self._vault_cfg.base_tag)
        self.hash_media_cb.setChecked(self._vault_cfg.hash_media)
        self.cloze_cb.setChecked(self._vault_cfg.cloze_notes)
        self.re_stage_cb.setChecked(self._vault_cfg.re_stage_occlusions)

        # Deck routing
        decks = self._vault_cfg.decks or {}
        self.routing_table.setRowCount(len(decks))
        for r, (folder, target_deck) in enumerate(decks.items()):
            self.routing_table.setItem(r, 0, QTableWidgetItem(folder))
            self.routing_table.setItem(r, 1, QTableWidgetItem(target_deck))

        # Ignore globs
        ignore_lines = "\n".join(self._vault_cfg.ignore or [])
        self.ignore_edit.setPlainText(ignore_lines)

    def _on_save(self) -> None:
        vault_str = self.vault_edit.text().strip()
        vault_path = Path(vault_str).expanduser().resolve() if vault_str else None

        occ_str = self.occlusion_edit.text().strip()
        occ_path = Path(occ_str).expanduser().resolve() if occ_str else None

        basic_id = self._get_selected_model_id(self.basic_model_combo)
        cloze_id = self._get_selected_model_id(self.cloze_model_combo)

        # 1. Save profile settings
        new_settings = AddonProfileSettings(
            profile_name=self._profile_name,
            vault_path=vault_path,
            occlusion_dir=occ_path,
            auto_close_on_success=False,
            show_diff_before_update=self.show_diff_cb.isChecked(),
            basic_model_id=basic_id,
            cloze_model_id=cloze_id,
        )
        self._manager.save(new_settings)

        # 2. Save vault config to m2a.toml if vault path is valid
        if vault_path and vault_path.is_dir():
            try:
                cfg = self._manager.load_vault(vault_path)
                cfg.deck = self.deck_combo.currentText().strip() or "Default"
                cfg.base_tag = self.tag_edit.text().strip()
                cfg.hash_media = self.hash_media_cb.isChecked()
                cfg.cloze_notes = self.cloze_cb.isChecked()
                cfg.re_stage_occlusions = self.re_stage_cb.isChecked()

                # Collect deck routing
                decks: dict[str, str] = {}
                for r in range(self.routing_table.rowCount()):
                    f_item = self.routing_table.item(r, 0)
                    d_item = self.routing_table.item(r, 1)
                    if f_item and d_item and f_item.text().strip():
                        decks[f_item.text().strip()] = d_item.text().strip()
                cfg.decks = decks

                if getattr(cfg, "subdirectories", None) == {"Anki - Lectures": "Lectures", "Anki - Exercises": "Exercises"}:
                    cfg.subdirectories = {}

                # Collect ignore
                ignore_text = self.ignore_edit.toPlainText().strip()
                cfg.ignore = [line.strip() for line in ignore_text.splitlines() if line.strip()]

                self._manager.save_vault(vault_path, cfg)
            except Exception as exc:
                QMessageBox.warning(self, "Warning", f"Could not update m2a.toml: {exc}")

        self.accept()

    def _init_tab_logs(self) -> None:
        layout = QVBoxLayout(self.tab_logs)
        layout.setSpacing(10)
        layout.setContentsMargins(12, 12, 12, 12)

        # Header with actions
        header = QHBoxLayout()
        desc = QLabel("Operation history across sessions (<b>newest entries on top</b>):")
        desc.setStyleSheet("font-size: 13px;")
        header.addWidget(desc)
        header.addStretch()

        refresh_btn = QPushButton("Refresh")
        refresh_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        refresh_btn.clicked.connect(self._refresh_logs)
        header.addWidget(refresh_btn)

        copy_btn = QPushButton("Copy All")
        copy_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        copy_btn.clicked.connect(self._copy_logs)
        header.addWidget(copy_btn)

        open_file_btn = QPushButton("Open File")
        open_file_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        open_file_btn.clicked.connect(self._open_log_file)
        header.addWidget(open_file_btn)

        clear_btn = QPushButton("Clear Log")
        clear_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        clear_btn.clicked.connect(self._clear_logs)
        header.addWidget(clear_btn)

        layout.addLayout(header)

        self.log_edit = QPlainTextEdit()
        self.log_edit.setReadOnly(True)
        self.log_edit.setStyleSheet("""
            QPlainTextEdit {
                font-family: monospace;
                font-size: 12px;
                border: 1px solid palette(midlight);
                border-radius: 6px;
                padding: 8px;
                background: palette(alternate-base);
            }
        """)
        layout.addWidget(self.log_edit)

        self._refresh_logs()

    def _refresh_logs(self) -> None:
        text = read_log_text(newest_first=True)
        self.log_edit.setPlainText(text or "(Log is currently empty)")

    def _copy_logs(self) -> None:
        clipboard = QApplication.clipboard()
        if clipboard:
            clipboard.setText(self.log_edit.toPlainText())
            QMessageBox.information(self, "Copied", "Log contents copied to clipboard.")

    def _open_log_file(self) -> None:
        log_path = get_log_file_path()
        if not log_path.exists():
            log_path.parent.mkdir(parents=True, exist_ok=True)
            log_path.touch()
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(log_path)))

    def _clear_logs(self) -> None:
        res = QMessageBox.question(
            self,
            "Clear Log",
            "Are you sure you want to clear the entire log history?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if res == QMessageBox.StandardButton.Yes:
            clear_log()
            self._refresh_logs()
