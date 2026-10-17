from __future__ import annotations

from typing import TYPE_CHECKING

from aqt import mw
from aqt.qt import (
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

    Uses native Anki window palette colors without custom colored text or emojis,
    matching the native look and feel of Anki Desktop. Stays visible for 10 seconds
    and provides a clean [×] button to dismiss immediately.
    """

    def __init__(
        self,
        report: SyncReport,
        parent: QWidget | None = None,
        include_updates: bool = True,
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
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 10)
        layout.setSpacing(5)

        # Header row: Title + Close button
        header_row = QHBoxLayout()
        header_row.setSpacing(8)

        lbl_title = QLabel("<b>VaultSync</b>")
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

        total_added = report.created_count
        total_updated = report.updated_count
        total_changed = total_added + total_updated

        if total_changed == 0 and not report.warnings and not report.skipped_files:
            lbl_idle = QLabel("<i>Up to date (no changes)</i>")
            layout.addWidget(lbl_idle)
        else:
            # a) Total cards added
            lbl_added = QLabel(f"<b>Added:</b> {total_added} card{'s' if total_added != 1 else ''}")
            layout.addWidget(lbl_added)

            # Detailed breakdown: basic first, then image clozes, then text clozes
            if total_added > 0:
                if report.created_basic_count > 0:
                    lbl_bc = QLabel(f"   • Basic: <b>{report.created_basic_count}</b>")
                    layout.addWidget(lbl_bc)

                lbl_ic = QLabel(f"   • Image Clozes: <b>{report.created_image_cloze_count}</b>")
                layout.addWidget(lbl_ic)

                lbl_tc = QLabel(f"   • Text Clozes: <b>{report.created_text_cloze_count}</b>")
                layout.addWidget(lbl_tc)

            # d) Cards updated (at a sync with update)
            if include_updates:
                lbl_upd = QLabel(f"<b>Updated:</b> {total_updated} card{'s' if total_updated != 1 else ''}")
                layout.addWidget(lbl_upd)

            # Additional details: Media files
            if report.media_added_count > 0:
                lbl_med = QLabel(f"<b>Media:</b> {report.media_added_count} file{'s' if report.media_added_count != 1 else ''}")
                layout.addWidget(lbl_med)

            # Additional details: Deck movements
            if report.deck_moved_count > 0:
                lbl_mov = QLabel(f"<b>Moved Decks:</b> {report.deck_moved_count} card{'s' if report.deck_moved_count != 1 else ''}")
                layout.addWidget(lbl_mov)

            # Additional details: Warnings / Skipped
            issue_count = len(report.warnings) + len(report.skipped_files)
            if issue_count > 0:
                lbl_warn = QLabel(f"<b>Warnings:</b> {issue_count} (see Logs tab)")
                layout.addWidget(lbl_warn)

        # Auto-dismiss after 10 seconds
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self.close)
        self._timer.start(10000)

        self.adjustSize()
        self._position_bottom_left(parent)

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
    if is_startup and total_changed == 0 and not report.warnings and not report.skipped_files:
        return None

    if _active_notification is not None:
        try:
            _active_notification.close()
        except Exception:
            pass
        _active_notification = None

    notif = SyncNotification(report, parent=parent, include_updates=include_updates)
    notif.show()
    _active_notification = notif
    return notif
