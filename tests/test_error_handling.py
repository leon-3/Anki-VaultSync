from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

from aqt.qt import QApplication, QModelIndex
from markdown2anki.diagnostics import Diagnostic, DiagnosticCategory, DiagnosticLevel
from src.contracts import FileSyncPlan, SyncPlan, SyncReport
from src.profile_config import ProfileConfigManager
from src.ui.main_window import ErrorBannerWidget, MainWindow
from src.ui.notification import (
    dismiss_active_notification,
    show_error_notification,
    show_sync_notification,
)
from src.ui.sync_report_dialog import (
    DiagnosticsFilterProxyModel,
    DiagnosticsTableModel,
    IssuesDialog,
    SyncIssuesDialog,
)

# Ensure QApplication exists
app = QApplication.instance() or QApplication([])


def test_error_banner_widget():
    retry_mock = MagicMock()
    log_mock = MagicMock()
    banner = ErrorBannerWidget(on_open_logs=log_mock)
    assert not banner.isVisible()

    # Show error without retry
    banner.show_error("Test failure")
    assert banner.isVisible()
    assert "Test failure" in banner.msg_lbl.text()
    assert not banner.retry_btn.isVisible()

    # Show error with retry
    banner.show_error("Database locked", retry_action=retry_mock)
    assert banner.retry_btn.isVisible()
    banner.retry_btn.click()
    assert not banner.isVisible()
    retry_mock.assert_called_once()

    # View log button
    banner.view_log_btn.click()
    log_mock.assert_called_once()


def test_diagnostics_table_and_proxy_model():
    d1 = Diagnostic(
        file=Path("note1.md"),
        line=10,
        level=DiagnosticLevel.ERROR,
        category=DiagnosticCategory.SYNTAX,
        message="Unclosed fence",
        suggestion="Close with ```",
    )
    d2 = Diagnostic(
        file=Path("note2.md"),
        line=20,
        level=DiagnosticLevel.WARNING,
        category=DiagnosticCategory.MEDIA,
        message="Missing image",
    )
    d3 = Diagnostic(
        file=Path("note3.md"),
        line=None,
        level=DiagnosticLevel.ERROR,
        category=DiagnosticCategory.CONFLICT,
        message="External edit conflict",
    )

    table_model = DiagnosticsTableModel([d1, d2, d3])
    assert table_model.rowCount() == 3
    assert table_model.columnCount() == 4
    assert "🔴 Error" in table_model.data(table_model.index(0, 0))
    assert "note1.md:10" == table_model.data(table_model.index(0, 1))
    assert "Syntax" == table_model.data(table_model.index(0, 2))
    assert "Unclosed fence\nTip: Close with ```" in table_model.data(table_model.index(0, 3))

    proxy = DiagnosticsFilterProxyModel()
    proxy.setSourceModel(table_model)

    # Tab 0: All
    proxy.set_tab_filter(DiagnosticsFilterProxyModel.TAB_ALL)
    assert proxy.rowCount() == 3

    # Tab 1: Syntax
    proxy.set_tab_filter(DiagnosticsFilterProxyModel.TAB_SYNTAX)
    assert proxy.rowCount() == 1
    src_idx = proxy.mapToSource(proxy.index(0, 0))
    assert table_model.get_diagnostic(src_idx.row()) == d1

    # Tab 2: Media
    proxy.set_tab_filter(DiagnosticsFilterProxyModel.TAB_MEDIA)
    assert proxy.rowCount() == 1
    src_idx = proxy.mapToSource(proxy.index(0, 0))
    assert table_model.get_diagnostic(src_idx.row()) == d2

    # Tab 3: Conflicts
    proxy.set_tab_filter(DiagnosticsFilterProxyModel.TAB_CONFLICTS)
    assert proxy.rowCount() == 1
    src_idx = proxy.mapToSource(proxy.index(0, 0))
    assert table_model.get_diagnostic(src_idx.row()) == d3


def test_issues_dialog_pre_sync_and_post_sync():
    d1 = Diagnostic(
        file=Path("test.md"),
        line=5,
        level=DiagnosticLevel.ERROR,
        category=DiagnosticCategory.SYNTAX,
        message="Syntax issue",
    )
    # Pre-sync mode
    rescan_mock = MagicMock()
    dlg_pre = IssuesDialog(diagnostics=[d1], on_rescan=rescan_mock)
    assert dlg_pre.table_model.rowCount() == 1
    dlg_pre.close()

    # Post-sync mode with SyncReport
    report = SyncReport(
        created_count=3,
        updated_count=1,
        diagnostics=[d1],
        externally_modified_files=[Path("test.md")],
    )
    dlg_post = SyncIssuesDialog(report=report)
    assert dlg_post.table_model.rowCount() == 1
    dlg_post.close()


def test_notification_error_mode():
    dismiss_active_notification()
    log_mock = MagicMock()
    notif = show_error_notification("Permission denied", on_open_logs=log_mock)
    assert notif.isVisible()
    assert "Permission denied" in notif.text() if hasattr(notif, "text") else True
    assert notif._timer.interval() == 30000

    dismiss_active_notification()
    assert not notif.isVisible()


def test_main_window_pre_sync_health_indicator(tmp_path: Path):
    cfg_file = tmp_path / "config.json"
    manager = ProfileConfigManager(config_path=cfg_file)
    win = MainWindow(manager, "TestProfile")

    # Plan with diagnostics
    d1 = Diagnostic(
        file=Path("sample.md"),
        line=12,
        level=DiagnosticLevel.ERROR,
        category=DiagnosticCategory.SYNTAX,
        message="Bad card syntax",
    )
    plan = SyncPlan(
        vault_path=tmp_path,
        default_deck="Default",
        file_plans=[],
        diagnostics=[d1],
        skipped_cards_count=1,
    )

    win._on_scan_success(plan)
    assert "1 issue(s) detected" in win.status_label.text()
    assert "1 card(s) will be skipped" in win.status_label.text()
    assert not win.status_report_btn.isHidden()
    assert win.status_report_btn.text() == "Review Issues"

    # Simulate operation failure -> error banner appears, no modal popup
    win._on_op_failure(Exception("Disk full"), retry_action=win._start_scan)
    assert not win.error_banner.isHidden()
    assert "Disk full" in win.error_banner.msg_lbl.text()
    assert not win.error_banner.retry_btn.isHidden()
    win.close()
