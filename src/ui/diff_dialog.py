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
    from ..contracts import NoteUpdateOp, SyncPlan


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
                    f"<del style='color:#c62828; background-color:rgba(198,40,40,0.15); text-decoration:line-through;'>{deleted}</del>"
                )
        elif tag == "insert":
            inserted = "".join(_format_diff_token(t) for t in words_new[j1:j2] if _format_diff_token(t))
            if inserted:
                result.append(
                    f"<ins style='color:#2e7d32; background-color:rgba(46,125,50,0.15); text-decoration:none; font-weight:bold;'>{inserted}</ins>"
                )
        elif tag == "replace":
            deleted = "".join(_format_diff_token(t) for t in words_old[i1:i2] if _format_diff_token(t))
            inserted = "".join(_format_diff_token(t) for t in words_new[j1:j2] if _format_diff_token(t))
            if deleted:
                result.append(
                    f"<del style='color:#c62828; background-color:rgba(198,40,40,0.15); text-decoration:line-through;'>{deleted}</del>"
                )
            if inserted:
                result.append(
                    f"<ins style='color:#2e7d32; background-color:rgba(46,125,50,0.15); text-decoration:none; font-weight:bold;'>{inserted}</ins>"
                )
    return "".join(result).replace(chr(10), "<br>")


def _generate_diff_html(op: NoteUpdateOp) -> str:
    lines: list[str] = []
    lines.append(f"<h3>Card ID: <code>{html.escape(op.card_id)}</code></h3>")
    lines.append(f"<p><b>File:</b> {html.escape(str(op.source_file))}:{op.source_line}</p>")

    if op.deck_changed:
        old_d = ", ".join(op.old_deck_names) if op.old_deck_names else "(unknown)"
        lines.append(
            f"<p><b>Deck Changed:</b> <span style='color:red;'>{html.escape(old_d)}</span> "
            f"&rarr; <span style='color:green;'><b>{html.escape(op.target_deck_name)}</b></span></p>"
        )

    if op.tags_changed:
        old_set = set(op.old_tags)
        new_set = set(op.new_tags)
        removed = sorted(old_set - new_set)
        added = sorted(new_set - old_set)
        tag_diff = []
        for r in removed:
            tag_diff.append(f"<span style='color:red;'>-{html.escape(r)}</span>")
        for a in added:
            tag_diff.append(f"<span style='color:green;'>+{html.escape(a)}</span>")
        lines.append(f"<p><b>Tags:</b> {' '.join(tag_diff)}</p>")

    lines.append("<hr>")

    for f_name in sorted(set(op.old_fields.keys()) | set(op.new_fields.keys())):
        old_val = op.old_fields.get(f_name, "")
        new_val = op.new_fields.get(f_name, "")
        if old_val == new_val:
            lines.append(f"<p><b>Field [{html.escape(f_name)}]:</b> <i>(unchanged)</i></p>")
            continue

        lines.append(f"<p><b>Field [{html.escape(f_name)}]:</b></p>")
        formatted_diff = _generate_word_diff_html(old_val, new_val)
        lines.append(
            f"<div style='font-family:monospace; background:palette(alternate-base); border:1px solid palette(midlight); border-radius:4px; padding:10px; line-height:1.5;'>{formatted_diff}</div>"
        )

    return "".join(lines)


class DiffDialog(QDialog):
    """Side-by-side visual diff preview and selective update approval dialog."""

    def __init__(self, plan: SyncPlan, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Review Modified Cards (Diff Inspector)")
        self.resize(900, 550)

        self._plan = plan
        self._all_updates: list[NoteUpdateOp] = []
        for fp in plan.file_plans:
            self._all_updates.extend(fp.updates)
        self.rescan_requested = False

        self._init_ui()

    def _init_ui(self) -> None:
        layout = QVBoxLayout(self)

        splitter = QSplitter(Qt.Orientation.Horizontal)

        # Left Column: Updates List
        left_widget = QSplitter(Qt.Orientation.Vertical)
        self.select_all_cb = QCheckBox("Select / Deselect All")
        self.select_all_cb.setChecked(True)
        self.select_all_cb.toggled.connect(self._on_select_all_toggled)

        self.list_widget = QListWidget()
        for idx, op in enumerate(self._all_updates):
            preview = op.new_fields.get("Front") or op.new_fields.get("Text") or ""
            # Strip tags and take first 50 chars
            clean_preview = preview.replace("\n", " ")[:45]
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
        button_box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        button_box.button(QDialogButtonBox.StandardButton.Ok).setText("Apply Selected")
        button_box.accepted.connect(self.accept)
        button_box.rejected.connect(self.reject)

        self.rescan_btn = QPushButton("Rescan Vault")
        self.rescan_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.rescan_btn.setToolTip("Discard current preview and re-scan your vault for the latest changes.")
        self.rescan_btn.clicked.connect(self._on_rescan_clicked)
        button_box.addButton(self.rescan_btn, QDialogButtonBox.ButtonRole.ActionRole)

        layout.addWidget(button_box)

        # Select first item
        if self.list_widget.count() > 0:
            self.list_widget.setCurrentRow(0)

    def _on_rescan_clicked(self) -> None:
        self.rescan_requested = True
        self.reject()

    def _is_file_externally_modified(self, op: NoteUpdateOp) -> bool:
        try:
            cur_sha = hashlib.sha256(op.source_file.read_bytes()).hexdigest()
            for fp in self._plan.file_plans:
                if fp.file_path == op.source_file:
                    return cur_sha != fp.sha256_hex
        except Exception:
            pass
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
                "<div style='background-color:#fff3cd; color:#856404; border:1px solid #ffeeba; "
                "border-radius:6px; padding:10px; margin-bottom:12px; font-size:12px; font-family:sans-serif;'>"
                "<b>⚠️ Note modified in Obsidian since scan.</b><br>"
                "This file was edited externally. If you sync now, this note will be skipped for safety. "
                "Click <b>Rescan Vault</b> below to refresh the preview with your latest edits."
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
