from __future__ import annotations

from typing import TYPE_CHECKING, Any, Callable

from aqt import mw
from aqt.qt import (
    QEvent,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMouseEvent,
    QPushButton,
    QTimer,
    Qt,
    QVBoxLayout,
    QWidget,
)

if TYPE_CHECKING:
    from ..contracts import SyncReport

_active_notification: SyncNotification | None = None


class SyncNotification(QFrame):
    """Clean, informative multi-line notification popup positioned at the bottom-left.

    Uses native Anki window palette colors, matching the look and feel of Anki Desktop.
    Supports both success reports and persistent error states.
    Closes automatically if the parent window is minimized, hidden, or closed.
    """

    def __init__(
        self,
        report: SyncReport | None = None,
        error: str | Exception | None = None,
        parent: QWidget | None = None,
        include_updates: bool = True,
        on_open_logs: Callable[[], None] | None = None,
    ) -> None:
        super().__init__(
            parent,
            Qt.WindowType.ToolTip | Qt.WindowType.FramelessWindowHint,
        )
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setObjectName("syncNotification")

        self.setStyleSheet("""
            QFrame#syncNotification {
                background-color: palette(window);
                border: 1px solid palette(mid);
                border-radius: 6px;
                padding: 8px 12px;
            }
            QLabel {
                color: palette(window-text);
                font-size: 12px;
                line-height: 1.35;
                background: transparent;
                border: none;
            }
            QPushButton#closeBtn {
                background: transparent;
                border: none;
                color: palette(window-text);
                font-size: 14px;
                font-weight: bold;
                min-width: 18px;
                max-width: 18px;
                min-height: 18px;
                max-height: 18px;
                border-radius: 9px;
                padding: 0;
            }
            QPushButton#closeBtn:hover {
                background-color: palette(midlight);
            }
            QPushButton.notifActionBtn {
                font-size: 11px;
                padding: 3px 8px;
                border-radius: 4px;
            }
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 10)
        layout.setSpacing(6)

        # Header row: Title + Close button
        header_row = QHBoxLayout()
        header_row.setSpacing(8)

        is_error = error is not None
        title_text = "<b><span style='color: #F44336;'>VaultSync Error</span></b>" if is_error else "<b>VaultSync</b>"
        lbl_title = QLabel(title_text)
        header_row.addWidget(lbl_title)
        header_row.addStretch()

        btn_close = QPushButton("×")
        btn_close.setObjectName("closeBtn")
        btn_close.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_close.clicked.connect(self.close)
        header_row.addWidget(btn_close)
        layout.addLayout(header_row)

        # Separator line
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet("background-color: palette(mid); max-height: 1px; border: none;")
        layout.addWidget(sep)

        if is_error:
            err_msg = str(error)
            lbl_err = QLabel(f"Operation failed:<br><b>{err_msg}</b>")
            lbl_err.setWordWrap(True)
            lbl_err.setMaximumWidth(320)
            layout.addWidget(lbl_err)

            if on_open_logs is not None:
                action_row = QHBoxLayout()
                action_row.addStretch()
                logs_btn = QPushButton("View Log")
                logs_btn.setObjectName("notifLogsBtn")
                logs_btn.setProperty("class", "notifActionBtn")
                logs_btn.setCursor(Qt.CursorShape.PointingHandCursor)
                logs_btn.clicked.connect(lambda: (self.close(), on_open_logs()))
                action_row.addWidget(logs_btn)
                layout.addLayout(action_row)

            # Error notifications stay for 30 seconds (sticky)
            timeout_ms = 30000
        else:
            assert report is not None
            total_added = report.created_count
            total_updated = report.updated_count
            total_changed = total_added + total_updated

            if total_changed == 0 and not report.warnings and not report.skipped_files:
                lbl_idle = QLabel("<i>Up to date (no changes)</i>")
                layout.addWidget(lbl_idle)
            else:
                # Total cards added
                lbl_added = QLabel(f"<b>Added:</b> {total_added} card{'s' if total_added != 1 else ''}")
                layout.addWidget(lbl_added)

                # Breakdown
                if total_added > 0:
                    if report.created_basic_count > 0:
                        lbl_bc = QLabel(f"   • Basic: <b>{report.created_basic_count}</b>")
                        layout.addWidget(lbl_bc)

                    if report.created_image_cloze_count > 0:
                        lbl_ic = QLabel(f"   • Image Clozes: <b>{report.created_image_cloze_count}</b>")
                        layout.addWidget(lbl_ic)

                    if report.created_text_cloze_count > 0:
                        lbl_tc = QLabel(f"   • Text Clozes: <b>{report.created_text_cloze_count}</b>")
                        layout.addWidget(lbl_tc)

                if include_updates:
                    lbl_upd = QLabel(f"<b>Updated:</b> {total_updated} card{'s' if total_updated != 1 else ''}")
                    layout.addWidget(lbl_upd)

                if report.media_added_count > 0:
                    lbl_med = QLabel(f"<b>Media:</b> {report.media_added_count} file{'s' if report.media_added_count != 1 else ''}")
                    layout.addWidget(lbl_med)

                if report.deck_moved_count > 0:
                    lbl_mov = QLabel(f"<b>Moved Decks:</b> {report.deck_moved_count} card{'s' if report.deck_moved_count != 1 else ''}")
                    layout.addWidget(lbl_mov)

                issue_count = len(report.warnings) + len(report.skipped_files) + len(report.diagnostics)
                if issue_count > 0:
                    lbl_warn = QLabel(f"<b>Warnings:</b> {issue_count} (see Logs tab)")
                    layout.addWidget(lbl_warn)

            timeout_ms = 10000

        # Auto-dismiss timer
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self.close)
        self._timer.start(timeout_ms)

        self.adjustSize()
        self._position_bottom_left(parent)

        if parent is not None:
            parent.installEventFilter(self)

    def eventFilter(self, watched: Any, event: Any) -> bool:
        if watched == self.parent():
            event_type = event.type()
            # If parent window is hidden or closed, dismiss tooltip
            if event_type in (QEvent.Type.Hide, QEvent.Type.Close):
                self.close()
            elif event_type == QEvent.Type.WindowStateChange:
                parent_w = self.parent()
                if parent_w is not None and hasattr(parent_w, "isMinimized") and parent_w.isMinimized():
                    self.close()
            elif event_type in (QEvent.Type.Move, QEvent.Type.Resize):
                self._position_bottom_left(self.parent())
        return super().eventFilter(watched, event)

    def closeEvent(self, event: Any) -> None:
        parent_w = self.parent()
        if parent_w is not None:
            try:
                parent_w.removeEventFilter(self)
            except Exception:
                pass
        super().closeEvent(event)

    def mousePressEvent(self, event: QMouseEvent | None) -> None:
        if event is not None:
            event.accept()
        self.close()

    def _position_bottom_left(self, parent_widget: QWidget | None = None) -> None:
        target_win = parent_widget or (mw if mw is not None else None)
        if target_win is not None and hasattr(target_win, "geometry"):
            try:
                geom = target_win.geometry()
                margin = 24
                x = geom.x() + margin
                y = geom.bottom() - self.height() - margin
                self.move(x, y)
            except Exception:
                pass


def dismiss_active_notification() -> None:
    """Closes and unregisters any active popup notification."""
    global _active_notification
    if _active_notification is not None:
        try:
            _active_notification.close()
        except Exception:
            pass
        _active_notification = None


def show_sync_notification(
    report: SyncReport,
    parent: QWidget | None = None,
    include_updates: bool = True,
    is_startup: bool = False,
) -> SyncNotification | None:
    """Shows the 10-second multi-line sync notification popup at the bottom-left."""
    global _active_notification

    # Startup sync with no changes stays quiet
    total_changed = report.created_count + report.updated_count
    if is_startup and total_changed == 0 and not report.warnings and not report.skipped_files and not report.diagnostics:
        return None

    dismiss_active_notification()

    notif = SyncNotification(report=report, parent=parent, include_updates=include_updates)
    notif.show()
    _active_notification = notif
    return notif


def show_error_notification(
    error: str | Exception,
    parent: QWidget | None = None,
    on_open_logs: Callable[[], None] | None = None,
) -> SyncNotification:
    """Shows a persistent 30-second error notification popup with action button at bottom-left."""
    global _active_notification

    dismiss_active_notification()

    notif = SyncNotification(error=error, parent=parent, on_open_logs=on_open_logs)
    notif.show()
    _active_notification = notif
    return notif
