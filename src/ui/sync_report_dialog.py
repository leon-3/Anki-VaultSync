from __future__ import annotations

from typing import Callable
from pathlib import Path

try:
    from aqt.qt import (
        QDialog,
        QVBoxLayout,
        QHBoxLayout,
        QLabel,
        QPushButton,
        QTextEdit,
        QApplication,
        Qt,
    )
except ImportError:
    from PyQt6.QtWidgets import (
        QDialog,
        QVBoxLayout,
        QHBoxLayout,
        QLabel,
        QPushButton,
        QTextEdit,
        QApplication,
    )
    from PyQt6.QtCore import Qt

from ..contracts import SyncReport


class SyncIssuesDialog(QDialog):
    """Dialog shown only when sync completes with warnings or skipped files."""

    def __init__(
        self,
        report: SyncReport,
        parent=None,
        on_open_logs: Callable[[], None] | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("VaultSync - Sync Issues Detected")
        self.resize(620, 400)

        self._report = report
        self._on_open_logs = on_open_logs

        self._init_ui()

    def _init_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setSpacing(12)
        layout.setContentsMargins(18, 18, 18, 18)

        # Header title
        title = QLabel("<b>Sync completed with warnings</b>")
        title.setStyleSheet("font-size: 15px; color: #E65100;")
        layout.addWidget(title)

        # Overview
        summary = (
            f"Successfully processed: <b>{self._report.created_count}</b> added, "
            f"<b>{self._report.updated_count}</b> updated.<br>"
            "The following issues were encountered and require your attention:"
        )
        summary_lbl = QLabel(summary)
        summary_lbl.setStyleSheet("font-size: 13px;")
        summary_lbl.setWordWrap(True)
        layout.addWidget(summary_lbl)

        # Issues details box
        self.issues_edit = QTextEdit()
        self.issues_edit.setReadOnly(True)
        self.issues_edit.setStyleSheet("""
            QTextEdit {
                font-family: monospace;
                font-size: 12px;
                border: 1px solid palette(midlight);
                border-radius: 6px;
                padding: 8px;
                background: palette(alternate-base);
            }
        """)

        issue_lines: list[str] = []
        if self._report.skipped_files:
            issue_lines.append("=== SKIPPED FILES ===")
            for path, reason in self._report.skipped_files:
                issue_lines.append(f"• {path.name}: {reason} (Path: {path})")
            issue_lines.append("")

        if self._report.warnings:
            issue_lines.append("=== WARNINGS ===")
            for warn in self._report.warnings:
                issue_lines.append(f"• {warn}")

        self.issues_edit.setPlainText("\n".join(issue_lines))
        layout.addWidget(self.issues_edit)

        # Bottom buttons
        btn_layout = QHBoxLayout()
        btn_layout.setSpacing(8)

        copy_btn = QPushButton("Copy Issues")
        copy_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        copy_btn.clicked.connect(self._copy_issues)
        btn_layout.addWidget(copy_btn)

        if self._on_open_logs:
            logs_btn = QPushButton("View Full Log...")
            logs_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            logs_btn.clicked.connect(self._view_logs)
            btn_layout.addWidget(logs_btn)

        btn_layout.addStretch()

        close_btn = QPushButton("Dismiss")
        close_btn.setDefault(True)
        close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        close_btn.clicked.connect(self.accept)
        btn_layout.addWidget(close_btn)

        layout.addLayout(btn_layout)

    def _copy_issues(self) -> None:
        clipboard = QApplication.clipboard()
        if clipboard:
            clipboard.setText(self.issues_edit.toPlainText())

    def _view_logs(self) -> None:
        self.accept()
        if self._on_open_logs:
            self._on_open_logs()
