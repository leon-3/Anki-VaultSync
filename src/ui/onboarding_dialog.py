"""Interactive multi-step onboarding wizard for Anki-VaultSync."""
from __future__ import annotations

import logging
from pathlib import Path
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
    QGridLayout,
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

from ..profile_config import AddonProfileSettings, ProfileConfigManager
from ..vault_detector import DetectedVault, detect_obsidian_vaults
from .onboarding_state import OnboardingState
from .shortcuts import ShortcutConfigWidget

logger = logging.getLogger("anki_vaultsync")

TOTAL_STEPS = 5
STEP_TITLES = ["Welcome", "Vault & Deck", "Card Types", "Workflow", "Ready"]

CARD_FRAME_STYLE = """
    #dashboardCard {
        border: 1px solid rgba(130, 130, 130, 0.25);
        border-radius: 8px;
        background-color: rgba(130, 130, 130, 0.03);
    }
"""

INPUT_STYLE = """
    QLineEdit, QComboBox {
        border: 1px solid rgba(130, 130, 130, 0.35);
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

BTN_STYLE = """
    QPushButton {
        border: 1px solid rgba(130, 130, 130, 0.35);
        border-radius: 6px;
        padding: 6px 14px;
        background-color: palette(button);
        color: palette(button-text);
        font-size: 12px;
        font-weight: 500;
    }
    QPushButton:hover {
        background-color: palette(highlight);
        color: palette(highlighted-text);
        border-color: palette(highlight);
    }
"""


def _create_card_frame(parent: QWidget | None = None) -> QFrame:
    """Helper creating a consistent card container matching the Dashboard design."""
    card = QFrame(parent)
    card.setObjectName("dashboardCard")
    card.setStyleSheet(CARD_FRAME_STYLE)
    return card


class StepHeaderWidget(QWidget):
    """Custom stepper header rendering numbered nodes with connecting lines and subtle card framing."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._current_step = 0
        self.setFixedHeight(68)
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

        # 1. Subtle card frame background matching Dashboard panels
        bg_col = QColor(mid_color)
        bg_col.setAlpha(16)
        border_col = QColor(mid_color)
        border_col.setAlpha(55)
        painter.setBrush(bg_col)
        painter.setPen(QPen(border_col, 1))
        painter.drawRoundedRect(1, 1, width - 2, self.height() - 2, 8, 8)

        margin = 50
        available_w = width - 2 * margin
        spacing = available_w / (TOTAL_STEPS - 1)
        node_radius = 12
        y_circle = 22

        # 2. Draw connecting lines
        pen = QPen(mid_color, 2)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
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

        # 3. Draw nodes & labels
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
                painter.setPen(QPen(mid_color, 1.5))
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
            painter.drawText(x - 55, y_circle + 15, 110, 20, Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop,
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
        self.content_layout.setContentsMargins(20, 14, 20, 16)
        self.content_layout.setSpacing(14)
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
        t_lbl.setStyleSheet("font-size: 17px; font-weight: bold; color: palette(window-text);")
        sub_lbl = QLabel(subtitle)
        sub_lbl.setStyleSheet("font-size: 13px; color: palette(window-text); opacity: 0.8; line-height: 1.4;")
        sub_lbl.setWordWrap(True)

        header_box.addWidget(t_lbl)
        header_box.addWidget(sub_lbl)
        self.content_layout.addLayout(header_box)
        self.content_layout.addSpacing(2)

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

        # 1. Feature Highlights Card (Matching Dashboard card style)
        card_features = _create_card_frame(self)
        cf_layout = QVBoxLayout(card_features)
        cf_layout.setContentsMargins(16, 14, 16, 14)
        cf_layout.setSpacing(10)

        title_features = QLabel("How VaultSync Works")
        title_features.setStyleSheet("font-size: 14px; font-weight: bold; border: none; background: transparent;")
        cf_layout.addWidget(title_features)

        features = [
            ("Obsidian Note Tracking", "Scans your notes directly — no export steps or complex tagging required."),
            ("Deterministic Note IDs", "Guarantees cards update in place without resetting your scheduling or review history."),
            ("Rich Card Formats", "Full native support for Basic Q&A cards, Cloze deletions, and Image Occlusions."),
        ]
        for name, desc in features:
            row = QHBoxLayout()
            row.setSpacing(8)
            dot = QLabel("●")
            dot.setStyleSheet("color: #4CAF50; font-size: 11px; border: none; background: transparent;")
            lbl = QLabel(f"<b>{name}:</b> {desc}")
            lbl.setStyleSheet("font-size: 12px; border: none; background: transparent;")
            lbl.setWordWrap(True)
            row.addWidget(dot, 0, Qt.AlignmentFlag.AlignTop)
            row.addWidget(lbl, 1)
            cf_layout.addLayout(row)

        self.content_layout.addWidget(card_features)

        # 2. Syntax Preview Card
        card_preview = _create_card_frame(self)
        cp_layout = QVBoxLayout(card_preview)
        cp_layout.setContentsMargins(16, 14, 16, 14)
        cp_layout.setSpacing(10)

        preview_title = QLabel("Markdown Card Syntax Examples")
        preview_title.setStyleSheet("font-size: 14px; font-weight: bold; border: none; background: transparent;")
        cp_layout.addWidget(preview_title)

        cards_box = QHBoxLayout()
        cards_box.setSpacing(12)

        code_style = """
            QFrame {
                border: 1px solid rgba(130, 130, 130, 0.22);
                border-radius: 6px;
                background-color: rgba(0, 0, 0, 0.05);
            }
        """

        # Basic QA Box
        box1 = QFrame()
        box1.setStyleSheet(code_style)
        l1 = QVBoxLayout(box1)
        l1.setContentsMargins(12, 10, 12, 10)
        l1.setSpacing(6)
        hdr1 = QLabel("<b>Basic Card:</b>")
        hdr1.setStyleSheet("border: none; background: transparent; font-size: 12px;")
        l1.addWidget(hdr1)
        code1 = QLabel("# Biology\n---\nWhat is photosynthesis?\nLight-driven synthesis of sugars.")
        code1.setStyleSheet("font-family: monospace; font-size: 11px; border: none; background: transparent; line-height: 1.4;")
        l1.addWidget(code1)
        cards_box.addWidget(box1)

        # Cloze Box
        box2 = QFrame()
        box2.setStyleSheet(code_style)
        l2 = QVBoxLayout(box2)
        l2.setContentsMargins(12, 10, 12, 10)
        l2.setSpacing(6)
        hdr2 = QLabel("<b>Cloze Note:</b>")
        hdr2.setStyleSheet("border: none; background: transparent; font-size: 12px;")
        l2.addWidget(hdr2)
        code2 = QLabel("# Genetics\n---\nCloze\nDNA consists of {{c1::nucleotides}}.")
        code2.setStyleSheet("font-family: monospace; font-size: 11px; border: none; background: transparent; line-height: 1.4;")
        l2.addWidget(code2)
        cards_box.addWidget(box2)

        cp_layout.addLayout(cards_box)
        self.content_layout.addWidget(card_preview)

        # 3. Documentation Link Button
        link_btn = QPushButton("Open Syntax Documentation & Cheat Sheet ↗")
        link_btn.setFixedHeight(36)
        link_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        link_btn.setStyleSheet(BTN_STYLE)
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
        self._detected_vaults: list[DetectedVault] = []
        self._debounce_timer = QTimer(self)
        self._debounce_timer.setSingleShot(True)
        self._debounce_timer.timeout.connect(self._check_vault_path_async)
        self._init_ui()

    def _init_ui(self) -> None:
        self._create_header(
            "Select Your Obsidian Vault",
            "Choose your local notes folder and a default Anki deck for imported flashcards."
        )

        # 1. Vault Selection Card
        card_vault = _create_card_frame(self)
        l_vault = QVBoxLayout(card_vault)
        l_vault.setContentsMargins(16, 14, 16, 14)
        l_vault.setSpacing(10)

        title_vault = QLabel("Obsidian Vault Location")
        title_vault.setStyleSheet("font-size: 14px; font-weight: bold; border: none; background: transparent;")
        l_vault.addWidget(title_vault)

        # 1a. Detected Vaults Dropdown (if any detected)
        self.detected_box = QWidget()
        l_det = QVBoxLayout(self.detected_box)
        l_det.setContentsMargins(0, 0, 0, 0)
        l_det.setSpacing(4)

        det_header = QHBoxLayout()
        dot_det = QLabel("●")
        dot_det.setStyleSheet("font-size: 12px; color: #4CAF50; border: none; background: transparent;")
        self.lbl_detected = QLabel("Detected Obsidian Vaults:")
        self.lbl_detected.setStyleSheet("font-size: 13px; font-weight: 600; border: none; background: transparent;")
        det_header.addWidget(dot_det)
        det_header.addWidget(self.lbl_detected)
        det_header.addStretch()
        l_det.addLayout(det_header)

        self.vault_combo = QComboBox()
        self.vault_combo.setFixedHeight(34)
        self.vault_combo.setStyleSheet(INPUT_STYLE)
        self.vault_combo.setCursor(Qt.CursorShape.PointingHandCursor)
        self.vault_combo.currentIndexChanged.connect(self._on_detected_vault_selected)
        l_det.addWidget(self.vault_combo)

        l_vault.addWidget(self.detected_box)

        # 1b. Vault path input row
        self.lbl_vault = QLabel("Obsidian Vault Folder:")
        self.lbl_vault.setStyleSheet("font-size: 13px; font-weight: 600; border: none; background: transparent;")
        l_vault.addWidget(self.lbl_vault)

        row_vault = QHBoxLayout()
        row_vault.setSpacing(8)
        self.vault_edit = QLineEdit()
        self.vault_edit.setFixedHeight(34)
        self.vault_edit.setStyleSheet(INPUT_STYLE)
        self.vault_edit.setPlaceholderText("/home/user/Documents/My Obsidian Vault")
        self.vault_edit.textChanged.connect(self._on_vault_text_changed)

        btn_browse = QPushButton("Browse...")
        btn_browse.setFixedHeight(34)
        btn_browse.setStyleSheet(BTN_STYLE)
        btn_browse.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_browse.setAutoDefault(False)
        btn_browse.clicked.connect(self._on_browse_vault)

        row_vault.addWidget(self.vault_edit)
        row_vault.addWidget(btn_browse)
        l_vault.addLayout(row_vault)

        # 1c. Status indicator bar (matching Dashboard status bar design)
        self.status_box = QFrame()
        self.status_box.setObjectName("statusBox")
        self.status_box.setStyleSheet("""
            #statusBox {
                border: 1px solid rgba(130, 130, 130, 0.25);
                border-radius: 6px;
                background-color: rgba(130, 130, 130, 0.04);
            }
        """)
        sb_layout = QHBoxLayout(self.status_box)
        sb_layout.setContentsMargins(12, 7, 12, 7)
        sb_layout.setSpacing(8)

        self.status_dot = QLabel("●")
        self.status_dot.setStyleSheet("font-size: 12px; color: #4CAF50; border: none; background: transparent;")

        self.status_badge = QLabel()
        self.status_badge.setStyleSheet("font-size: 12px; border: none; background: transparent;")

        self.btn_reset_toml = QPushButton("Reset to Default Configuration")
        self.btn_reset_toml.setFixedHeight(28)
        self.btn_reset_toml.setStyleSheet(BTN_STYLE)
        self.btn_reset_toml.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_reset_toml.setVisible(False)
        self.btn_reset_toml.setAutoDefault(False)
        self.btn_reset_toml.clicked.connect(self._reset_corrupt_toml)

        sb_layout.addWidget(self.status_dot)
        sb_layout.addWidget(self.status_badge)
        sb_layout.addStretch()
        sb_layout.addWidget(self.btn_reset_toml)
        self.status_box.setVisible(False)

        l_vault.addWidget(self.status_box)
        self.content_layout.addWidget(card_vault)

        # 2. Target Deck Card
        card_deck = _create_card_frame(self)
        l_deck = QVBoxLayout(card_deck)
        l_deck.setContentsMargins(16, 14, 16, 14)
        l_deck.setSpacing(10)

        title_deck = QLabel("Default Target Deck")
        title_deck.setStyleSheet("font-size: 14px; font-weight: bold; border: none; background: transparent;")
        l_deck.addWidget(title_deck)

        self.deck_combo = QComboBox()
        self.deck_combo.setEditable(True)
        self.deck_combo.setFixedHeight(34)
        self.deck_combo.setStyleSheet(INPUT_STYLE)
        self.deck_combo.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.deck_combo.currentTextChanged.connect(lambda _: self.validity_changed.emit(self.is_valid()))
        l_deck.addWidget(self.deck_combo)

        deck_hint = QLabel(
            "Cards not matched by specific folder routing rules will land in this default deck. "
            "Detailed folder-to-deck routing rules can be configured anytime later in Settings (Rules & Decks)."
        )
        deck_hint.setWordWrap(True)
        deck_hint.setStyleSheet("font-size: 11px; color: palette(window-text); opacity: 0.8; margin-top: 2px;")
        l_deck.addWidget(deck_hint)

        self.content_layout.addWidget(card_deck)
        self.content_layout.addStretch()
        self._populate_detected_vaults()

    def _populate_detected_vaults(self, detected: list[DetectedVault] | None = None) -> None:
        if detected is None:
            detected = detect_obsidian_vaults()
        self._detected_vaults = list(detected)

        self.vault_combo.blockSignals(True)
        self.vault_combo.clear()

        if not self._detected_vaults:
            self.detected_box.setVisible(False)
            self.vault_combo.blockSignals(False)
            return

        self.detected_box.setVisible(True)
        count_str = f" ({len(self._detected_vaults)} found)" if len(self._detected_vaults) > 1 else ""
        self.vault_combo.addItem(f"-- Select a detected Obsidian vault{count_str} --", userData=None)

        for v in self._detected_vaults:
            status = " (Active)" if v.is_open else ""
            label = f"{v.name}{status} — {v.path}"
            self.vault_combo.addItem(label, userData=v.path)

        self.vault_combo.blockSignals(False)

    def _on_detected_vault_selected(self, index: int) -> None:
        if index <= 0:
            return
        path = self.vault_combo.itemData(index)
        if path:
            self.vault_edit.setText(str(path))

    def _sync_combo_with_text(self, text: str) -> None:
        if not hasattr(self, "vault_combo") or not getattr(self, "_detected_vaults", None):
            return
        raw = text.strip()
        target_idx = 0
        if raw:
            try:
                p_norm = Path(raw).expanduser().resolve()
                for idx in range(1, self.vault_combo.count()):
                    val = self.vault_combo.itemData(idx)
                    if isinstance(val, Path) and val == p_norm:
                        target_idx = idx
                        break
            except Exception:
                pass
        self.vault_combo.blockSignals(True)
        self.vault_combo.setCurrentIndex(target_idx)
        self.vault_combo.blockSignals(False)

    def _on_vault_text_changed(self) -> None:
        self._sync_combo_with_text(self.vault_edit.text())
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
            self.status_box.setVisible(False)
            self.status_badge.setText("")
            self.btn_reset_toml.setVisible(False)
            self.validity_changed.emit(self.is_valid())
            return

        p = Path(raw).expanduser()
        if not p.is_dir():
            self.status_dot.setText("●")
            self.status_dot.setStyleSheet("font-size: 12px; color: #F44336; border: none; background: transparent;")
            self.status_badge.setText("⚠️ Directory does not exist or is not accessible.")
            self.status_badge.setStyleSheet("font-size: 12px; color: #ffb4b4; border: none; background: transparent;")
            self.status_box.setVisible(True)
            self.btn_reset_toml.setVisible(False)
            self.validity_changed.emit(self.is_valid())
            return

        toml_file = p / "m2a.toml"
        if toml_file.is_file():
            try:
                load_vault_config(p)
                self.status_dot.setText("●")
                self.status_dot.setStyleSheet("font-size: 12px; color: #4CAF50; border: none; background: transparent;")
                self.status_badge.setText("✓ Valid vault found. Existing configuration (m2a.toml) loaded.")
                self.status_badge.setStyleSheet("font-size: 12px; color: #b4ffcf; border: none; background: transparent;")
                self.status_box.setVisible(True)
                self.btn_reset_toml.setVisible(False)
            except Exception as exc:
                self.status_dot.setText("●")
                self.status_dot.setStyleSheet("font-size: 12px; color: #FF9800; border: none; background: transparent;")
                self.status_badge.setText(f"⚠️ Existing m2a.toml is invalid: {exc}")
                self.status_badge.setStyleSheet("font-size: 12px; color: #ffe6b4; border: none; background: transparent;")
                self.status_box.setVisible(True)
                self.btn_reset_toml.setVisible(True)
        else:
            self.status_dot.setText("●")
            self.status_dot.setStyleSheet("font-size: 12px; color: #2196F3; border: none; background: transparent;")
            self.status_badge.setText("ℹ️ Valid folder. A new m2a.toml will be initialized.")
            self.status_badge.setStyleSheet("font-size: 12px; color: #b4d8ff; border: none; background: transparent;")
            self.status_box.setVisible(True)
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
        if not self._detected_vaults:
            self._populate_detected_vaults()
        if state.vault_path:
            self.vault_edit.setText(str(state.vault_path))
            self._sync_combo_with_text(str(state.vault_path))
        elif self._detected_vaults:
            if len(self._detected_vaults) == 1 or self._detected_vaults[0].is_open:
                target_vault = self._detected_vaults[0]
                self.vault_combo.blockSignals(True)
                self.vault_combo.setCurrentIndex(1)
                self.vault_combo.blockSignals(False)
                self.vault_edit.setText(str(target_vault.path))
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
        probe_file = p / f".vaultsync_probe_{uuid.uuid4().hex[:8]}"
        try:
            probe_file.write_text("probe", encoding="utf-8")
            probe_file.unlink(missing_ok=True)
        except OSError as exc:
            QMessageBox.critical(
                self,
                "Permission Error",
                f"Cannot write to the selected directory:\n{p}\n\nError: {exc}"
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

        cb_style = "font-size: 13px; color: palette(text); border: none; background: transparent;"

        # Section 1: Cloze notes
        f_cloze = _create_card_frame(self)
        l_cloze = QVBoxLayout(f_cloze)
        l_cloze.setContentsMargins(16, 14, 16, 14)
        l_cloze.setSpacing(6)

        title_cloze = QLabel("Cloze Deletions")
        title_cloze.setStyleSheet("font-size: 14px; font-weight: bold; border: none; background: transparent;")
        l_cloze.addWidget(title_cloze)

        self.cb_cloze = QCheckBox("Enable Native Cloze Note Deletions")
        self.cb_cloze.setStyleSheet(cb_style)
        self.cb_cloze.setChecked(True)
        l_cloze.addWidget(self.cb_cloze)

        cloze_desc = QLabel("When enabled, notes containing {{c1::...}} are converted into native Anki Cloze notes.")
        cloze_desc.setStyleSheet("font-size: 11px; color: palette(text); opacity: 0.75; border: none; background: transparent;")
        cloze_desc.setWordWrap(True)
        l_cloze.addWidget(cloze_desc)
        self.content_layout.addWidget(f_cloze)

        # Section 2: Image Occlusions
        f_io = _create_card_frame(self)
        l_io = QVBoxLayout(f_io)
        l_io.setContentsMargins(16, 14, 16, 14)
        l_io.setSpacing(8)

        lbl_io = QLabel("Image Occlusion Mode")
        lbl_io.setStyleSheet("font-size: 14px; font-weight: bold; border: none; background: transparent;")
        l_io.addWidget(lbl_io)

        self.rb_native = QRadioButton("● Native Anki Image Occlusion Mode (Recommended)")
        self.rb_native.setChecked(True)
        self.rb_native.setStyleSheet("font-size: 12px; color: palette(text); border: none; background: transparent;")
        l_io.addWidget(self.rb_native)

        lbl_native_hint = QLabel("Uses Anki's modern built-in Image Occlusion note type.")
        lbl_native_hint.setStyleSheet("font-size: 11px; color: palette(text); opacity: 0.75; margin-left: 20px; border: none; background: transparent;")
        l_io.addWidget(lbl_native_hint)

        self.rb_stage = QRadioButton("○ Staging Folder Mode")
        self.rb_stage.setStyleSheet("font-size: 12px; color: palette(text); border: none; background: transparent;")
        l_io.addWidget(self.rb_stage)

        lbl_stage_hint = QLabel("Exports occlusion images to an external directory for manual Anki IO mask editing.")
        lbl_stage_hint.setStyleSheet("font-size: 11px; color: palette(text); opacity: 0.75; margin-left: 20px; border: none; background: transparent;")
        l_io.addWidget(lbl_stage_hint)

        # Staging folder picker (dynamically visible)
        self.staging_box = QWidget()
        l_sb = QVBoxLayout(self.staging_box)
        l_sb.setContentsMargins(20, 4, 0, 0)
        l_sb.setSpacing(4)

        lbl_occ_dir = QLabel("Image Occlusion Export Folder:")
        lbl_occ_dir.setStyleSheet("font-size: 11px; font-weight: 600; color: palette(text); border: none; background: transparent;")
        l_sb.addWidget(lbl_occ_dir)

        row_occ = QHBoxLayout()
        row_occ.setSpacing(8)
        self.occ_edit = QLineEdit()
        self.occ_edit.setFixedHeight(34)
        self.occ_edit.setStyleSheet(INPUT_STYLE)
        self.occ_edit.setPlaceholderText("/home/user/AnkiOcclusions")

        btn_browse_occ = QPushButton("Browse...")
        btn_browse_occ.setFixedHeight(34)
        btn_browse_occ.setStyleSheet(BTN_STYLE)
        btn_browse_occ.setAutoDefault(False)
        btn_browse_occ.clicked.connect(self._on_browse_occ)

        row_occ.addWidget(self.occ_edit)
        row_occ.addWidget(btn_browse_occ)
        l_sb.addLayout(row_occ)

        self.staging_box.setVisible(False)
        l_io.addWidget(self.staging_box)
        self.content_layout.addWidget(f_io)

        # Section 3: Suspension
        f_susp = _create_card_frame(self)
        l_susp = QVBoxLayout(f_susp)
        l_susp.setContentsMargins(16, 14, 16, 14)
        l_susp.setSpacing(8)

        lbl_susp = QLabel("Study Readiness & Card Suspension")
        lbl_susp.setStyleSheet("font-size: 14px; font-weight: bold; border: none; background: transparent;")
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

        # 1. Startup automation card
        f_auto = _create_card_frame(self)
        l_auto = QVBoxLayout(f_auto)
        l_auto.setContentsMargins(16, 14, 16, 14)
        l_auto.setSpacing(6)

        title_auto = QLabel("Startup Automation")
        title_auto.setStyleSheet("font-size: 14px; font-weight: bold; border: none; background: transparent;")
        l_auto.addWidget(title_auto)

        self.cb_sync_on_startup = QCheckBox("Automatically sync when opening this Anki profile")
        self.cb_sync_on_startup.setStyleSheet("font-size: 13px; color: palette(text); border: none; background: transparent;")
        self.cb_sync_on_startup.setChecked(False)
        l_auto.addWidget(self.cb_sync_on_startup)

        hint = QLabel("When enabled, VaultSync scans your vault in the background whenever Anki starts.")
        hint.setStyleSheet("font-size: 11px; color: palette(text); opacity: 0.75; border: none; background: transparent;")
        l_auto.addWidget(hint)
        self.content_layout.addWidget(f_auto)

        # 2. Shortcuts Card
        f_sc = _create_card_frame(self)
        l_sc = QVBoxLayout(f_sc)
        l_sc.setContentsMargins(16, 14, 16, 14)
        l_sc.setSpacing(10)

        lbl_sc = QLabel("Keyboard Shortcuts")
        lbl_sc.setStyleSheet("font-size: 14px; font-weight: bold; border: none; background: transparent;")
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
    """Step 5: Setup Summary and final actions."""

    finish_requested = pyqtSignal(bool)  # False: Close without sync, True: Sync Vault Directly

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._init_ui()

    def _init_ui(self) -> None:
        self._create_header(
            "Ready to Sync!",
            "Review your configuration and finish setup or synchronize immediately."
        )

        # Summary box (Dashboard card & 2-column grid layout)
        f_sum = _create_card_frame(self)
        l_sum = QVBoxLayout(f_sum)
        l_sum.setContentsMargins(18, 16, 18, 16)
        l_sum.setSpacing(12)

        lbl_title = QLabel("Configuration Overview")
        lbl_title.setStyleSheet("font-size: 15px; font-weight: bold; border: none; background: transparent;")
        l_sum.addWidget(lbl_title)

        grid = QGridLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setVerticalSpacing(10)
        grid.setHorizontalSpacing(24)

        for r in range(5):
            grid.setRowMinimumHeight(r, 26)

        lbl_k_style = "font-size: 13px; font-weight: normal; border: none; background: transparent;"
        lbl_v_style = "font-size: 13px; font-weight: bold; border: none; background: transparent;"

        lbl_v_title = QLabel("Obsidian Vault:")
        lbl_v_title.setStyleSheet(lbl_k_style)
        self.val_vault = QLabel("(Not configured)")
        self.val_vault.setStyleSheet(lbl_v_style)
        grid.addWidget(lbl_v_title, 0, 0)
        grid.addWidget(self.val_vault, 0, 1, Qt.AlignmentFlag.AlignRight)

        lbl_d_title = QLabel("Default Target Deck:")
        lbl_d_title.setStyleSheet(lbl_k_style)
        self.val_deck = QLabel("Default")
        self.val_deck.setStyleSheet(lbl_v_style)
        grid.addWidget(lbl_d_title, 1, 0)
        grid.addWidget(self.val_deck, 1, 1, Qt.AlignmentFlag.AlignRight)

        lbl_c_title = QLabel("Cloze Deletions:")
        lbl_c_title.setStyleSheet(lbl_k_style)
        self.val_cloze = QLabel("Enabled")
        self.val_cloze.setStyleSheet(lbl_v_style)
        grid.addWidget(lbl_c_title, 2, 0)
        grid.addWidget(self.val_cloze, 2, 1, Qt.AlignmentFlag.AlignRight)

        lbl_i_title = QLabel("Image Occlusions:")
        lbl_i_title.setStyleSheet(lbl_k_style)
        self.val_io = QLabel("Native Anki Mode")
        self.val_io.setStyleSheet(lbl_v_style)
        grid.addWidget(lbl_i_title, 3, 0)
        grid.addWidget(self.val_io, 3, 1, Qt.AlignmentFlag.AlignRight)

        lbl_s_title = QLabel("Sync on Startup:")
        lbl_s_title.setStyleSheet(lbl_k_style)
        self.val_startup = QLabel("Disabled")
        self.val_startup.setStyleSheet(lbl_v_style)
        grid.addWidget(lbl_s_title, 4, 0)
        grid.addWidget(self.val_startup, 4, 1, Qt.AlignmentFlag.AlignRight)

        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)
        l_sum.addLayout(grid)

        # Compatibility label
        self.summary_label = QLabel()
        self.summary_label.setVisible(False)
        l_sum.addWidget(self.summary_label)

        self.content_layout.addWidget(f_sum)
        self.content_layout.addSpacing(12)

        # Action Buttons Container (Button A: Close, Button B: Sync Vault Directly)
        btn_box = QHBoxLayout()
        btn_box.setSpacing(14)

        self.btn_close = QPushButton("Close")
        self.btn_close.setFixedHeight(36)
        self.btn_close.setToolTip("Save configuration and close the wizard without running a sync.")
        self.btn_close.setStyleSheet(BTN_STYLE)
        self.btn_close.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_close.setAutoDefault(False)
        self.btn_close.clicked.connect(lambda: self.finish_requested.emit(False))

        self.btn_sync = QPushButton("Sync Vault Directly")
        self.btn_sync.setFixedHeight(36)
        self.btn_sync.setToolTip("Save configuration and immediately start synchronizing your cards.")
        self.btn_sync.setStyleSheet(BTN_STYLE)
        self.btn_sync.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_sync.setDefault(True)
        self.btn_sync.clicked.connect(lambda: self.finish_requested.emit(True))

        btn_box.addStretch()
        btn_box.addWidget(self.btn_close)
        btn_box.addWidget(self.btn_sync)

        self.content_layout.addLayout(btn_box)
        self.content_layout.addStretch()

    def on_enter(self, state: OnboardingState) -> None:
        v_str = str(state.vault_path) if state.vault_path else "(Not configured)"
        d_str = state.target_deck
        cloze_str = "Enabled" if state.cloze_notes else "Disabled"
        io_str = "Native Anki Mode" if state.image_occlusion_mode == "native" else f"Staging ({state.occlusion_dir or 'None'})"
        startup_str = "Enabled" if state.sync_on_startup else "Disabled"

        self.val_vault.setText(v_str)
        self.val_deck.setText(d_str)
        self.val_cloze.setText(cloze_str)
        self.val_io.setText(io_str)
        self.val_startup.setText(startup_str)

        text = (
            f"<b>Vault Folder:</b> {v_str}<br>"
            f"<b>Default Target Deck:</b> {d_str}<br>"
            f"<b>Cloze Deletions:</b> {cloze_str}<br>"
            f"<b>Image Occlusions:</b> {io_str}<br>"
            f"<b>Sync on Startup:</b> {startup_str}"
        )
        self.summary_label.setText(text)

    def on_leave(self, state: OnboardingState) -> None:
        pass


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
        self.setMinimumSize(780, 580)
        self.setModal(True)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(14)

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

        # Separator line matching Dashboard subtle divider
        line = QFrame()
        line.setStyleSheet("border: none; background-color: rgba(130, 130, 130, 0.2); max-height: 1px;")
        layout.addWidget(line)

        # 3. Footer Buttons
        footer = QHBoxLayout()
        footer.setSpacing(12)

        existing = self._manager.load(self._profile_name)
        cancel_text = "Cancel" if existing.onboarding_completed else "Set Up Later"
        self.btn_cancel = QPushButton(cancel_text)
        self.btn_cancel.setFixedHeight(36)
        self.btn_cancel.setStyleSheet(BTN_STYLE)
        self.btn_cancel.setAutoDefault(False)
        self.btn_cancel.clicked.connect(self.reject)
        footer.addWidget(self.btn_cancel)

        footer.addStretch()

        self.btn_back = QPushButton("Back")
        self.btn_back.setFixedHeight(36)
        self.btn_back.setStyleSheet(BTN_STYLE)
        self.btn_back.setAutoDefault(False)
        self.btn_back.clicked.connect(self._go_back)
        footer.addWidget(self.btn_back)

        self.btn_next = QPushButton("Next")
        self.btn_next.setFixedHeight(36)
        self.btn_next.setStyleSheet("""
            QPushButton {
                font-size: 13px;
                font-weight: 600;
                border-radius: 6px;
                padding: 6px 22px;
                background-color: palette(highlight);
                color: palette(highlighted-text);
                border: 1px solid palette(highlight);
            }
            QPushButton:hover {
                opacity: 0.9;
            }
            QPushButton:disabled {
                opacity: 0.4;
            }
        """)
        self.btn_next.setDefault(True)
        self.btn_next.clicked.connect(self._go_next)
        footer.addWidget(self.btn_next)

        layout.addLayout(footer)

        self.page_completion.finish_requested.connect(self._on_finish_requested)
        self.pages[0].on_enter(self.state)
        self._update_footer_buttons()

        # Ensure no button steals focus by default
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setFocus()

    def _update_footer_buttons(self) -> None:
        curr = self.stack.currentIndex()
        self.header.set_current_step(curr)
        self.btn_back.setEnabled(curr > 0)

        is_valid = self.pages[curr].is_valid()
        self.btn_next.setEnabled(is_valid)

        if curr == TOTAL_STEPS - 1:
            self.btn_next.setVisible(False)
            self.btn_cancel.setVisible(False)
        else:
            self.btn_next.setVisible(True)
            self.btn_next.setText("Next")
            existing = self._manager.load(self._profile_name)
            cancel_text = "Cancel" if existing.onboarding_completed else "Set Up Later"
            self.btn_cancel.setText(cancel_text)
            self.btn_cancel.setVisible(True)

    def _on_finish_requested(self, run_sync: bool) -> None:
        self.state.run_initial_sync = run_sync
        self._commit_onboarding()

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
                f"Failed to save m2a.toml in vault directory:\n{exc}\n\n"
                "Settings were not saved. Please check directory write permissions."
            )
            return

        # 2. Save profile settings
        profile_settings = self._manager.load(self._profile_name)
        profile_settings.vault_path = self.state.vault_path
        profile_settings.occlusion_dir = self.state.occlusion_dir if self.state.image_occlusion_mode == "stage" else None
        profile_settings.sync_on_startup = self.state.sync_on_startup
        profile_settings.shortcuts = self.state.shortcuts
        profile_settings.onboarding_completed = True
        self._manager.save(profile_settings)

        try:
            from .menu import apply_menu_shortcuts
            apply_menu_shortcuts(profile_settings)
        except Exception:
            pass

        # 3. Accept dialog
        self.accept()

        # 4. Optional initial sync triggered after dialog closes
        if self.state.run_initial_sync:
            try:
                from .menu import _run_menu_sync
                _run_menu_sync(include_updates=True)
            except Exception as exc:
                logger.error(f"Failed to trigger initial sync after onboarding: {exc}")
