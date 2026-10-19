from __future__ import annotations

import difflib
import html
from pathlib import Path
import re
from typing import TYPE_CHECKING

import hashlib
from aqt.qt import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QSplitter,
    Qt,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

if TYPE_CHECKING:
    from ..contracts import FileSyncPlan, NoteUpdateOp, SyncPlan

_CHECKMARK_SVG_PATH = Path(__file__).parent / "assets" / "checkmark.svg"

M2A_MARKER_STRIP_RE = re.compile(r"\s*<!--m2a:[a-zA-Z0-9_-]+-->")
HTML_TOKEN_RE = re.compile(r"<[^>]+>|\w+|\s+|[^\w\s<]+")


def _format_diff_token(tok: str) -> str:
    lower = tok.lower().strip()
    if lower in ("<br>", "<br/>", "<br />"):
        return "<br>"
    if lower in ("<p>", "<div>"):
        return ""
    if lower in ("</p>", "</div>"):
        return "<br>"
    if tok.startswith("<") and tok.endswith(">"):
        return ""  # Strip structural wrapper tags from visual text diff
    return html.escape(tok)


def _generate_word_diff_html(old_text: str, new_text: str) -> str:
    """Generate an inline word-by-word diff with <del> and <ins> tags."""
    clean_old = M2A_MARKER_STRIP_RE.sub("", old_text).strip()
    clean_new = M2A_MARKER_STRIP_RE.sub("", new_text).strip()

    words_old = HTML_TOKEN_RE.findall(clean_old)
    words_new = HTML_TOKEN_RE.findall(clean_new)
    matcher = difflib.SequenceMatcher(None, words_old, words_new)
    result: list[str] = []
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            for tok in words_old[i1:i2]:
                result.append(_format_diff_token(tok))
        elif tag == "delete":
            deleted = "".join(_format_diff_token(t) for t in words_old[i1:i2] if _format_diff_token(t))
            if deleted:
                result.append(
                    f"<del style='background-color:rgba(244, 67, 54, 0.22); color:#ef9a9a; text-decoration:line-through; padding:1px 4px; border-radius:3px;'>{deleted}</del>"
                )
        elif tag == "insert":
            inserted = "".join(_format_diff_token(t) for t in words_new[j1:j2] if _format_diff_token(t))
            if inserted:
                result.append(
                    f"<ins style='background-color:rgba(76, 175, 80, 0.22); color:#a5d6a7; text-decoration:none; padding:1px 4px; border-radius:3px;'>{inserted}</ins>"
                )
        elif tag == "replace":
            deleted = "".join(_format_diff_token(t) for t in words_old[i1:i2] if _format_diff_token(t))
            inserted = "".join(_format_diff_token(t) for t in words_new[j1:j2] if _format_diff_token(t))
            if deleted:
                result.append(
                    f"<del style='background-color:rgba(244, 67, 54, 0.22); color:#ef9a9a; text-decoration:line-through; padding:1px 4px; border-radius:3px;'>{deleted}</del>"
                )
            if inserted:
                result.append(
                    f"<ins style='background-color:rgba(76, 175, 80, 0.22); color:#a5d6a7; text-decoration:none; padding:1px 4px; border-radius:3px;'>{inserted}</ins>"
                )

    return "".join(result)


def _generate_diff_html(op: NoteUpdateOp) -> str:
    lines: list[str] = [
        "<div style='font-family: -apple-system, BlinkMacSystemFont, \"Segoe UI\", Roboto, sans-serif; color: #e0e0e0; padding: 2px;'>"
    ]
    lines.append(
        f"<div style='margin-bottom: 12px; padding-bottom: 8px; border-bottom: 1px solid rgba(255, 255, 255, 0.08);'>"
        f"<span style='background: rgba(33, 150, 243, 0.15); color: #64B5F6; font-family: monospace; font-size: 12px; font-weight: 600; padding: 2px 7px; border-radius: 4px; border: 1px solid rgba(33, 150, 243, 0.3); margin-right: 8px;'>ID: {html.escape(op.card_id)}</span>"
        f"<span style='color: rgba(220, 220, 220, 0.55); font-size: 12px;'>{html.escape(str(op.source_file))}:{op.source_line}</span>"
        f"</div>"
    )

    if op.deck_changed:
        old_d = ", ".join(op.old_deck_names) if op.old_deck_names else "(unknown)"
        lines.append(
            f"<div style='margin-bottom: 8px; font-size: 12px;'>"
            f"<b style='color: rgba(240, 240, 240, 0.9);'>Deck Changed:</b> "
            f"<span style='background: rgba(244, 67, 54, 0.15); color: #ef9a9a; padding: 2px 7px; border-radius: 4px; border: 1px solid rgba(244, 67, 54, 0.25);'>{html.escape(old_d)}</span> "
            f"<span style='color: rgba(255, 255, 255, 0.4); margin: 0 4px;'>&rarr;</span> "
            f"<span style='background: rgba(76, 175, 80, 0.18); color: #a5d6a7; font-weight: 600; padding: 2px 7px; border-radius: 4px; border: 1px solid rgba(76, 175, 80, 0.25);'><b>{html.escape(op.target_deck_name)}</b></span>"
            f"</div>"
        )

    if op.tags_changed:
        old_set = set(op.old_tags)
        new_set = set(op.new_tags)
        removed = sorted(old_set - new_set)
        added = sorted(new_set - old_set)
        tag_diff = []
        for r in removed:
            tag_diff.append(
                f"<span style='background: rgba(244, 67, 54, 0.15); color: #ef9a9a; padding: 2px 7px; border-radius: 4px; border: 1px solid rgba(244, 67, 54, 0.25); margin-right: 4px;'>-{html.escape(r)}</span>"
            )
        for a in added:
            tag_diff.append(
                f"<span style='background: rgba(76, 175, 80, 0.18); color: #a5d6a7; padding: 2px 7px; border-radius: 4px; border: 1px solid rgba(76, 175, 80, 0.25); margin-right: 4px;'>+{html.escape(a)}</span>"
            )
        lines.append(f"<div style='margin-bottom: 12px; font-size: 12px;'><b style='color: rgba(240, 240, 240, 0.9);'>Tags:</b> {' '.join(tag_diff)}</div>")

    for f_name in sorted(set(op.old_fields.keys()) | set(op.new_fields.keys())):
        old_val = op.old_fields.get(f_name, "")
        new_val = op.new_fields.get(f_name, "")
        if old_val == new_val:
            lines.append(
                f"<div style='margin-top: 10px; margin-bottom: 2px; font-size: 11px; font-weight: 600; text-transform: uppercase; letter-spacing: 0.5px; color: rgba(255, 255, 255, 0.45);'>Field [{html.escape(f_name)}]: <span style='font-style: italic; font-weight: normal; text-transform: none; font-size: 11px;'>— (unchanged)</span></div>"
            )
            continue

        lines.append(
            f"<div style='margin-top: 14px; margin-bottom: 4px; font-size: 11px; font-weight: 600; text-transform: uppercase; letter-spacing: 0.5px; color: rgba(255, 255, 255, 0.75);'>Field [{html.escape(f_name)}]:</div>"
        )
        formatted_diff = _generate_word_diff_html(old_val, new_val)
        lines.append(
            f"<div style='font-family: monospace, sans-serif; background: rgba(0, 0, 0, 0.25); border: 1px solid rgba(255, 255, 255, 0.08); border-radius: 6px; padding: 10px 12px; line-height: 1.6; color: #f0f0f0;'>{formatted_diff}</div>"
        )

    lines.append("</div>")
    return "".join(lines)


class DiffDialog(QDialog):
    """Side-by-side visual diff preview and selective update approval dialog."""

    def __init__(self, plan: SyncPlan, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Review Modified Cards (Diff Inspector)")
        self.resize(920, 580)

        self._plan = plan
        self._all_updates: list[NoteUpdateOp] = []
        for fp in plan.file_plans:
            self._all_updates.extend(fp.updates)
        self._file_plan_by_path: dict[Path, FileSyncPlan] = {
            fp.file_path: fp for fp in plan.file_plans
        }
        self._original_approvals: dict[int, bool] = {
            idx: op.approved for idx, op in enumerate(self._all_updates)
        }
        self.rescan_requested = False

        self._init_ui()

    def _init_ui(self) -> None:
        chk_svg = _CHECKMARK_SVG_PATH.resolve().as_posix() if _CHECKMARK_SVG_PATH.exists() else ""
        self.setStyleSheet(f"""
            QDialog {{
                background-color: #1e1e1e;
                color: #ffffff;
            }}
            QSplitter::handle {{
                background-color: rgba(255, 255, 255, 0.08);
                width: 2px;
            }}
            QSplitter::handle:hover {{
                background-color: #2196F3;
            }}
            QCheckBox {{
                font-size: 12px;
                spacing: 8px;
                color: rgba(230, 230, 230, 0.95);
            }}
            QCheckBox::indicator {{
                width: 16px;
                height: 16px;
                border: 1.5px solid rgba(150, 150, 150, 0.75);
                border-radius: 4px;
                background-color: rgba(130, 130, 130, 0.12);
            }}
            QCheckBox::indicator:hover {{
                border: 1.5px solid #2196F3;
                background-color: rgba(33, 150, 243, 0.15);
            }}
            QCheckBox::indicator:checked {{
                background-color: #2196F3;
                border: 1.5px solid #1E88E5;
                image: url("{chk_svg}");
            }}
            QListWidget {{
                background-color: rgba(255, 255, 255, 0.03);
                border: 1px solid rgba(255, 255, 255, 0.08);
                border-radius: 8px;
                color: #e0e0e0;
                font-size: 12px;
                padding: 4px;
                outline: none;
            }}
            QListWidget::item {{
                padding: 8px 10px;
                border-radius: 6px;
                margin-bottom: 2px;
                border: 1px solid transparent;
            }}
            QListWidget::item:hover {{
                background-color: rgba(255, 255, 255, 0.06);
            }}
            QListWidget::item:selected {{
                background-color: rgba(33, 150, 243, 0.2);
                color: #ffffff;
                border: 1px solid rgba(33, 150, 243, 0.45);
            }}
            QTextBrowser {{
                background-color: rgba(255, 255, 255, 0.03);
                border: 1px solid rgba(255, 255, 255, 0.08);
                border-radius: 8px;
                color: #e0e0e0;
                padding: 14px;
                selection-background-color: #2196F3;
            }}
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(12)

        # Header Bar
        header_layout = QHBoxLayout()
        header_text = QVBoxLayout()
        header_text.setSpacing(2)
        title = QLabel("Card Updates Preview")
        title.setStyleSheet("font-size: 15px; font-weight: 700; color: #ffffff;")
        subtitle = QLabel("Review modified cards and field diffs before synchronizing changes to Anki.")
        subtitle.setStyleSheet("font-size: 11.5px; color: rgba(200, 200, 200, 0.7);")
        header_text.addWidget(title)
        header_text.addWidget(subtitle)
        header_layout.addLayout(header_text)
        header_layout.addStretch()

        count_badge = QLabel(f"{len(self._all_updates)} modified card(s)")
        count_badge.setStyleSheet("""
            background-color: rgba(33, 150, 243, 0.15);
            color: #64B5F6;
            font-size: 11px;
            font-weight: 600;
            padding: 4px 10px;
            border-radius: 12px;
            border: 1px solid rgba(33, 150, 243, 0.3);
        """)
        header_layout.addWidget(count_badge)
        layout.addLayout(header_layout)

        splitter = QSplitter(Qt.Orientation.Horizontal)

        # Left Column: Updates List
        self.select_all_cb = QCheckBox("Select / Deselect All")
        self.select_all_cb.setChecked(True)
        self.select_all_cb.setCursor(Qt.CursorShape.PointingHandCursor)
        self.select_all_cb.toggled.connect(self._on_select_all_toggled)

        self.list_widget = QListWidget()
        for idx, op in enumerate(self._all_updates):
            preview = (
                op.new_fields.get("Front")
                or op.new_fields.get("Text")
                or op.new_fields.get("Header")
                or (next(iter(op.new_fields.values()), "") if op.new_fields else "")
            )
            clean_preview = re.sub(r"<[^>]+>", "", preview).replace("\n", " ").strip()[:45]
            item = QListWidgetItem(f"[{op.card_id}] {clean_preview}...")
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked if op.approved else Qt.CheckState.Unchecked)
            item.setData(Qt.ItemDataRole.UserRole, idx)
            self.list_widget.addItem(item)

        self.list_widget.currentItemChanged.connect(self._on_item_selected)
        self.list_widget.itemChanged.connect(self._on_item_check_changed)

        left_layout_container = QWidget()
        left_layout = QVBoxLayout(left_layout_container)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(8)
        left_layout.addWidget(self.select_all_cb)
        left_layout.addWidget(self.list_widget)

        # Right Column: Diff Browser
        self.diff_browser = QTextBrowser()
        self.diff_browser.setOpenExternalLinks(False)

        splitter.addWidget(left_layout_container)
        splitter.addWidget(self.diff_browser)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 2)
        layout.addWidget(splitter)

        # Dialog Buttons
        self.button_box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        self.apply_btn = self.button_box.button(QDialogButtonBox.StandardButton.Ok)
        self.apply_btn.setText("Apply Selected")
        self.apply_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.apply_btn.setStyleSheet("""
            QPushButton {
                background-color: #2196F3;
                color: #ffffff;
                font-weight: 600;
                font-size: 12px;
                border: none;
                border-radius: 5px;
                padding: 6px 18px;
                min-height: 22px;
            }
            QPushButton:hover {
                background-color: #1976D2;
            }
            QPushButton:pressed {
                background-color: #1565C0;
            }
        """)

        self.cancel_btn = self.button_box.button(QDialogButtonBox.StandardButton.Cancel)
        self.cancel_btn.setText("Cancel")
        self.cancel_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.cancel_btn.setStyleSheet("""
            QPushButton {
                background-color: transparent;
                color: rgba(220, 220, 220, 0.85);
                font-size: 12px;
                border: 1px solid rgba(255, 255, 255, 0.15);
                border-radius: 5px;
                padding: 6px 14px;
                min-height: 22px;
            }
            QPushButton:hover {
                background-color: rgba(255, 255, 255, 0.08);
                color: #ffffff;
            }
        """)

        self.button_box.accepted.connect(self.accept)
        self.button_box.rejected.connect(self.reject)

        self.rescan_btn = QPushButton("Rescan Vault")
        self.rescan_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.rescan_btn.setToolTip("Discard current preview and re-scan your vault for the latest changes.")
        self.rescan_btn.setStyleSheet("""
            QPushButton {
                background-color: transparent;
                color: rgba(220, 220, 220, 0.85);
                font-size: 12px;
                border: 1px solid rgba(255, 255, 255, 0.14);
                border-radius: 5px;
                padding: 6px 14px;
                min-height: 22px;
            }
            QPushButton:hover {
                background-color: rgba(255, 255, 255, 0.08);
                color: #ffffff;
                border-color: rgba(255, 255, 255, 0.25);
            }
        """)
        self.rescan_btn.clicked.connect(self._on_rescan_clicked)
        self.button_box.addButton(self.rescan_btn, QDialogButtonBox.ButtonRole.ActionRole)

        layout.addWidget(self.button_box)

        # Select first item
        if self.list_widget.count() > 0:
            self.list_widget.setCurrentRow(0)

    def reject(self) -> None:
        # Transactional rollback: revert approved flags if user cancelled
        for idx, op in enumerate(self._all_updates):
            if idx in self._original_approvals:
                op.approved = self._original_approvals[idx]
        super().reject()

    def _on_rescan_clicked(self) -> None:
        self.rescan_requested = True
        self.reject()

    def _is_file_externally_modified(self, op: NoteUpdateOp) -> bool:
        fp = self._file_plan_by_path.get(op.source_file)
        if fp is None:
            return False
        try:
            stat = op.source_file.stat()
            if stat.st_mtime_ns == fp.mtime_ns:
                return False
            cur_sha = hashlib.sha256(op.source_file.read_bytes()).hexdigest()
            return cur_sha != fp.sha256_hex
        except OSError:
            return True
        except Exception:
            return False

    def _on_item_selected(self, current: QListWidgetItem | None, previous: QListWidgetItem | None) -> None:
        if current is None:
            self.diff_browser.clear()
            return
        idx = current.data(Qt.ItemDataRole.UserRole)
        op = self._all_updates[idx]
        html_content = _generate_diff_html(op)

        if self._is_file_externally_modified(op):
            banner = (
                "<div style='background-color: rgba(255, 179, 0, 0.12); color: #ffca28; "
                "border: 1px solid rgba(255, 179, 0, 0.35); border-radius: 6px; padding: 10px 12px; "
                "margin-bottom: 12px; font-size: 12px; font-family: sans-serif; line-height: 1.4;'>"
                "<b>Notice: Note modified in Obsidian since scan.</b><br>"
                "<span style='color: rgba(240, 240, 240, 0.85); font-size: 11.5px;'>"
                "This file was edited externally. Click <b>Rescan Vault</b> below to refresh preview with latest edits."
                "</span>"
                "</div>"
            )
            html_content = banner + html_content

        self.diff_browser.setHtml(html_content)

    def _on_item_check_changed(self, item: QListWidgetItem) -> None:
        idx = item.data(Qt.ItemDataRole.UserRole)
        is_checked = item.checkState() == Qt.CheckState.Checked
        self._all_updates[idx].approved = is_checked

    def _on_select_all_toggled(self, checked: bool) -> None:
        state = Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked
        self.list_widget.blockSignals(True)
        for i in range(self.list_widget.count()):
            item = self.list_widget.item(i)
            item.setCheckState(state)
            idx = item.data(Qt.ItemDataRole.UserRole)
            self._all_updates[idx].approved = checked
        self.list_widget.blockSignals(False)
