from __future__ import annotations

from typing import Callable
from pathlib import Path

try:
    from aqt.qt import (
        QApplication,
        QDesktopServices,
        QDialog,
        QHBoxLayout,
        QLabel,
        QPushButton,
        QTextEdit,
        QUrl,
        Qt,
        QVBoxLayout,
    )
except ImportError:
    from PyQt6.QtWidgets import (
        QApplication,
        QDialog,
        QHBoxLayout,
        QLabel,
        QPushButton,
        QTextEdit,
        QVBoxLayout,
    )
    from PyQt6.QtCore import Qt, QUrl
    from PyQt6.QtGui import QDesktopServices

from ..contracts import SyncReport


class SyncIssuesDialog(QDialog):
    """Dialog shown when sync completes with warnings, skipped files, or external modifications."""

    def __init__(
        self,
        report: SyncReport,
        parent=None,
        on_open_logs: Callable[[], None] | None = None,
        on_rescan: Callable[[], None] | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("VaultSync - Sync Report & Issues")
        self.resize(640, 430)

        self._report = report
        self._on_open_logs = on_open_logs
        self._on_rescan = on_rescan

        self._init_ui()

    def _init_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setSpacing(12)
        layout.setContentsMargins(18, 18, 18, 18)

        # Header title
        title = QLabel("<b>Sync Report: Attention Needed</b>")
        title.setStyleSheet("font-size: 15px; color: #E65100;")
        layout.addWidget(title)

        # Overview
        summary = (
            f"Successfully processed: <b>{self._report.created_count}</b> added, "
            f"<b>{self._report.updated_count}</b> updated"
        )
        if self._report.occlusions_staged_count > 0:
            summary += f", <b>{self._report.occlusions_staged_count}</b> image occlusions staged"
        summary += ".<br>The following issues or skipped files were encountered:"

        summary_lbl = QLabel(summary)
        summary_lbl.setStyleSheet("font-size: 13px;")
        summary_lbl.setWordWrap(True)
        layout.addWidget(summary_lbl)

        # External modification banner if applicable
        if self._report.externally_modified_files:
            banner = QLabel(
                f"<b>⚠️ External Edits Detected:</b> {len(self._report.externally_modified_files)} note(s) were modified in "
                "Obsidian while VaultSync was open. They were skipped to protect your edits from being overwritten.<br>"
                "Click <b>'Re-scan Vault Now'</b> below to incorporate the latest changes."
            )
            banner.setStyleSheet("""
                QLabel {
                    background: rgba(255, 152, 0, 0.15);
                    border: 1px solid #FF9800;
                    border-radius: 6px;
                    padding: 8px 12px;
                    font-size: 12px;
                }
            """)
            banner.setWordWrap(True)
            layout.addWidget(banner)

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

        if self._report.staged_occlusions_by_tag:
            issue_lines.append("")
            issue_lines.append("=== STAGED IMAGE OCCLUSIONS ===")
            for tag, imgs in sorted(self._report.staged_occlusions_by_tag.items()):
                issue_lines.append(f"• Tag [{tag}]: {', '.join(imgs)}")
            if self._report.occlusion_dir:
                issue_lines.append(f"(A full index.md was created in: {self._report.occlusion_dir})")

        self.issues_edit.setPlainText("\n".join(issue_lines))
        layout.addWidget(self.issues_edit)

        # Bottom buttons
        btn_layout = QHBoxLayout()
        btn_layout.setSpacing(8)

        copy_btn = QPushButton("Copy Issues")
        copy_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        copy_btn.clicked.connect(self._copy_issues)
        btn_layout.addWidget(copy_btn)

        if self._report.occlusion_dir and self._report.occlusion_dir.is_dir():
            occ_btn = QPushButton("Open Occlusions Folder")
            occ_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            occ_btn.clicked.connect(self._open_occlusion_dir)
            btn_layout.addWidget(occ_btn)

        if self._on_open_logs:
            logs_btn = QPushButton("View Full Log...")
            logs_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            logs_btn.clicked.connect(self._view_logs)
            btn_layout.addWidget(logs_btn)

        btn_layout.addStretch()

        if self._on_rescan and self._report.externally_modified_files:
            rescan_btn = QPushButton("Re-scan Vault Now")
            rescan_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            rescan_btn.setStyleSheet("""
                QPushButton {
                    font-size: 13px;
                    font-weight: bold;
                    background-color: #007acc;
                    color: white;
                    border-radius: 6px;
                    padding: 5px 14px;
                }
            """)
            rescan_btn.clicked.connect(self._trigger_rescan)
            btn_layout.addWidget(rescan_btn)

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

    def _open_occlusion_dir(self) -> None:
        if self._report.occlusion_dir:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self._report.occlusion_dir)))

    def _view_logs(self) -> None:
        self.accept()
        if self._on_open_logs:
            self._on_open_logs()

    def _trigger_rescan(self) -> None:
        self.accept()
        if self._on_rescan:
            self._on_rescan()
