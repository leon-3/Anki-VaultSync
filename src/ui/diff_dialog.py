from __future__ import annotations

import difflib
import hashlib
import html
from pathlib import Path
import re
from typing import TYPE_CHECKING, Any

from aqt.qt import (
    QCheckBox,
    QColor,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QIcon,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPainter,
    QPushButton,
    QRect,
    QSplitter,
    QSplitterHandle,
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


def _format_diff_chunk(tokens: list[str], tag_name: str, style: str) -> str:
    """Format inline diff tokens while preserving HTML tag boundaries."""
    out: list[str] = []
    in_tag = False
    for t in tokens:
        if t.startswith("<") and t.endswith(">"):
            if in_tag:
                out.append(f"</{tag_name}>")
                in_tag = False
            out.append(t)
        else:
            if not in_tag:
                out.append(f'<{tag_name} style="{style}">')
                in_tag = True
            out.append(html.escape(t))
    if in_tag:
        out.append(f"</{tag_name}>")
    return "".join(out)


def _normalize_field_text(text: str) -> str:
    clean = M2A_MARKER_STRIP_RE.sub("", text).strip()
    if not clean:
        return ""
    # If the text has raw newlines but no HTML block tags, convert \n to <br> so card linebreaks are visible
    if "\n" in clean and not any(
        tag in clean.lower()
        for tag in ("<p>", "<br>", "<div>", "<ul>", "<ol>", "<table>", "<pre>")
    ):
        clean = "<br>".join(clean.splitlines())
    return clean


def _generate_word_diff_html(old_text: str, new_text: str) -> str:
    """Generate rich card inline diff preserving HTML formatting, line breaks, and styling."""
    clean_old = _normalize_field_text(old_text)
    clean_new = _normalize_field_text(new_text)

    words_old = HTML_TOKEN_RE.findall(clean_old)
    words_new = HTML_TOKEN_RE.findall(clean_new)
    matcher = difflib.SequenceMatcher(None, words_old, words_new)

    ins_style = (
        "background-color: rgba(76, 175, 80, 0.18); border-bottom: 1.5px solid #4CAF50; "
        "border-radius: 2px; padding: 1px 3px; text-decoration: none;"
    )
    del_style = (
        "background-color: rgba(244, 67, 54, 0.16); color: rgba(244, 67, 54, 0.88); "
        "text-decoration: line-through; border-radius: 2px; padding: 1px 3px;"
    )

    result: list[str] = []
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            for tok in words_old[i1:i2]:
                if tok.startswith("<") and tok.endswith(">"):
                    result.append(tok)
                else:
                    result.append(html.escape(tok))
        elif tag == "delete":
            chunk = _format_diff_chunk(words_old[i1:i2], "del", del_style)
            if chunk:
                result.append(chunk)
        elif tag == "insert":
            chunk = _format_diff_chunk(words_new[j1:j2], "ins", ins_style)
            if chunk:
                result.append(chunk)
        elif tag == "replace":
            chunk_del = _format_diff_chunk(words_old[i1:i2], "del", del_style)
            chunk_ins = _format_diff_chunk(words_new[j1:j2], "ins", ins_style)
            if chunk_del:
                result.append(chunk_del)
            if chunk_ins:
                result.append(chunk_ins)

    return "".join(result)


def _generate_diff_html(op: NoteUpdateOp) -> str:
    lines: list[str] = [
        "<div style='font-family: -apple-system, BlinkMacSystemFont, \"Segoe UI\", Roboto, \"Inter\", sans-serif; color: palette(window-text); font-size: 13px; line-height: 1.5;'>"
    ]
    # Header metadata bar: ID on left, file:line on right
    lines.append(
        "<table style='width:100%; border-bottom:1px solid rgba(130,130,130,0.22); padding-bottom:6px; margin-bottom:10px;'>"
        "<tr>"
        f"<td style='font-size:12px; font-weight:600; color:#2196F3; font-family:\"JetBrains Mono\", \"Cascadia Code\", \"Fira Code\", \"SF Mono\", \"Menlo\", \"Consolas\", monospace;'>ID: {html.escape(op.card_id)}</td>"
        f"<td style='text-align:right; font-size:11px; opacity:0.75;'>{html.escape(str(op.source_file))}:{op.source_line}</td>"
        "</tr>"
        "</table>"
    )

    if op.deck_changed:
        old_d = ", ".join(op.old_deck_names) if op.old_deck_names else "(unknown)"
        lines.append(
            f"<div style='margin-bottom: 6px; font-size: 11.5px;'>"
            f"<b>Deck Changed:</b> "
            f"<span style='background: rgba(244, 67, 54, 0.15); color: #ef9a9a; padding: 1px 6px; border-radius: 3px;'>{html.escape(old_d)}</span> "
            f"<span style='opacity: 0.5; margin: 0 4px;'>&rarr;</span> "
            f"<span style='background: rgba(76, 175, 80, 0.18); color: #a5d6a7; font-weight: 600; padding: 1px 6px; border-radius: 3px;'><b>{html.escape(op.target_deck_name)}</b></span>"
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
                f"<span style='background: rgba(244, 67, 54, 0.15); color: #ef9a9a; padding: 1px 5px; border-radius: 3px; margin-right: 4px;'>-{html.escape(r)}</span>"
            )
        for a in added:
            tag_diff.append(
                f"<span style='background: rgba(76, 175, 80, 0.18); color: #a5d6a7; padding: 1px 5px; border-radius: 3px; margin-right: 4px;'>+{html.escape(a)}</span>"
            )
        lines.append(f"<div style='margin-bottom: 8px; font-size: 11.5px;'><b>Tags:</b> {' '.join(tag_diff)}</div>")

    for f_name in sorted(set(op.old_fields.keys()) | set(op.new_fields.keys())):
        old_val = op.old_fields.get(f_name, "")
        new_val = op.new_fields.get(f_name, "")
        if old_val == new_val:
            lines.append(
                f"<div style='margin-top: 8px; margin-bottom: 2px; font-size: 11px; font-weight: 600; text-transform: uppercase; letter-spacing: 0.5px; opacity: 0.55;'>Field [{html.escape(f_name)}]: <span style='font-style: italic; font-weight: normal; text-transform: none;'>— (unchanged)</span></div>"
            )
            continue

        lines.append(
            f"<div style='margin-top: 12px; margin-bottom: 5px; font-size: 11px; font-weight: 600; text-transform: uppercase; letter-spacing: 0.5px; color: #2196F3;'>Field [{html.escape(f_name)}]:</div>"
        )
        formatted_diff = _generate_word_diff_html(old_val, new_val)
        lines.append(
            f"<div style='background-color: rgba(130, 130, 130, 0.05); border: 1px solid rgba(130, 130, 130, 0.22); border-radius: 8px; padding: 12px 15px; font-size: 13px; line-height: 1.6; color: palette(window-text); font-family: -apple-system, BlinkMacSystemFont, \"Segoe UI\", Roboto, \"Inter\", sans-serif;'>{formatted_diff}</div>"
        )

    lines.append("</div>")
    return "".join(lines)


class _DiffSplitterHandle(QSplitterHandle):
    """Custom splitter handle with centered resize grip and subtle padding."""

    def __init__(self, orientation: Qt.Orientation, parent: QSplitter) -> None:
        super().__init__(orientation, parent)
        self.setCursor(
            Qt.CursorShape.SplitHCursor
            if orientation == Qt.Orientation.Horizontal
            else Qt.CursorShape.SplitVCursor
        )
        self._hovered = False
        self._pressed = False

    def enterEvent(self, event: Any) -> None:
        self._hovered = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event: Any) -> None:
        self._hovered = False
        self.update()
        super().leaveEvent(event)

    def mousePressEvent(self, event: Any) -> None:
        self._pressed = True
        self.update()
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event: Any) -> None:
        self._pressed = False
        self.update()
        super().mouseReleaseEvent(event)

    def paintEvent(self, event: Any) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        # Center grip pill
        w = 4
        h = 36
        x = (self.width() - w) // 2
        y = (self.height() - h) // 2

        if self._pressed:
            color = QColor(25, 118, 210, 240)
        elif self._hovered:
            color = QColor(33, 150, 243, 210)
        else:
            color = QColor(130, 130, 130, 110)

        painter.setBrush(color)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawRoundedRect(QRect(x, y, w, h), 2, 2)


class _DiffSplitter(QSplitter):
    """Splitter using _DiffSplitterHandle with centered resize grip."""

    def createHandle(self) -> QSplitterHandle:
        return _DiffSplitterHandle(self.orientation(), self)


class DiffDialog(QDialog):
    """Side-by-side visual diff preview and selective update approval dialog."""

    def __init__(self, plan: SyncPlan, parent=None, occ_status: Any = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Review Modified Cards (Diff Inspector)")
        self.resize(920, 560)

        self._plan = plan
        self._occ_status = occ_status
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
            QCheckBox {{
                font-size: 12px;
                spacing: 8px;
                color: palette(window-text);
            }}
            QCheckBox::indicator, QListWidget::indicator {{
                width: 15px;
                height: 15px;
                border: 1.5px solid rgba(150, 150, 150, 0.7);
                border-radius: 4px;
                background-color: rgba(130, 130, 130, 0.12);
            }}
            QCheckBox::indicator:hover, QListWidget::indicator:hover {{
                border: 1.5px solid #2196F3;
                background-color: rgba(33, 150, 243, 0.15);
            }}
            QCheckBox::indicator:checked, QListWidget::indicator:checked {{
                background-color: #2196F3;
                border: 1.5px solid #1E88E5;
                image: url("{chk_svg}");
            }}
            QListWidget {{
                background-color: rgba(130, 130, 130, 0.04);
                border: 1px solid rgba(130, 130, 130, 0.25);
                border-radius: 8px;
                color: palette(window-text);
                font-size: 12px;
                font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Inter", sans-serif;
                padding: 4px;
                outline: none;
            }}
            QListWidget::item {{
                padding: 6px 8px;
                border-radius: 5px;
                margin-bottom: 2px;
                border: 1px solid transparent;
            }}
            QListWidget::item:hover {{
                background-color: rgba(130, 130, 130, 0.12);
            }}
            QListWidget::item:selected {{
                background-color: rgba(33, 150, 243, 0.18);
                color: palette(window-text);
                border: 1px solid rgba(33, 150, 243, 0.45);
            }}
            QTextBrowser {{
                background-color: rgba(130, 130, 130, 0.04);
                border: 1px solid rgba(130, 130, 130, 0.25);
                border-radius: 8px;
                color: palette(window-text);
                font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Inter", sans-serif;
                padding: 10px 14px;
                selection-background-color: #2196F3;
            }}
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(10)

        # Compact, sleek single-line header
        header_bar = QHBoxLayout()
        header_bar.setContentsMargins(0, 0, 0, 0)
        header_bar.setSpacing(8)

        title = QLabel("Card Updates Preview")
        title.setStyleSheet("font-size: 13px; font-weight: bold; color: palette(window-text);")
        header_bar.addWidget(title)

        count_badge = QLabel(f"• {len(self._all_updates)} modified card(s)")
        count_badge.setStyleSheet("font-size: 12px; color: palette(window-text); opacity: 0.75;")
        header_bar.addWidget(count_badge)

        header_bar.addStretch()
        layout.addLayout(header_bar)

        splitter = _DiffSplitter(Qt.Orientation.Horizontal)
        splitter.setHandleWidth(14)

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
        left_layout.setSpacing(6)
        left_layout.addWidget(self.select_all_cb)
        left_layout.addWidget(self.list_widget)

        # Right Column: Diff Browser
        self.diff_browser = QTextBrowser()
        self.diff_browser.setOpenExternalLinks(False)

        splitter.addWidget(left_layout_container)
        splitter.addWidget(self.diff_browser)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 2)
        layout.addWidget(splitter, stretch=1)

        # Bottom Action Bar
        self.button_box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        self.apply_btn = self.button_box.button(QDialogButtonBox.StandardButton.Ok)
        self.apply_btn.setText("Apply Selected")
        self.apply_btn.setIcon(QIcon())
        self.apply_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.apply_btn.setStyleSheet("""
            QPushButton {
                background-color: #2196F3;
                color: #ffffff;
                font-weight: 600;
                font-size: 12px;
                border: 1px solid #1E88E5;
                border-radius: 6px;
                padding: 6px 16px;
                min-height: 24px;
            }
            QPushButton:hover {
                background-color: #1976D2;
                border-color: #1565C0;
            }
            QPushButton:pressed {
                background-color: #0D47A1;
                border-color: #0D47A1;
            }
        """)

        self.cancel_btn = self.button_box.button(QDialogButtonBox.StandardButton.Cancel)
        self.cancel_btn.setText("Cancel")
        self.cancel_btn.setIcon(QIcon())
        self.cancel_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.cancel_btn.setStyleSheet("""
            QPushButton {
                font-size: 12px;
                border: 1px solid rgba(130, 130, 130, 0.3);
                border-radius: 6px;
                padding: 6px 14px;
                min-height: 24px;
                background: transparent;
                color: palette(window-text);
            }
            QPushButton:hover {
                background-color: #2196F3;
                border-color: #1E88E5;
                color: #ffffff;
            }
            QPushButton:pressed {
                background-color: #1976D2;
                border-color: #1565C0;
                color: #ffffff;
            }
        """)

        self.button_box.accepted.connect(self.accept)
        self.button_box.rejected.connect(self.reject)

        self.rescan_btn = QPushButton("Rescan Vault")
        self.rescan_btn.setIcon(QIcon())
        self.rescan_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.rescan_btn.setToolTip("Discard current preview and re-scan your vault for the latest changes.")
        self.rescan_btn.setStyleSheet("""
            QPushButton {
                font-size: 12px;
                border: 1px solid rgba(130, 130, 130, 0.3);
                border-radius: 6px;
                padding: 6px 14px;
                min-height: 24px;
                background: transparent;
                color: palette(window-text);
            }
            QPushButton:hover {
                background-color: #2196F3;
                border-color: #1E88E5;
                color: #ffffff;
            }
            QPushButton:pressed {
                background-color: #1976D2;
                border-color: #1565C0;
                color: #ffffff;
            }
        """)
        for btn in self.button_box.buttons():
            btn.setIcon(QIcon())
        self.rescan_btn.clicked.connect(self._on_rescan_clicked)
        self.button_box.addButton(self.rescan_btn, QDialogButtonBox.ButtonRole.ActionRole)

        bottom_bar = QHBoxLayout()
        if self._occ_status is not None and getattr(self._occ_status, "ready_to_unflag", 0) > 0:
            self.unflag_occlusions_cb = QCheckBox(f"Also unflag completed occlusions ({self._occ_status.ready_to_unflag} ready)")
            self.unflag_occlusions_cb.setChecked(False)
            self.unflag_occlusions_cb.setToolTip("Unsuspend cards and remove TODO_PROCESS_OCCLUSION from completed image occlusion notes.")
            bottom_bar.addWidget(self.unflag_occlusions_cb)
        bottom_bar.addStretch()
        bottom_bar.addWidget(self.button_box)
        layout.addLayout(bottom_bar)

        # Select first item
        if self.list_widget.count() > 0:
            self.list_widget.setCurrentRow(0)

    @property
    def should_unflag_occlusions(self) -> bool:
        return hasattr(self, "unflag_occlusions_cb") and self.unflag_occlusions_cb.isChecked()

    def reject(self) -> None:
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
                "border: 1px solid rgba(255, 179, 0, 0.35); border-radius: 6px; padding: 6px 10px; "
                "margin-bottom: 8px; font-size: 11px; font-family: sans-serif; line-height: 1.35;'>"
                "<b>Notice: Note modified in Obsidian since scan.</b><br>"
                "<span style='opacity: 0.85; font-size: 11px;'>"
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
