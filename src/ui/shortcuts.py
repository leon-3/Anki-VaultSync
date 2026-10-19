from __future__ import annotations

from typing import TYPE_CHECKING, Any

import aqt
from aqt import mw
from aqt.qt import (
    QAction,
    QHBoxLayout,
    QKeySequence,
    QKeySequenceEdit,
    QIcon,
    QLabel,
    QPushButton,
    QShortcut,
    QVBoxLayout,
    QWidget,
    Qt,
    pyqtSignal,
    sip,
)

from ..profile_config import DEFAULT_SHORTCUTS


def _is_deleted(obj: Any) -> bool:
    if obj is None:
        return True
    if sip is not None and hasattr(sip, "isdeleted"):
        try:
            return sip.isdeleted(obj)
        except Exception:
            return True
    return False


def normalize_key_sequence(seq_or_str: QKeySequence | str) -> str:
    """Returns a canonical portable string representation of a shortcut (e.g. 'Ctrl+Alt+V')."""
    if isinstance(seq_or_str, str):
        seq = QKeySequence(seq_or_str.strip())
    else:
        seq = seq_or_str
    if seq.isEmpty():
        return ""
    return seq.toString(QKeySequence.SequenceFormat.PortableText)


def find_shortcut_conflict(
    shortcut_str: str,
    allowed_actions: set[QAction] | None = None,
) -> str | None:
    """Checks whether the given shortcut string is already registered on mw or any child action.

    Returns the descriptive name/label of the conflicting action, or None if no conflict exists.
    """
    clean_str = shortcut_str.strip()
    if not clean_str:
        return None

    target_seq = QKeySequence(clean_str)
    if target_seq.isEmpty():
        return None

    target_canonical = target_seq.toString(QKeySequence.SequenceFormat.PortableText).casefold()

    live_mw = aqt.mw if getattr(aqt, "mw", None) is not None else mw
    if live_mw is None:
        return None

    # Check QAction objects under live_mw
    if hasattr(live_mw, "findChildren"):
        for action in live_mw.findChildren(QAction):
            if _is_deleted(action):
                continue
            if allowed_actions and action in allowed_actions:
                continue

            try:
                # Check primary shortcut
                sc = action.shortcut()
                if not sc.isEmpty():
                    if sc.toString(QKeySequence.SequenceFormat.PortableText).casefold() == target_canonical:
                        label = action.text().split("\t")[0].replace("&", "").strip() or action.objectName() or "Anki action"
                        return label

                # Check secondary / multiple shortcuts if any
                for sc_extra in action.shortcuts():
                    if not sc_extra.isEmpty():
                        if sc_extra.toString(QKeySequence.SequenceFormat.PortableText).casefold() == target_canonical:
                            label = action.text().split("\t")[0].replace("&", "").strip() or action.objectName() or "Anki action"
                            return label
            except RuntimeError:
                continue

        # Check QShortcut objects under live_mw
        for qsc in live_mw.findChildren(QShortcut):
            if _is_deleted(qsc):
                continue
            try:
                sc = qsc.key()
                if not sc.isEmpty():
                    if sc.toString(QKeySequence.SequenceFormat.PortableText).casefold() == target_canonical:
                        label = qsc.objectName() or "Anki shortcut"
                        return label
            except RuntimeError:
                continue

    return None


class ShortcutConfigWidget(QWidget):
    """Reusable widget for configuring Anki-VaultSync keyboard shortcuts."""

    changed = pyqtSignal()

    def __init__(
        self,
        initial_shortcuts: dict[str, str] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._rows: list[tuple[str, str, QKeySequenceEdit, QLabel]] = []
        self._init_ui()
        if initial_shortcuts:
            self.set_shortcuts(initial_shortcuts)
        else:
            self.reset_to_defaults()

    def _init_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)

        input_style = """
            QKeySequenceEdit {
                border: 1px solid palette(mid);
                border-radius: 6px;
                padding: 4px 8px;
                background-color: palette(base);
                color: palette(text);
                font-size: 12px;
            }
            QKeySequenceEdit:focus {
                border: 1px solid palette(highlight);
            }
        """
        btn_style = """
            QPushButton {
                border: 1px solid rgba(255, 255, 255, 0.15);
                border-radius: 6px;
                padding: 4px 10px;
                background-color: rgba(255, 255, 255, 0.04);
                color: palette(window-text);
                font-size: 11px;
            }
            QPushButton:hover:enabled {
                background-color: #2196F3;
                border: 1px solid #1E88E5;
                color: #ffffff;
            }
            QPushButton:pressed {
                background-color: #1976D2;
                border: 1px solid #1565C0;
                color: #ffffff;
            }
        """

        definitions = [
            ("open_dashboard", "Open Dashboard:"),
            ("quick_sync", "Quick Sync (New Cards):"),
            ("full_sync", "Sync and Update (Modified Cards):"),
        ]

        for key, label_text in definitions:
            lbl = QLabel(label_text)
            lbl.setStyleSheet("font-size: 12px; font-weight: 600; color: palette(window-text);")
            layout.addWidget(lbl)

            row = QHBoxLayout()
            row.setSpacing(8)

            edit = QKeySequenceEdit()
            edit.setStyleSheet(input_style)
            edit.setMaximumWidth(170)
            if hasattr(edit, "setAutoDefault"):
                edit.setAutoDefault(False)

            btn_clear_style = """
                QPushButton {
                    font-size: 11px;
                    padding: 2px 8px;
                    border-radius: 4px;
                    border: 1px solid rgba(255, 255, 255, 0.15);
                    background-color: rgba(255, 255, 255, 0.04);
                    color: palette(window-text);
                    min-height: 18px;
                    max-width: 48px;
                }
                QPushButton:hover:enabled {
                    background-color: #2196F3;
                    border: 1px solid #1E88E5;
                    color: #ffffff;
                }
                QPushButton:pressed {
                    background-color: #1976D2;
                    border: 1px solid #1565C0;
                    color: #ffffff;
                }
            """
            btn_clear = QPushButton("Clear")
            btn_clear.setIcon(QIcon())
            btn_clear.setStyleSheet(btn_clear_style)
            btn_clear.setCursor(Qt.CursorShape.PointingHandCursor)
            btn_clear.setAutoDefault(False)
            btn_clear.clicked.connect(lambda _, e=edit: e.setKeySequence(QKeySequence()))

            row.addWidget(edit)
            row.addWidget(btn_clear)
            row.addStretch()
            layout.addLayout(row)

            warn_lbl = QLabel()
            warn_lbl.setStyleSheet("font-size: 11px; color: #e5c07b; font-weight: 500;")
            warn_lbl.setVisible(False)
            layout.addWidget(warn_lbl)

            edit.keySequenceChanged.connect(self._on_changed)
            self._rows.append((key, label_text.rstrip(":"), edit, warn_lbl))

        layout.addSpacing(4)
        row_reset = QHBoxLayout()
        btn_reset = QPushButton("Reset Shortcuts to Defaults")
        btn_reset.setIcon(QIcon())
        btn_reset.setStyleSheet(btn_style)
        btn_reset.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_reset.setAutoDefault(False)
        btn_reset.clicked.connect(self.reset_to_defaults)
        row_reset.addWidget(btn_reset)
        row_reset.addStretch()
        layout.addLayout(row_reset)

    def _on_changed(self) -> None:
        self.validate()
        self.changed.emit()

    def get_shortcuts(self) -> dict[str, str]:
        shortcuts: dict[str, str] = {}
        for key, _, edit, _ in self._rows:
            shortcuts[key] = normalize_key_sequence(edit.keySequence())
        return shortcuts

    def set_shortcuts(self, shortcuts: dict[str, str]) -> None:
        for key, _, edit, _ in self._rows:
            val = shortcuts.get(key, DEFAULT_SHORTCUTS.get(key, ""))
            edit.setKeySequence(QKeySequence(val))
        self.validate()

    def reset_to_defaults(self) -> None:
        self.set_shortcuts(DEFAULT_SHORTCUTS)

    def validate(self) -> bool:
        """Validate shortcuts for mutual conflicts and collisions with external Anki actions."""
        try:
            from .menu import get_menu_actions
            menu_actions = get_menu_actions()
            allowed = {act for act in menu_actions.values() if act is not None}
        except Exception:
            allowed = set()

        has_conflicts = False

        for i, (key, name, edit, warn_lbl) in enumerate(self._rows):
            seq = edit.keySequence()
            if seq.isEmpty():
                warn_lbl.setVisible(False)
                continue

            seq_str = normalize_key_sequence(seq)
            seq_canon = seq_str.casefold()

            # 1. Check mutual conflict with other fields in this widget
            mutual_conflict = None
            for j, (_, other_name, other_edit, _) in enumerate(self._rows):
                if i != j:
                    other_seq = other_edit.keySequence()
                    if not other_seq.isEmpty() and normalize_key_sequence(other_seq).casefold() == seq_canon:
                        mutual_conflict = other_name
                        break

            if mutual_conflict:
                warn_lbl.setText(f"Conflicts with '{mutual_conflict}'. Shortcut will be disabled if saved.")
                warn_lbl.setVisible(True)
                has_conflicts = True
                continue

            # 2. Check collision with external Anki actions
            conflict = find_shortcut_conflict(seq_str, allowed_actions=allowed)
            if conflict:
                warn_lbl.setText(f"Already taken by '{conflict}'. Shortcut will be disabled if saved.")
                warn_lbl.setVisible(True)
                has_conflicts = True
            else:
                warn_lbl.setVisible(False)

        return not has_conflicts
