"""Interactive multi-step onboarding wizard for Anki-VaultSync."""
from __future__ import annotations

import logging
from pathlib import Path
import tempfile
from typing import TYPE_CHECKING, Any
import uuid

from aqt import mw
from aqt.qt import (
    QApplication,
    QCheckBox,
    QColor,
    QComboBox,
    QDialog,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QMovie,
    QPainter,
    QPalette,
    QPen,
    QPixmap,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QSizePolicy,
    QStackedWidget,
    QTimer,
    QVBoxLayout,
    QWidget,
    Qt,
    pyqtSignal,
)
from aqt.utils import openLink
from markdown2anki.config import Config, load_vault_config, save_vault_config

from src.profile_config import AddonProfileSettings, ProfileConfigManager
from src.ui.onboarding_state import OnboardingState
from src.ui.shortcuts import ShortcutConfigWidget
from src.ui.welcome_note import create_welcome_note

logger = logging.getLogger("anki_vaultsync")

TOTAL_STEPS = 5
STEP_TITLES = ["Welcome", "Vault & Deck", "Card Types", "Workflow", "Ready"]


class StepHeaderWidget(QWidget):
    """Custom stepper header rendering numbered nodes with connecting lines."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._current_step = 0
        self.setFixedHeight(64)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def set_current_step(self, step: int) -> None:
        self._current_step = max(0, min(step, TOTAL_STEPS - 1))
        self.update()

    def paintEvent(self, event: Any) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        palette = self.palette()
        highlight_color = palette.color(QPalette.ColorRole.Highlight)
        highlight_text = palette.color(QPalette.ColorRole.HighlightedText)
        mid_color = palette.color(QPalette.ColorRole.Mid)
        text_color = palette.color(QPalette.ColorRole.WindowText)
        dim_text = QColor(text_color)
        dim_text.setAlpha(150)

        width = self.width()
        margin = 45
        available_w = width - 2 * margin
        spacing = available_w / (TOTAL_STEPS - 1)
        node_radius = 12
        y_circle = 20

        # Draw connecting lines
        pen = QPen(mid_color, 2)
        painter.setPen(pen)
        for i in range(TOTAL_STEPS - 1):
            x1 = margin + i * spacing + node_radius
            x2 = margin + (i + 1) * spacing - node_radius
            if i < self._current_step:
                pen.setColor(highlight_color)
            else:
                pen.setColor(mid_color)
            painter.setPen(pen)
            painter.drawLine(int(x1), y_circle, int(x2), y_circle)

        # Draw nodes & labels
        font = painter.font()
        for i in range(TOTAL_STEPS):
            x = int(margin + i * spacing)

            if i < self._current_step:
                # Completed
                painter.setBrush(highlight_color)
                painter.setPen(Qt.PenStyle.NoPen)
                painter.drawEllipse(x - node_radius, y_circle - node_radius, node_radius * 2, node_radius * 2)

                painter.setPen(highlight_text)
                font.setPointSize(9)
                font.setBold(True)
                painter.setFont(font)
                painter.drawText(x - node_radius, y_circle - node_radius, node_radius * 2, node_radius * 2,
                                 Qt.AlignmentFlag.AlignCenter, "✓")

                label_color = text_color
            elif i == self._current_step:
                # Active
                painter.setBrush(highlight_color)
                painter.setPen(QPen(highlight_color, 2))
                painter.drawEllipse(x - node_radius, y_circle - node_radius, node_radius * 2, node_radius * 2)

                painter.setPen(highlight_text)
                font.setPointSize(9)
                font.setBold(True)
                painter.setFont(font)
                painter.drawText(x - node_radius, y_circle - node_radius, node_radius * 2, node_radius * 2,
                                 Qt.AlignmentFlag.AlignCenter, str(i + 1))

                label_color = highlight_color
            else:
                # Pending
                painter.setBrush(palette.color(QPalette.ColorRole.Base))
                painter.setPen(QPen(mid_color, 2))
                painter.drawEllipse(x - node_radius, y_circle - node_radius, node_radius * 2, node_radius * 2)

                painter.setPen(dim_text)
                font.setPointSize(9)
                font.setBold(False)
                painter.setFont(font)
                painter.drawText(x - node_radius, y_circle - node_radius, node_radius * 2, node_radius * 2,
                                 Qt.AlignmentFlag.AlignCenter, str(i + 1))

                label_color = dim_text

            # Label below node
            font.setPointSize(8)
            font.setBold(i == self._current_step)
            painter.setFont(font)
            painter.setPen(label_color)
            painter.drawText(x - 50, y_circle + 14, 100, 20, Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop,
                             STEP_TITLES[i])


class OnboardingPageBase(QWidget):
    """Base widget for wizard pages providing consistent scroll area and media lifecycle."""

    validity_changed = pyqtSignal(bool)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._movie: QMovie | None = None

        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        # Media slot
        self.media_container = QWidget()
        self.media_container.setMaximumHeight(160)
        self.media_container.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.media_layout = QVBoxLayout(self.media_container)
        self.media_layout.setContentsMargins(16, 8, 16, 8)
        self.media_label = QLabel()
        self.media_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.media_layout.addWidget(self.media_label)
        self.media_container.setVisible(False)
        main_layout.addWidget(self.media_container)

        # Scrollable content area
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.content_widget = QWidget()
        self.content_layout = QVBoxLayout(self.content_widget)
        self.content_layout.setContentsMargins(28, 16, 28, 20)
        self.content_layout.setSpacing(16)
        self.scroll.setWidget(self.content_widget)
        main_layout.addWidget(self.scroll)

    def set_media_asset(self, asset_path: Path | None, is_animated: bool = False) -> None:
        if self._movie:
            self._movie.stop()
            self._movie = None

        if not asset_path or not asset_path.is_file():
            self.media_container.setVisible(False)
            return

        if is_animated or asset_path.suffix.lower() == ".gif":
            self._movie = QMovie(str(asset_path))
            self.media_label.setMovie(self._movie)
            self._movie.start()
        else:
            pix = QPixmap(str(asset_path))
            if not pix.isNull():
                dpr = self.devicePixelRatioF() if hasattr(self, "devicePixelRatioF") else 1.0
                scaled = pix.scaledToHeight(int(140 * dpr), Qt.TransformationMode.SmoothTransformation)
                scaled.setDevicePixelRatio(dpr)
                self.media_label.setPixmap(scaled)

        self.media_container.setVisible(True)

    def showEvent(self, event: Any) -> None:
        super().showEvent(event)
        if self._movie and self._movie.isValid():
            self._movie.setPaused(False)

    def hideEvent(self, event: Any) -> None:
        super().hideEvent(event)
        if self._movie and self._movie.isValid():
            self._movie.setPaused(True)

    def _create_header(self, title: str, subtitle: str) -> None:
        header_box = QVBoxLayout()
        header_box.setSpacing(4)

        t_lbl = QLabel(title)
        t_lbl.setStyleSheet("font-size: 18px; font-weight: bold; color: palette(window-text);")
        sub_lbl = QLabel(subtitle)
        sub_lbl.setStyleSheet("font-size: 13px; color: palette(window-text); opacity: 0.85;")
        sub_lbl.setWordWrap(True)

        header_box.addWidget(t_lbl)
        header_box.addWidget(sub_lbl)
        self.content_layout.addLayout(header_box)
        self.content_layout.addSpacing(4)

    def on_enter(self, state: OnboardingState) -> None:
        pass

    def on_leave(self, state: OnboardingState) -> None:
        pass

    def is_valid(self) -> bool:
        return True

    def validate_page(self) -> bool:
        return True


class PageWelcome(OnboardingPageBase):
    """Step 1: Welcome message, mental model, and card syntax cheat sheet link."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._init_ui()

    def _init_ui(self) -> None:
        self._create_header(
            "Welcome to VaultSync",
            "Seamlessly synchronize Markdown flashcards from your Obsidian vault into Anki."
        )

        desc = QLabel(
            "VaultSync tracks your Obsidian markdown files and converts formatted notes into Anki flashcards "
            "directly and without duplicates.\n\n"
            "• Write notes normally in Obsidian — no complex tags or export steps required.\n"
            "• Deterministic note IDs guarantee cards are updated in place without resetting review intervals.\n"
            "• Full support for Basic Q&A cards, Cloze deletions, and Image Occlusions."
        )
        desc.setWordWrap(True)
        desc.setStyleSheet("font-size: 12px; line-height: 1.5; color: palette(window-text);")
        self.content_layout.addWidget(desc)

        preview_card = QFrame()
        preview_card.setStyleSheet("QFrame { border: 1px solid palette(mid); border-radius: 8px; background: palette(base); }")
        pc_layout = QVBoxLayout(preview_card)
        pc_layout.setContentsMargins(14, 12, 14, 12)
        pc_layout.setSpacing(10)

        preview_title = QLabel("Markdown Card Syntax Examples:")
        preview_title.setStyleSheet("font-weight: 600; font-size: 12px; color: palette(text); border: none;")
        pc_layout.addWidget(preview_title)

        cards_box = QHBoxLayout()
        cards_box.setSpacing(12)

        # Basic QA Box
        box1 = QFrame()
        box1.setStyleSheet("QFrame { border: 1px dashed palette(mid); border-radius: 6px; padding: 6px; }")
        l1 = QVBoxLayout(box1)
        l1.addWidget(QLabel("<b>Basic Card:</b>"))
        code1 = QLabel("# Biology\n---\nWhat is photosynthesis?\nLight-driven synthesis of sugars.")
        code1.setStyleSheet("font-family: monospace; font-size: 11px;")
        l1.addWidget(code1)
        cards_box.addWidget(box1)

        # Cloze Box
        box2 = QFrame()
        box2.setStyleSheet("QFrame { border: 1px dashed palette(mid); border-radius: 6px; padding: 6px; }")
        l2 = QVBoxLayout(box2)
        l2.addWidget(QLabel("<b>Cloze Note:</b>"))
        code2 = QLabel("# Genetics\n---\nCloze\nDNA consists of {{c1::nucleotides}}.")
        code2.setStyleSheet("font-family: monospace; font-size: 11px;")
        l2.addWidget(code2)
        cards_box.addWidget(box2)

        pc_layout.addLayout(cards_box)
        self.content_layout.addWidget(preview_card)

        # Documentation Link
        link_btn = QPushButton("Open Syntax Documentation & Cheat Sheet ↗")
        link_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        link_btn.setStyleSheet("""
            QPushButton {
                border: 1px solid palette(highlight);
                border-radius: 6px;
                padding: 6px 14px;
                background-color: palette(button);
                color: palette(highlight);
                font-weight: 500;
            }
            QPushButton:hover {
                background-color: palette(highlight);
                color: palette(highlighted-text);
            }
        """)
        link_btn.setAutoDefault(False)
        link_btn.clicked.connect(lambda: openLink("https://github.com/leon-3/Anki-VaultSync#markdown-card-syntax"))

        link_layout = QHBoxLayout()
        link_layout.addWidget(link_btn)
        link_layout.addStretch()
        self.content_layout.addLayout(link_layout)
        self.content_layout.addStretch()


class PageVaultDeck(OnboardingPageBase):
    """Step 2: Obsidian Vault location picker and Default Anki target deck selector."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._debounce_timer = QTimer(self)
        self._debounce_timer.setSingleShot(True)
        self._debounce_timer.timeout.connect(self._check_vault_path_async)
        self._init_ui()

    def _init_ui(self) -> None:
        self._create_header(
            "Select Your Obsidian Vault",
            "Choose your local notes folder and a default Anki deck for imported flashcards."
        )

        input_style = """
            QLineEdit, QComboBox {
                border: 1px solid palette(mid);
                border-radius: 6px;
                padding: 6px 10px;
                background-color: palette(base);
                color: palette(text);
                font-size: 12px;
            }
            QLineEdit:focus, QComboBox:focus {
                border: 1px solid palette(highlight);
            }
        """
        btn_style = """
            QPushButton {
                border: 1px solid palette(mid);
                border-radius: 6px;
                padding: 6px 14px;
                background-color: palette(button);
                color: palette(button-text);
                font-size: 12px;
            }
            QPushButton:hover {
                background-color: palette(highlight);
                color: palette(highlighted-text);
            }
        """

        # 1. Vault path
        lbl_vault = QLabel("Obsidian Vault Folder:")
        lbl_vault.setStyleSheet("font-size: 13px; font-weight: 600; color: palette(window-text);")
        self.content_layout.addWidget(lbl_vault)

        row_vault = QHBoxLayout()
        row_vault.setSpacing(8)
        self.vault_edit = QLineEdit()
        self.vault_edit.setStyleSheet(input_style)
        self.vault_edit.setPlaceholderText("/home/user/Documents/My Obsidian Vault")
        self.vault_edit.textChanged.connect(self._on_vault_text_changed)

        btn_browse = QPushButton("Browse...")
        btn_browse.setStyleSheet(btn_style)
        btn_browse.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_browse.setAutoDefault(False)
        btn_browse.clicked.connect(self._on_browse_vault)

        row_vault.addWidget(self.vault_edit)
        row_vault.addWidget(btn_browse)
        self.content_layout.addLayout(row_vault)

        # Status badge & reset button
        self.badge_box = QHBoxLayout()
        self.status_badge = QLabel()
        self.status_badge.setStyleSheet("font-size: 11px; padding: 4px 8px; border-radius: 4px;")
        self.status_badge.setVisible(False)

        self.btn_reset_toml = QPushButton("Reset to Default Configuration")
        self.btn_reset_toml.setStyleSheet(btn_style)
        self.btn_reset_toml.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_reset_toml.setVisible(False)
        self.btn_reset_toml.setAutoDefault(False)
        self.btn_reset_toml.clicked.connect(self._reset_corrupt_toml)

        self.badge_box.addWidget(self.status_badge)
        self.badge_box.addWidget(self.btn_reset_toml)
        self.badge_box.addStretch()
        self.content_layout.addLayout(self.badge_box)

        self.content_layout.addSpacing(10)

        # 2. Target Deck
        lbl_deck = QLabel("Default Target Deck in Anki:")
        lbl_deck.setStyleSheet("font-size: 13px; font-weight: 600; color: palette(window-text);")
        self.content_layout.addWidget(lbl_deck)

        self.deck_combo = QComboBox()
        self.deck_combo.setEditable(True)
        self.deck_combo.setStyleSheet(input_style)
        self.deck_combo.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.deck_combo.currentTextChanged.connect(lambda _: self.validity_changed.emit(self.is_valid()))
        self.content_layout.addWidget(self.deck_combo)

        deck_hint = QLabel("Cards not matched by any folder routing rule will automatically land in this deck.")
        deck_hint.setStyleSheet("font-size: 11px; color: palette(window-text); opacity: 0.75;")
        self.content_layout.addWidget(deck_hint)

        self.content_layout.addStretch()

    def _on_vault_text_changed(self) -> None:
        self._debounce_timer.stop()
        self._debounce_timer.start(250)

    def _on_browse_vault(self) -> None:
        curr = self.vault_edit.text().strip() or str(Path.home())
        selected = QFileDialog.getExistingDirectory(self, "Select Obsidian Vault Folder", curr)
        if selected:
            self.vault_edit.setText(selected)

    def _populate_decks(self, selected_deck: str = "Default") -> None:
        self.deck_combo.clear()
        names: list[str] = []
        if mw and hasattr(mw, "col") and mw.col:
            for d in mw.col.decks.all():
                # Filter out dynamic / filtered decks
                is_filtered = getattr(mw.col.decks, "is_filtered", lambda did: False)(d["id"])
                if is_filtered or d.get("dyn", False):
                    continue
                names.append(d["name"])
        names.sort(key=str.casefold)
        if "Default" not in names:
            names.insert(0, "Default")

        self.deck_combo.addItems(names)
        idx = self.deck_combo.findText(selected_deck, Qt.MatchFlag.MatchFixedString)
        if idx >= 0:
            self.deck_combo.setCurrentIndex(idx)
        else:
            self.deck_combo.setEditText(selected_deck)

    def _check_vault_path_async(self) -> None:
        raw = self.vault_edit.text().strip()
        if not raw:
            self.status_badge.setVisible(False)
            self.btn_reset_toml.setVisible(False)
            self.validity_changed.emit(self.is_valid())
            return

        p = Path(raw).expanduser()
        if not p.is_dir():
            self.status_badge.setText("⚠️ Directory does not exist or is not accessible.")
            self.status_badge.setStyleSheet("background-color: #5c2020; color: #ffb4b4; padding: 4px 8px; border-radius: 4px;")
            self.status_badge.setVisible(True)
            self.btn_reset_toml.setVisible(False)
            self.validity_changed.emit(self.is_valid())
            return

        toml_file = p / "m2a.toml"
        if toml_file.is_file():
            try:
                load_vault_config(p)
                self.status_badge.setText("✓ Valid vault found. Existing configuration (m2a.toml) loaded.")
                self.status_badge.setStyleSheet("background-color: #1b4d2e; color: #b4ffcf; padding: 4px 8px; border-radius: 4px;")
                self.status_badge.setVisible(True)
                self.btn_reset_toml.setVisible(False)
            except Exception as exc:
                self.status_badge.setText(f"⚠️ Existing m2a.toml is invalid: {exc}")
                self.status_badge.setStyleSheet("background-color: #5c431b; color: #ffe6b4; padding: 4px 8px; border-radius: 4px;")
                self.status_badge.setVisible(True)
                self.btn_reset_toml.setVisible(True)
        else:
            self.status_badge.setText("ℹ️ Valid folder. A new m2a.toml will be initialized.")
            self.status_badge.setStyleSheet("background-color: #1e3a5f; color: #b4d8ff; padding: 4px 8px; border-radius: 4px;")
            self.status_badge.setVisible(True)
            self.btn_reset_toml.setVisible(False)

        self.validity_changed.emit(self.is_valid())

    def _reset_corrupt_toml(self) -> None:
        raw = self.vault_edit.text().strip()
        if not raw:
            return
        p = Path(raw).expanduser()
        toml_file = p / "m2a.toml"
        try:
            if toml_file.is_file():
                toml_file.unlink()
            save_vault_config(p, Config(vault=p, deck=self.deck_combo.currentText().strip() or "Default"))
            self._check_vault_path_async()
        except Exception as exc:
            QMessageBox.critical(self, "Reset Failed", f"Could not reset configuration: {exc}")

    def on_enter(self, state: OnboardingState) -> None:
        if state.vault_path:
            self.vault_edit.setText(str(state.vault_path))
        self._populate_decks(state.target_deck)
        self._check_vault_path_async()

    def on_leave(self, state: OnboardingState) -> None:
        raw = self.vault_edit.text().strip()
        state.vault_path = Path(raw).expanduser().resolve() if raw else None
        state.target_deck = self.deck_combo.currentText().strip() or "Default"

        if state.vault_path and (state.vault_path / "m2a.toml").is_file():
            try:
                cfg = load_vault_config(state.vault_path)
                state.has_existing_toml = True
                state.target_deck = cfg.deck or state.target_deck
                state.cloze_notes = cfg.cloze_notes
                state.image_occlusion_mode = cfg.image_occlusion_mode
                state.suspend_new_occlusions = cfg.suspend_new_occlusions
                state.suspend_new_clozes = cfg.suspend_new_clozes
            except Exception as exc:
                state.has_existing_toml = False
                state.toml_error = str(exc)
        else:
            state.has_existing_toml = False

    def is_valid(self) -> bool:
        raw = self.vault_edit.text().strip()
        if not raw:
            return False
        p = Path(raw).expanduser()
        if not p.is_dir():
            return False
        deck = self.deck_combo.currentText().strip()
        if not deck or deck.startswith("::") or deck.endswith("::"):
            return False
        return True

    def validate_page(self) -> bool:
        if not self.is_valid():
            return False
        p = Path(self.vault_edit.text().strip()).expanduser().resolve()
        # Physical write probe
        probe_file = p / f".vaultsync_probe_{uuid.uuid4().hex[:8]}"
        try:
            probe_file.write_text("probe", encoding="utf-8")
            probe_file.unlink(missing_ok=True)
        except OSError as exc:
            QMessageBox.critical(
                self,
                "Permission Error",
                f"Cannot write to the selected directory:\\n{p}\\n\\nError: {exc}"
            )
            return False
        return True


class PageCardBehavior(OnboardingPageBase):
    """Step 3: Card Types & Behavior (Clozes, Image Occlusions, Suspension)."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._init_ui()

    def _init_ui(self) -> None:
        self._create_header(
            "Card Types & Behavior",
            "Configure how Cloze deletions and Image Occlusions are parsed and scheduled."
        )

        card_style = "QFrame { border: 1px solid palette(mid); border-radius: 8px; background: palette(base); }"
        cb_style = "font-size: 13px; color: palette(text);"

        # Section 1: Cloze notes
        f_cloze = QFrame()
        f_cloze.setStyleSheet(card_style)
        l_cloze = QVBoxLayout(f_cloze)
        l_cloze.setContentsMargins(14, 12, 14, 12)
        l_cloze.setSpacing(6)

        self.cb_cloze = QCheckBox("Enable Native Cloze Note Deletions")
        self.cb_cloze.setStyleSheet(cb_style)
        self.cb_cloze.setChecked(True)
        l_cloze.addWidget(self.cb_cloze)

        cloze_desc = QLabel("When enabled, notes containing {{c1::...}} are converted into native Anki Cloze notes.")
        cloze_desc.setStyleSheet("font-size: 11px; color: palette(text); opacity: 0.75;")
        cloze_desc.setWordWrap(True)
        l_cloze.addWidget(cloze_desc)
        self.content_layout.addWidget(f_cloze)

        # Section 2: Image Occlusions
        f_io = QFrame()
        f_io.setStyleSheet(card_style)
        l_io = QVBoxLayout(f_io)
        l_io.setContentsMargins(14, 12, 14, 12)
        l_io.setSpacing(8)

        lbl_io = QLabel("Image Occlusion Mode:")
        lbl_io.setStyleSheet("font-size: 13px; font-weight: 600; color: palette(text);")
        l_io.addWidget(lbl_io)

        self.rb_native = QRadioButton("● Native Anki Image Occlusion Mode (Recommended)")
        self.rb_native.setChecked(True)
        self.rb_native.setStyleSheet("font-size: 12px; color: palette(text);")
        l_io.addWidget(self.rb_native)

        lbl_native_hint = QLabel("Uses Anki's modern built-in Image Occlusion note type.")
        lbl_native_hint.setStyleSheet("font-size: 11px; color: palette(text); opacity: 0.75; margin-left: 20px;")
        l_io.addWidget(lbl_native_hint)

        self.rb_stage = QRadioButton("○ Staging Folder Mode")
        self.rb_stage.setStyleSheet("font-size: 12px; color: palette(text);")
        l_io.addWidget(self.rb_stage)

        lbl_stage_hint = QLabel("Exports occlusion images to an external directory for manual Anki IO mask editing.")
        lbl_stage_hint.setStyleSheet("font-size: 11px; color: palette(text); opacity: 0.75; margin-left: 20px;")
        l_io.addWidget(lbl_stage_hint)

        # Staging folder picker (dynamically visible)
        self.staging_box = QWidget()
        l_sb = QVBoxLayout(self.staging_box)
        l_sb.setContentsMargins(20, 4, 0, 0)
        l_sb.setSpacing(4)

        lbl_occ_dir = QLabel("Image Occlusion Export Folder:")
        lbl_occ_dir.setStyleSheet("font-size: 11px; font-weight: 600; color: palette(text);")
        l_sb.addWidget(lbl_occ_dir)

        row_occ = QHBoxLayout()
        row_occ.setSpacing(8)
        self.occ_edit = QLineEdit()
        self.occ_edit.setPlaceholderText("/home/user/AnkiOcclusions")
        btn_browse_occ = QPushButton("Browse...")
        btn_browse_occ.setAutoDefault(False)
        btn_browse_occ.clicked.connect(self._on_browse_occ)
        row_occ.addWidget(self.occ_edit)
        row_occ.addWidget(btn_browse_occ)
        l_sb.addLayout(row_occ)

        self.staging_box.setVisible(False)
        l_io.addWidget(self.staging_box)
        self.content_layout.addWidget(f_io)

        # Section 3: Suspension
        f_susp = QFrame()
        f_susp.setStyleSheet(card_style)
        l_susp = QVBoxLayout(f_susp)
        l_susp.setContentsMargins(14, 12, 14, 12)
        l_susp.setSpacing(6)

        lbl_susp = QLabel("New Card Suspension (Study Readiness):")
        lbl_susp.setStyleSheet("font-size: 13px; font-weight: 600; color: palette(text);")
        l_susp.addWidget(lbl_susp)

        self.cb_suspend_occ = QCheckBox("Suspend newly created Image Occlusions until masks are placed")
        self.cb_suspend_occ.setChecked(True)
        self.cb_suspend_occ.setStyleSheet(cb_style)
        l_susp.addWidget(self.cb_suspend_occ)

        self.cb_suspend_cloze = QCheckBox("Suspend newly created Text Clozes")
        self.cb_suspend_cloze.setChecked(False)
        self.cb_suspend_cloze.setStyleSheet(cb_style)
        l_susp.addWidget(self.cb_suspend_cloze)
        self.content_layout.addWidget(f_susp)

        self.rb_native.toggled.connect(self._on_io_mode_changed)
        self.content_layout.addStretch()

    def _on_browse_occ(self) -> None:
        curr = self.occ_edit.text().strip() or str(Path.home())
        selected = QFileDialog.getExistingDirectory(self, "Select Image Occlusion Folder", curr)
        if selected:
            self.occ_edit.setText(selected)

    def _on_io_mode_changed(self) -> None:
        is_stage = self.rb_stage.isChecked()
        self.staging_box.setVisible(is_stage)
        # Suspend new occlusions only makes sense in native mode
        self.cb_suspend_occ.setEnabled(not is_stage)

    def on_enter(self, state: OnboardingState) -> None:
        self.cb_cloze.setChecked(state.cloze_notes)
        if state.image_occlusion_mode == "stage":
            self.rb_stage.setChecked(True)
        else:
            self.rb_native.setChecked(True)

        if state.occlusion_dir:
            self.occ_edit.setText(str(state.occlusion_dir))

        self.cb_suspend_occ.setChecked(state.suspend_new_occlusions)
        self.cb_suspend_cloze.setChecked(state.suspend_new_clozes)
        self._on_io_mode_changed()

    def on_leave(self, state: OnboardingState) -> None:
        state.cloze_notes = self.cb_cloze.isChecked()
        state.image_occlusion_mode = "stage" if self.rb_stage.isChecked() else "native"
        raw_occ = self.occ_edit.text().strip()
        state.occlusion_dir = Path(raw_occ).expanduser().resolve() if raw_occ else None
        state.suspend_new_occlusions = self.cb_suspend_occ.isChecked()
        state.suspend_new_clozes = self.cb_suspend_cloze.isChecked()

    def is_valid(self) -> bool:
        if self.rb_stage.isChecked():
            raw_occ = self.occ_edit.text().strip()
            if not raw_occ:
                return False
        return True


class PageWorkflow(OnboardingPageBase):
    """Step 4: Automation (sync on startup) and Keyboard Shortcuts."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._init_ui()

    def _init_ui(self) -> None:
        self._create_header(
            "Workflow & Automation",
            "Set up automatic sync on startup and configure keyboard shortcuts."
        )

        card_style = "QFrame { border: 1px solid palette(mid); border-radius: 8px; background: palette(base); }"

        # 1. Startup automation
        f_auto = QFrame()
        f_auto.setStyleSheet(card_style)
        l_auto = QVBoxLayout(f_auto)
        l_auto.setContentsMargins(14, 12, 14, 12)
        l_auto.setSpacing(6)

        self.cb_sync_on_startup = QCheckBox("Automatically sync when opening this Anki profile")
        self.cb_sync_on_startup.setStyleSheet("font-size: 13px; color: palette(text);")
        self.cb_sync_on_startup.setChecked(False)
        l_auto.addWidget(self.cb_sync_on_startup)

        hint = QLabel("When enabled, VaultSync scans your vault in the background whenever Anki starts.")
        hint.setStyleSheet("font-size: 11px; color: palette(text); opacity: 0.75;")
        l_auto.addWidget(hint)
        self.content_layout.addWidget(f_auto)

        # 2. Shortcuts Widget
        f_sc = QFrame()
        f_sc.setStyleSheet(card_style)
        l_sc = QVBoxLayout(f_sc)
        l_sc.setContentsMargins(14, 12, 14, 12)
        l_sc.setSpacing(10)

        lbl_sc = QLabel("Keyboard Shortcuts:")
        lbl_sc.setStyleSheet("font-size: 13px; font-weight: 600; color: palette(text);")
        l_sc.addWidget(lbl_sc)

        self.sc_widget = ShortcutConfigWidget(parent=self)
        l_sc.addWidget(self.sc_widget)
        self.content_layout.addWidget(f_sc)
        self.content_layout.addStretch()

    def on_enter(self, state: OnboardingState) -> None:
        self.cb_sync_on_startup.setChecked(state.sync_on_startup)
        if state.shortcuts:
            self.sc_widget.set_shortcuts(state.shortcuts)

    def on_leave(self, state: OnboardingState) -> None:
        state.sync_on_startup = self.cb_sync_on_startup.isChecked()
        state.shortcuts = self.sc_widget.get_shortcuts()

    def is_valid(self) -> bool:
        return self.sc_widget.validate()


class PageCompletion(OnboardingPageBase):
    """Step 5: Setup Summary, sample note creation, and initial synchronization."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._init_ui()

    def _init_ui(self) -> None:
        self._create_header(
            "Ready to Sync!",
            "Review your configuration and run your first synchronization."
        )

        card_style = "QFrame { border: 1px solid palette(mid); border-radius: 8px; background: palette(base); }"

        # Summary box
        f_sum = QFrame()
        f_sum.setStyleSheet(card_style)
        l_sum = QVBoxLayout(f_sum)
        l_sum.setContentsMargins(16, 14, 16, 14)
        l_sum.setSpacing(8)

        lbl_title = QLabel("Configuration Overview:")
        lbl_title.setStyleSheet("font-size: 13px; font-weight: 600; color: palette(text);")
        l_sum.addWidget(lbl_title)

        self.summary_label = QLabel()
        self.summary_label.setStyleSheet("font-size: 12px; line-height: 1.6; color: palette(text);")
        self.summary_label.setWordWrap(True)
        l_sum.addWidget(self.summary_label)
        self.content_layout.addWidget(f_sum)

        # Actions
        f_act = QFrame()
        f_act.setStyleSheet(card_style)
        l_act = QVBoxLayout(f_act)
        l_act.setContentsMargins(14, 12, 14, 12)
        l_act.setSpacing(8)

        self.cb_create_note = QCheckBox("Create sample note 'VaultSync Welcome.md' in your vault")
        self.cb_create_note.setChecked(True)
        self.cb_create_note.setStyleSheet("font-size: 13px; color: palette(text);")
        l_act.addWidget(self.cb_create_note)

        self.cb_run_sync = QCheckBox("Run initial sync immediately upon completion")
        self.cb_run_sync.setChecked(True)
        self.cb_run_sync.setStyleSheet("font-size: 13px; color: palette(text);")
        l_act.addWidget(self.cb_run_sync)
        self.content_layout.addWidget(f_act)

        self.content_layout.addStretch()

    def on_enter(self, state: OnboardingState) -> None:
        v_str = str(state.vault_path) if state.vault_path else "(Not configured)"
        d_str = state.target_deck
        cloze_str = "Enabled" if state.cloze_notes else "Disabled"
        io_str = "Native Anki Mode" if state.image_occlusion_mode == "native" else f"Staging ({state.occlusion_dir or 'None'})"
        startup_str = "Enabled" if state.sync_on_startup else "Disabled"

        text = (
            f"<b>Vault Folder:</b> {v_str}<br>"
            f"<b>Default Target Deck:</b> {d_str}<br>"
            f"<b>Cloze Deletions:</b> {cloze_str}<br>"
            f"<b>Image Occlusions:</b> {io_str}<br>"
            f"<b>Sync on Startup:</b> {startup_str}"
        )
        self.summary_label.setText(text)
        self.cb_create_note.setChecked(state.create_sample_note)
        self.cb_run_sync.setChecked(state.run_initial_sync)

    def on_leave(self, state: OnboardingState) -> None:
        state.create_sample_note = self.cb_create_note.isChecked()
        state.run_initial_sync = self.cb_run_sync.isChecked()


class OnboardingDialog(QDialog):
    """Main wizard dialog orchestrating the 5-step onboarding sequence."""

    def __init__(
        self,
        config_manager: ProfileConfigManager,
        profile_name: str,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent or mw)
        self._manager = config_manager
        self._profile_name = profile_name
        self.state = OnboardingState()
        self._init_state()
        self._init_ui()

    def _init_state(self) -> None:
        existing = self._manager.load(self._profile_name)
        self.state.vault_path = existing.vault_path
        self.state.occlusion_dir = existing.occlusion_dir
        self.state.sync_on_startup = existing.sync_on_startup
        self.state.shortcuts = dict(existing.shortcuts)

    def _init_ui(self) -> None:
        self.setWindowTitle("Anki-VaultSync Setup Wizard")
        self.setMinimumSize(740, 560)
        self.setModal(True)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        # 1. Stepper Header
        self.header = StepHeaderWidget(self)
        layout.addWidget(self.header)

        # 2. Stacked Pages
        self.stack = QStackedWidget(self)
        self.page_welcome = PageWelcome(self)
        self.page_vault_deck = PageVaultDeck(self)
        self.page_card_behavior = PageCardBehavior(self)
        self.page_workflow = PageWorkflow(self)
        self.page_completion = PageCompletion(self)

        self.pages: list[OnboardingPageBase] = [
            self.page_welcome,
            self.page_vault_deck,
            self.page_card_behavior,
            self.page_workflow,
            self.page_completion,
        ]

        for p in self.pages:
            self.stack.addWidget(p)
            p.validity_changed.connect(self._update_footer_buttons)

        layout.addWidget(self.stack)

        # Separator
        line = QFrame()
        line.setFrameShape(QFrame.Shape.HLine)
        line.setFrameShadow(QFrame.Shadow.Sunken)
        layout.addWidget(line)

        # 3. Footer
        footer = QHBoxLayout()
        footer.setSpacing(12)

        existing = self._manager.load(self._profile_name)
        cancel_text = "Cancel" if existing.onboarding_completed else "Set Up Later"
        self.btn_cancel = QPushButton(cancel_text)
        self.btn_cancel.setAutoDefault(False)
        self.btn_cancel.clicked.connect(self.reject)
        footer.addWidget(self.btn_cancel)

        footer.addStretch()

        self.step_label = QLabel("Step 1 of 5")
        self.step_label.setStyleSheet("font-size: 11px; color: palette(window-text); opacity: 0.8;")
        footer.addWidget(self.step_label)

        footer.addStretch()

        self.btn_back = QPushButton("Back")
        self.btn_back.setAutoDefault(False)
        self.btn_back.clicked.connect(self._go_back)
        footer.addWidget(self.btn_back)

        self.btn_next = QPushButton("Next")
        self.btn_next.setDefault(True)
        self.btn_next.clicked.connect(self._go_next)
        footer.addWidget(self.btn_next)

        layout.addLayout(footer)

        self.pages[0].on_enter(self.state)
        self._update_footer_buttons()

    def _update_footer_buttons(self) -> None:
        curr = self.stack.currentIndex()
        self.header.set_current_step(curr)
        self.step_label.setText(f"Step {curr + 1} of {TOTAL_STEPS}")
        self.btn_back.setEnabled(curr > 0)

        is_valid = self.pages[curr].is_valid()
        self.btn_next.setEnabled(is_valid)

        if curr == TOTAL_STEPS - 1:
            self.btn_next.setText("Finish & Start Using VaultSync")
        else:
            self.btn_next.setText("Next")

    def _go_back(self) -> None:
        curr = self.stack.currentIndex()
        if curr > 0:
            self.pages[curr].on_leave(self.state)
            self.stack.setCurrentIndex(curr - 1)
            self.pages[curr - 1].on_enter(self.state)
            self._update_footer_buttons()

    def _go_next(self) -> None:
        curr = self.stack.currentIndex()
        page = self.pages[curr]
        if not page.validate_page():
            return

        page.on_leave(self.state)

        if curr < TOTAL_STEPS - 1:
            self.stack.setCurrentIndex(curr + 1)
            self.pages[curr + 1].on_enter(self.state)
            self._update_footer_buttons()
        else:
            self._commit_onboarding()

    def _commit_onboarding(self) -> None:
        """Atomically persist m2a.toml, profile configuration, and welcome note."""
        if not self.state.vault_path:
            QMessageBox.critical(self, "Error", "No valid vault directory configured.")
            return

        # 1. Save m2a.toml (preserving existing rules)
        try:
            if self.state.has_existing_toml and (self.state.vault_path / "m2a.toml").is_file():
                cfg = self._manager.load_vault(self.state.vault_path)
            else:
                cfg = Config(vault=self.state.vault_path)

            cfg.deck = self.state.target_deck
            cfg.cloze_notes = self.state.cloze_notes
            cfg.image_occlusion_mode = self.state.image_occlusion_mode
            cfg.suspend_new_occlusions = self.state.suspend_new_occlusions
            cfg.suspend_new_clozes = self.state.suspend_new_clozes

            self._manager.save_vault(self.state.vault_path, cfg)
        except Exception as exc:
            QMessageBox.critical(
                self,
                "Error Saving Configuration",
                f"Failed to save m2a.toml in vault directory:\\n{exc}\\n\\n"
                "Settings were not saved. Please check directory write permissions."
            )
            return

        # 2. Sample note creation (optional)
        if self.state.create_sample_note and self.state.vault_path:
            try:
                create_welcome_note(self.state.vault_path)
            except Exception as exc:
                logger.warning(f"Failed to create sample welcome note: {exc}")

        # 3. Save profile settings
        profile_settings = self._manager.load(self._profile_name)
        profile_settings.vault_path = self.state.vault_path
        profile_settings.occlusion_dir = self.state.occlusion_dir if self.state.image_occlusion_mode == "stage" else None
        profile_settings.sync_on_startup = self.state.sync_on_startup
        profile_settings.shortcuts = self.state.shortcuts
        profile_settings.onboarding_completed = True
        self._manager.save(profile_settings)

        try:
            from src.ui.menu import apply_menu_shortcuts
            apply_menu_shortcuts(profile_settings)
        except Exception:
            pass

        # 4. Accept dialog
        self.accept()

        # 5. Optional initial sync triggered after dialog closes
        if self.state.run_initial_sync:
            try:
                from src.ui.menu import _run_menu_sync
                _run_menu_sync(include_updates=True)
            except Exception as exc:
                logger.error(f"Failed to trigger initial sync after onboarding: {exc}")
