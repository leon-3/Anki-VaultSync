from __future__ import annotations

import re
from pathlib import Path

from aqt import mw
from aqt.qt import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
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
    QVBoxLayout,
    QWidget,
)

from ..bridge import ensure_models
from ..profile_config import AddonProfileSettings, ProfileConfigManager
from .help_tooltips import TOOLTIPS


class ConfigWindow(QDialog):
    """Tabbed settings configurator for Anki-VaultSync."""

    def __init__(self, profile_manager: ProfileConfigManager, profile_name: str, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("VaultSync Settings")
        self.resize(700, 560)

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

    def _help_button(self, key: str) -> QPushButton:
        btn = QPushButton("?")
        btn.setFixedSize(26, 26)
        btn.setToolTip("Click for help")
        tip_text = TOOLTIPS.get(key, "No help available.")
        btn.clicked.connect(lambda: QMessageBox.information(self, "Setting Info", tip_text))
        return btn

    def _init_ui(self) -> None:
        main_layout = QVBoxLayout(self)

        self.tabs = QTabWidget()
        main_layout.addWidget(self.tabs)

        # Tab 1: General
        self.tab_general = QWidget()
        self._init_tab_general()
        self.tabs.addTab(self.tab_general, "General")

        # Tab 2: Decks & Routing
        self.tab_routing = QWidget()
        self._init_tab_routing()
        self.tabs.addTab(self.tab_routing, "Decks & Routing")

        # Tab 3: Card Rules & Media
        self.tab_rules = QWidget()
        self._init_tab_rules()
        self.tabs.addTab(self.tab_rules, "Card Rules & Media")

        # Tab 4: Advanced
        self.tab_advanced = QWidget()
        self._init_tab_advanced()
        self.tabs.addTab(self.tab_advanced, "Advanced")

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

        # Default Deck
        layout.addWidget(QLabel("Default Deck:"))
        deck_row = QHBoxLayout()
        self.deck_combo = QComboBox()
        self.deck_combo.setEditable(True)
        if mw is not None and hasattr(mw, "col") and mw.col is not None:
            for name in mw.col.decks.all_names():
                self.deck_combo.addItem(name)
        deck_row.addWidget(self.deck_combo)
        deck_row.addWidget(self._help_button("default_deck"))
        layout.addLayout(deck_row)

        # Base Tag
        layout.addWidget(QLabel("Base Tag:"))
        tag_row = QHBoxLayout()
        self.tag_edit = QLineEdit()
        tag_row.addWidget(self.tag_edit)
        tag_row.addWidget(self._help_button("base_tag"))
        layout.addLayout(tag_row)

        layout.addStretch()

    def _init_tab_routing(self) -> None:
        layout = QVBoxLayout(self.tab_routing)

        layout.addWidget(QLabel("Folder -> Deck Routing Rules (Folder / Course -> Target Anki Deck):"))
        self.routing_table = QTableWidget(0, 2)
        self.routing_table.setHorizontalHeaderLabels(["Vault Folder / Course", "Target Anki Deck"])
        self.routing_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.routing_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        layout.addWidget(self.routing_table)

        btn_row = QHBoxLayout()
        add_btn = QPushButton("Add Rule")
        add_btn.clicked.connect(self._add_routing_row)
        remove_btn = QPushButton("Remove Selected")
        remove_btn.clicked.connect(self._remove_routing_row)
        btn_row.addWidget(add_btn)
        btn_row.addWidget(remove_btn)
        btn_row.addStretch()
        btn_row.addWidget(self._help_button("decks"))
        layout.addLayout(btn_row)

        layout.addWidget(QLabel("Ignored Folders / Globs (one per line):"))
        self.ignore_edit = QPlainTextEdit()
        layout.addWidget(self.ignore_edit)

    def _init_tab_rules(self) -> None:
        layout = QVBoxLayout(self.tab_rules)

        self.hash_media_cb = QCheckBox("Hash Media (Deterministic SHA-256 filenames)")
        row1 = QHBoxLayout()
        row1.addWidget(self.hash_media_cb)
        row1.addWidget(self._help_button("hash_media"))
        row1.addStretch()
        layout.addLayout(row1)

        self.cloze_cb = QCheckBox("Enable Native Cloze Notes (questions titled 'Cloze')")
        row2 = QHBoxLayout()
        row2.addWidget(self.cloze_cb)
        row2.addWidget(self._help_button("cloze_notes"))
        row2.addStretch()
        layout.addLayout(row2)

        self.re_stage_cb = QCheckBox("Re-stage Image Occlusions on Update")
        row3 = QHBoxLayout()
        row3.addWidget(self.re_stage_cb)
        row3.addWidget(self._help_button("re_stage_occlusions"))
        row3.addStretch()
        layout.addLayout(row3)

        layout.addStretch()

    def _init_tab_advanced(self) -> None:
        layout = QVBoxLayout(self.tab_advanced)

        self.show_diff_cb = QCheckBox("Show Diff Preview Before Updating Modified Cards")
        row1 = QHBoxLayout()
        row1.addWidget(self.show_diff_cb)
        row1.addWidget(self._help_button("show_diff_before_update"))
        row1.addStretch()
        layout.addLayout(row1)

        self.auto_close_cb = QCheckBox("Auto-close Dashboard on Successful Sync")
        row2 = QHBoxLayout()
        row2.addWidget(self.auto_close_cb)
        row2.addWidget(self._help_button("auto_close_on_success"))
        row2.addStretch()
        layout.addLayout(row2)

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

        layout.addStretch()

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
            m = mw.col.models.by_name(text)
            if m:
                return int(m["id"])
        return None

    def _browse_vault(self) -> None:
        dir_path = QFileDialog.getExistingDirectory(self, "Select Obsidian Vault Folder")
        if dir_path:
            self.vault_edit.setText(dir_path)
            # Try to load vault config for newly selected path
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
        # Profile settings
        if self._profile_settings.vault_path:
            self.vault_edit.setText(str(self._profile_settings.vault_path))
        if self._profile_settings.occlusion_dir:
            self.occlusion_edit.setText(str(self._profile_settings.occlusion_dir))

        self.auto_close_cb.setChecked(self._profile_settings.auto_close_on_success)
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
        self.re_stage_cb.setChecked(getattr(self._vault_cfg, "re_stage_occlusions", False))

        # Deck routing table (Folder / Course -> Target Anki Deck)
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
            auto_close_on_success=self.auto_close_cb.isChecked(),
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

                # If subdirectories has the academic default ("Anki - Lectures"), clear it to enable recursive mode (Mode B)
                # unless the user has specifically configured non-default subdirectories.
                if getattr(cfg, "subdirectories", None) == {"Anki - Lectures": "Lectures", "Anki - Exercises": "Exercises"}:
                    cfg.subdirectories = {}

                # Collect ignore
                ignore_text = self.ignore_edit.toPlainText().strip()
                cfg.ignore = [line.strip() for line in ignore_text.splitlines() if line.strip()]

                self._manager.save_vault(vault_path, cfg)
            except Exception as exc:
                QMessageBox.warning(self, "Warning", f"Could not update m2a.toml: {exc}")

        self.accept()
