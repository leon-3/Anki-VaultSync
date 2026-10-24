from __future__ import annotations

from typing import Callable, Sequence
from pathlib import Path

try:
    from aqt.qt import (
        QAbstractTableModel,
        QAction,
        QApplication,
        QColor,
        QDesktopServices,
        QDialog,
        QHBoxLayout,
        QHeaderView,
        QIcon,
        QLabel,
        QMenu,
        QModelIndex,
        QPushButton,
        QSortFilterProxyModel,
        QTabBar,
        QTableView,
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
        QHeaderView,
        QLabel,
        QMenu,
        QPushButton,
        QTabBar,
        QTableView,
        QTextEdit,
        QVBoxLayout,
    )
    from PyQt6.QtCore import QAbstractTableModel, QModelIndex, QSortFilterProxyModel, Qt, QUrl
    from PyQt6.QtGui import QAction, QColor, QDesktopServices, QIcon

from markdown2anki.diagnostics import Diagnostic, DiagnosticCategory, DiagnosticLevel

_TABLE_STYLE = """
    QTableView {
        background-color: rgba(255, 255, 255, 0.02);
        border: 1px solid rgba(255, 255, 255, 0.12);
        border-radius: 8px;
        font-size: 12px;
        gridline-color: rgba(255, 255, 255, 0.08);
        outline: none;
    }
    QTableView::item {
        background-color: transparent;
        padding: 4px 8px;
        border: none;
    }
    QTableView::item:selected {
        background-color: rgba(255, 255, 255, 0.05);
        color: palette(window-text);
    }
    QTableView::item:focus {
        background-color: rgba(255, 255, 255, 0.05);
        outline: none;
    }
    QHeaderView {
        background-color: transparent;
        border: none;
    }
    QHeaderView::section {
        background-color: transparent;
        font-size: 12px;
        font-weight: 600;
        padding: 6px 8px;
        border: none;
        border-bottom: 1px solid rgba(255, 255, 255, 0.12);
        color: rgba(220, 220, 220, 0.95);
    }
"""

_BTN_STYLE = """
    QPushButton {
        font-size: 12px;
        padding: 5px 14px;
        border-radius: 6px;
        border: 1px solid rgba(255, 255, 255, 0.15);
        background-color: rgba(255, 255, 255, 0.04);
        color: palette(window-text);
        min-height: 22px;
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
    QPushButton:disabled {
        color: rgba(180, 180, 180, 0.4);
        border-color: rgba(255, 255, 255, 0.06);
        background-color: transparent;
    }
"""

from ..contracts import SyncReport


class DiagnosticsTableModel(QAbstractTableModel):
    """Virtualised model representing a collection of Diagnostic objects."""

    COL_LEVEL = 0
    COL_LOCATION = 1
    COL_CATEGORY = 2
    COL_MESSAGE = 3

    HEADERS = ["Level", "File & Line", "Category", "Issue & Suggestion"]

    def __init__(self, diagnostics: Sequence[Diagnostic], parent=None) -> None:
        super().__init__(parent)
        self._diagnostics: list[Diagnostic] = list(diagnostics)

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:
        if parent.isValid():
            return 0
        return len(self._diagnostics)

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return len(self.HEADERS)

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or not (0 <= index.row() < len(self._diagnostics)):
            return None

        diag = self._diagnostics[index.row()]
        col = index.column()

        if role == Qt.ItemDataRole.DisplayRole:
            if col == self.COL_LEVEL:
                lvl = diag.level.value if hasattr(diag.level, "value") else str(diag.level)
                if lvl == "error":
                    return "🔴 Error"
                if lvl == "warning":
                    return "🟡 Warning"
                return "ℹ️ Info"
            elif col == self.COL_LOCATION:
                return diag.location_label
            elif col == self.COL_CATEGORY:
                cat = diag.category.value if hasattr(diag.category, "value") else str(diag.category)
                return cat.title()
            elif col == self.COL_MESSAGE:
                msg = diag.message
                if diag.suggestion:
                    msg += f"\nTip: {diag.suggestion}"
                return msg

        elif role == Qt.ItemDataRole.ToolTipRole:
            tip = f"<b>{diag.location_label}</b><br>{diag.message}"
            if diag.suggestion:
                tip += f"<br><i>Tip: {diag.suggestion}</i>"
            tip += "<br><br>• Double-click to open in Obsidian<br>• Right-click for more options"
            return tip

        return None

    def headerData(self, section: int, orientation: Qt.Orientation, role: int = Qt.ItemDataRole.DisplayRole):
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            if 0 <= section < len(self.HEADERS):
                return self.HEADERS[section]
        return None

    def get_diagnostic(self, row: int) -> Diagnostic | None:
        if 0 <= row < len(self._diagnostics):
            return self._diagnostics[row]
        return None


class DiagnosticsFilterProxyModel(QSortFilterProxyModel):
    """Filters diagnostics by category group depending on the active tab."""

    TAB_ALL = 0
    TAB_SYNTAX = 1
    TAB_MEDIA = 2
    TAB_CONFLICTS = 3

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._active_tab = self.TAB_ALL

    def set_tab_filter(self, tab_idx: int) -> None:
        self._active_tab = tab_idx
        self.invalidateFilter()

    def filterAcceptsRow(self, source_row: int, source_parent: QModelIndex) -> bool:
        if self._active_tab == self.TAB_ALL:
            return True

        source_model = self.sourceModel()
        if not isinstance(source_model, DiagnosticsTableModel):
            return True

        diag = source_model.get_diagnostic(source_row)
        if diag is None:
            return False

        cat = diag.category.value if hasattr(diag.category, "value") else str(diag.category)

        if self._active_tab == self.TAB_SYNTAX:
            return cat in ("syntax", "cloze", "schema")
        elif self._active_tab == self.TAB_MEDIA:
            return cat in ("media",)
        elif self._active_tab == self.TAB_CONFLICTS:
            return cat in ("conflict", "system")

        return True


class SyncIssuesDialog(QDialog):
    """Structured, virtualised issues dialog shown before or after sync."""

    def __init__(
        self,
        report: SyncReport | None = None,
        parent=None,
        on_open_logs: Callable[[], None] | None = None,
        on_rescan: Callable[[], None] | None = None,
        diagnostics: Sequence[Diagnostic] | None = None,
        vault_path: Path | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("VaultSync - Issues & Diagnostics")
        self.resize(760, 520)

        self._report = report
        self._on_open_logs = on_open_logs
        self._on_rescan = on_rescan
        self._vault_path = vault_path

        # Consolidate diagnostics
        self._diagnostics = self._consolidate_diagnostics(diagnostics, report)

        self._init_ui()

    def _consolidate_diagnostics(
        self,
        explicit_diags: Sequence[Diagnostic] | None,
        report: SyncReport | None,
    ) -> list[Diagnostic]:
        if explicit_diags is not None:
            return list(explicit_diags)

        if report is not None and report.diagnostics:
            return list(report.diagnostics)

        # Synthesize fallback Diagnostics from legacy report structures if empty
        diags: list[Diagnostic] = []
        if report is not None:
            for f_path, reason in report.skipped_files:
                diags.append(
                    Diagnostic(
                        file=f_path,
                        level=DiagnosticLevel.ERROR,
                        category=DiagnosticCategory.SYSTEM,
                        message=reason,
                        suggestion="Check file permissions or verify note contents.",
                    )
                )
            for warn in report.warnings:
                diags.append(
                    Diagnostic(
                        file=Path("(vault)"),
                        level=DiagnosticLevel.WARNING,
                        category=DiagnosticCategory.SYNTAX,
                        message=warn,
                    )
                )
        return diags

    def _init_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setSpacing(10)
        layout.setContentsMargins(18, 16, 18, 16)

        # 1. Header title & summary
        is_pre_sync = self._report is None
        title_text = "<b>Vault Diagnostics: Attention Needed</b>" if is_pre_sync else "<b>Sync Report: Attention Needed</b>"
        title = QLabel(title_text)
        title.setStyleSheet("font-size: 15px; color: #E65100;")
        layout.addWidget(title)

        if not is_pre_sync and self._report is not None:
            summary = (
                f"Successfully processed: <b>{self._report.created_count}</b> added, "
                f"<b>{self._report.updated_count}</b> updated"
            )
            if self._report.occlusions_staged_count > 0:
                summary += f", <b>{self._report.occlusions_staged_count}</b> image occlusions staged"
            summary += ".<br>The following issues or skipped files were encountered:"
        else:
            summary = (
                "The following issues were detected during the vault scan.<br>"
                "You can double-click any note to open it directly in Obsidian and fix problems before syncing."
            )

        summary_lbl = QLabel(summary)
        summary_lbl.setStyleSheet("font-size: 13px;")
        summary_lbl.setWordWrap(True)
        layout.addWidget(summary_lbl)

        # External modification banner if applicable
        if self._report is not None and self._report.externally_modified_files:
            banner = QLabel(
                f"<b>Warning: External Edits Detected:</b> {len(self._report.externally_modified_files)} note(s) were modified in "
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

        # 2. Filter Tab Bar
        self.tab_bar = QTabBar()
        self.tab_bar.setExpanding(False)
        self.tab_bar.setCursor(Qt.CursorShape.PointingHandCursor)

        count_all = len(self._diagnostics)
        count_syntax = sum(
            1 for d in self._diagnostics
            if (d.category.value if hasattr(d.category, "value") else str(d.category)) in ("syntax", "cloze", "schema")
        )
        count_media = sum(
            1 for d in self._diagnostics
            if (d.category.value if hasattr(d.category, "value") else str(d.category)) == "media"
        )
        count_conflicts = sum(
            1 for d in self._diagnostics
            if (d.category.value if hasattr(d.category, "value") else str(d.category)) in ("conflict", "system")
        )

        self.tab_bar.addTab(f"All ({count_all})")
        self.tab_bar.addTab(f"Syntax & Cards ({count_syntax})")
        self.tab_bar.addTab(f"Missing Media ({count_media})")
        self.tab_bar.addTab(f"Conflicts & System ({count_conflicts})")
        self.tab_bar.currentChanged.connect(self._on_tab_changed)
        layout.addWidget(self.tab_bar)

        # 3. Virtualised Table View
        self.table_model = DiagnosticsTableModel(self._diagnostics, parent=self)
        self.proxy_model = DiagnosticsFilterProxyModel(parent=self)
        self.proxy_model.setSourceModel(self.table_model)

        self.table_view = QTableView()
        self.table_view.setModel(self.proxy_model)
        self.table_view.setSelectionBehavior(QTableView.SelectionBehavior.SelectRows)
        self.table_view.setSelectionMode(QTableView.SelectionMode.SingleSelection)
        self.table_view.setAlternatingRowColors(False)
        self.table_view.setShowGrid(True)
        self.table_view.verticalHeader().setVisible(False)
        self.table_view.setStyleSheet(_TABLE_STYLE)
        self.table_view.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table_view.customContextMenuRequested.connect(self._on_context_menu)
        self.table_view.doubleClicked.connect(self._on_row_double_clicked)

        # Column widths & header formatting
        header = self.table_view.horizontalHeader()
        header.setStretchLastSection(True)
        self.table_view.setColumnWidth(DiagnosticsTableModel.COL_LEVEL, 100)
        self.table_view.setColumnWidth(DiagnosticsTableModel.COL_LOCATION, 180)
        self.table_view.setColumnWidth(DiagnosticsTableModel.COL_CATEGORY, 110)

        layout.addWidget(self.table_view)

        # Backward compatibility text edit for tests
        self.issues_edit = QTextEdit()
        self.issues_edit.setVisible(False)
        self.issues_edit.setPlainText(self._generate_plain_text())
        layout.addWidget(self.issues_edit)

        # 4. Action buttons
        btn_layout = QHBoxLayout()
        btn_layout.setSpacing(8)

        copy_btn = QPushButton("Copy Issues")
        copy_btn.setIcon(QIcon())
        copy_btn.setStyleSheet(_BTN_STYLE)
        copy_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        copy_btn.clicked.connect(self._copy_issues)
        btn_layout.addWidget(copy_btn)

        if self._report is not None and self._report.occlusion_dir and self._report.occlusion_dir.is_dir():
            occ_btn = QPushButton("Open Occlusions Folder")
            occ_btn.setIcon(QIcon())
            occ_btn.setStyleSheet(_BTN_STYLE)
            occ_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            occ_btn.clicked.connect(self._open_occlusion_dir)
            btn_layout.addWidget(occ_btn)

        if self._on_open_logs:
            logs_btn = QPushButton("View Full Log...")
            logs_btn.setIcon(QIcon())
            logs_btn.setStyleSheet(_BTN_STYLE)
            logs_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            logs_btn.clicked.connect(self._view_logs)
            btn_layout.addWidget(logs_btn)

        btn_layout.addStretch()

        if self._on_rescan:
            rescan_btn = QPushButton("Re-scan Vault Now")
            rescan_btn.setIcon(QIcon())
            rescan_btn.setStyleSheet(_BTN_STYLE)
            rescan_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            rescan_btn.clicked.connect(self._trigger_rescan)
            btn_layout.addWidget(rescan_btn)

        close_btn = QPushButton("Dismiss")
        close_btn.setIcon(QIcon())
        close_btn.setStyleSheet(_BTN_STYLE)
        close_btn.setDefault(True)
        close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        close_btn.clicked.connect(self.accept)
        btn_layout.addWidget(close_btn)

        layout.addLayout(btn_layout)

    def _on_tab_changed(self, tab_idx: int) -> None:
        self.proxy_model.set_tab_filter(tab_idx)

    def _get_selected_diagnostic(self, index: QModelIndex | None = None) -> Diagnostic | None:
        idx = index or self.table_view.currentIndex()
        if not idx.isValid():
            return None
        source_idx = self.proxy_model.mapToSource(idx)
        return self.table_model.get_diagnostic(source_idx.row())

    def _on_row_double_clicked(self, index: QModelIndex) -> None:
        diag = self._get_selected_diagnostic(index)
        if diag is not None and diag.file.is_file():
            self._open_in_obsidian(diag)

    def _open_in_obsidian(self, diag: Diagnostic) -> None:
        uri = diag.to_obsidian_uri(vault_root=self._vault_path)
        qurl = QUrl.fromEncoded(uri.encode("utf-8"), QUrl.ParsingMode.StrictMode)
        QDesktopServices.openUrl(qurl)

    def _open_in_default_editor(self, diag: Diagnostic) -> None:
        if diag.file.is_file():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(diag.file.resolve())))

    def _open_in_file_manager(self, diag: Diagnostic) -> None:
        target_dir = diag.file.parent if diag.file.is_file() else diag.file
        if target_dir.is_dir():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(target_dir.resolve())))

    def _on_context_menu(self, pos) -> None:
        idx = self.table_view.indexAt(pos)
        if not idx.isValid():
            return

        diag = self._get_selected_diagnostic(idx)
        if diag is None:
            return

        menu = QMenu(self)

        act_obsidian = QAction("Open Note in Obsidian", self)
        act_obsidian.triggered.connect(lambda: self._open_in_obsidian(diag))
        menu.addAction(act_obsidian)

        act_editor = QAction("Open in System Default Editor", self)
        act_editor.triggered.connect(lambda: self._open_in_default_editor(diag))
        menu.addAction(act_editor)

        act_folder = QAction("Show in File Manager", self)
        act_folder.triggered.connect(lambda: self._open_in_file_manager(diag))
        menu.addAction(act_folder)

        menu.addSeparator()

        act_copy = QAction("Copy Issue Details", self)
        act_copy.triggered.connect(lambda: self._copy_single_issue(diag))
        menu.addAction(act_copy)

        menu.exec(self.table_view.viewport().mapToGlobal(pos))

    def _copy_single_issue(self, diag: Diagnostic) -> None:
        text = f"{diag.location_label}: {diag.message}"
        if diag.suggestion:
            text += f" (Tip: {diag.suggestion})"
        cb = QApplication.clipboard()
        if cb:
            cb.setText(text)

    def _generate_plain_text(self) -> str:
        lines: list[str] = []
        if self._report is not None and self._report.skipped_files:
            lines.append("=== SKIPPED FILES ===")
            for path, reason in self._report.skipped_files:
                lines.append(f"• {path.name}: {reason} (Path: {path})")
            lines.append("")

        if self._report is not None and self._report.warnings:
            lines.append("=== WARNINGS ===")
            for warn in self._report.warnings:
                lines.append(f"• {warn}")

        if not lines and self._diagnostics:
            lines.append("=== DIAGNOSTICS ===")
            for d in self._diagnostics:
                line_str = f"• {d.location_label}: {d.message}"
                if d.suggestion:
                    line_str += f" | Tip: {d.suggestion}"
                lines.append(line_str)

        return "\n".join(lines)

    def _copy_issues(self) -> None:
        clipboard = QApplication.clipboard()
        if clipboard:
            # Copy all currently filtered items in plain format
            lines = []
            for row in range(self.proxy_model.rowCount()):
                idx = self.proxy_model.index(row, 0)
                source_idx = self.proxy_model.mapToSource(idx)
                diag = self.table_model.get_diagnostic(source_idx.row())
                if diag:
                    line = f"• [{diag.level}] {diag.location_label} - {diag.message}"
                    if diag.suggestion:
                        line += f" (Tip: {diag.suggestion})"
                    lines.append(line)
            clipboard.setText("\n".join(lines) if lines else self._generate_plain_text())

    def _open_occlusion_dir(self) -> None:
        if self._report is not None and self._report.occlusion_dir:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self._report.occlusion_dir)))

    def _view_logs(self) -> None:
        if self._on_open_logs:
            self._on_open_logs()

    def _trigger_rescan(self) -> None:
        self.accept()
        if self._on_rescan:
            self._on_rescan()


# Alias for backward compatibility
IssuesDialog = SyncIssuesDialog
