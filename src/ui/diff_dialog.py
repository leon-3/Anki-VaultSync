from __future__ import annotations

import difflib
import html
from typing import TYPE_CHECKING

from aqt.qt import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QListWidget,
    QListWidgetItem,
    QSplitter,
    Qt,
    QTextBrowser,
    QVBoxLayout,
)

if TYPE_CHECKING:
    from ..contracts import NoteUpdateOp, SyncPlan


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
        diff_lines = list(
            difflib.unified_diff(
                old_val.splitlines(),
                new_val.splitlines(),
                fromfile="Current Anki",
                tofile="Markdown",
                lineterm="",
            )
        )
        diff_html_parts: list[str] = []
        for d in diff_lines:
            escaped = html.escape(d)
            if d.startswith("+"):
                diff_html_parts.append(f"<span style='color:#2e7d32; background-color:#e8f5e9;'>{escaped}</span>")
            elif d.startswith("-"):
                diff_html_parts.append(f"<span style='color:#c62828; background-color:#ffebee;'>{escaped}</span>")
            elif d.startswith("@@"):
                diff_html_parts.append(f"<span style='color:#1565c0;'>{escaped}</span>")
            else:
                diff_html_parts.append(escaped)

        formatted_diff = "<br>".join(diff_html_parts)
        lines.append(
            f"<div style='font-family:monospace; background:#fafafa; border:1px solid #ddd; padding:8px;'>{formatted_diff}</div>"
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

        left_layout_container = QDialog()
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
        layout.addWidget(button_box)

        # Select first item
        if self.list_widget.count() > 0:
            self.list_widget.setCurrentRow(0)

    def _on_item_selected(self, current: QListWidgetItem | None, previous: QListWidgetItem | None) -> None:
        if current is None:
            self.diff_browser.clear()
            return
        idx = current.data(Qt.ItemDataRole.UserRole)
        op = self._all_updates[idx]
        self.diff_browser.setHtml(_generate_diff_html(op))

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
