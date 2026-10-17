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

    Stays visible for 10 seconds and provides a clean [×] button to dismiss immediately.
    Displays detailed breakdown of added cards (basic, image clozes, text clozes), updated cards,
    media files, and any warnings without emojis.
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
                background-color: #282c34;
                border: 1px solid rgba(255, 255, 255, 0.16);
                border-radius: 6px;
                padding: 8px 12px;
            }
            QLabel {
                color: #d7dae0;
                font-size: 12px;
                line-height: 1.35;
                background: transparent;
                border: none;
            }
            QPushButton#closeBtn {
                background: transparent;
                border: none;
                color: #828997;
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
                background-color: rgba(255, 255, 255, 0.12);
                color: #ffffff;
            }
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 10)
        layout.setSpacing(5)

        # Header row: Clean title + Close button
        header_row = QHBoxLayout()
        header_row.setSpacing(8)

        lbl_title = QLabel("<b>VaultSync</b>")
        lbl_title.setStyleSheet("font-size: 12px; color: #61afef; font-weight: 600;")
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
        sep.setStyleSheet("background-color: rgba(255, 255, 255, 0.08); max-height: 1px; border: none;")
        layout.addWidget(sep)

        total_added = report.created_count
        total_updated = report.updated_count
        total_changed = total_added + total_updated

        if total_changed == 0 and not report.warnings and not report.skipped_files:
            lbl_idle = QLabel("<i>Up to date (no changes)</i>")
            lbl_idle.setStyleSheet("color: #98c379;")
            layout.addWidget(lbl_idle)
        else:
            # a) Total cards added
            lbl_added = QLabel(f"<b>Added:</b> {total_added} card{'s' if total_added != 1 else ''}")
            lbl_added.setStyleSheet("color: #98c379; font-weight: 600;")
            layout.addWidget(lbl_added)

            # Detailed breakdown: basic first, then image clozes, then text clozes
            if total_added > 0:
                if report.created_basic_count > 0:
                    lbl_bc = QLabel(f"   • Basic: <b>{report.created_basic_count}</b>")
                    lbl_bc.setStyleSheet("color: #abb2bf; font-size: 11px;")
                    layout.addWidget(lbl_bc)

                lbl_ic = QLabel(f"   • Image Clozes: <b>{report.created_image_cloze_count}</b>")
                lbl_ic.setStyleSheet("color: #abb2bf; font-size: 11px;")
                layout.addWidget(lbl_ic)

                lbl_tc = QLabel(f"   • Text Clozes: <b>{report.created_text_cloze_count}</b>")
                lbl_tc.setStyleSheet("color: #abb2bf; font-size: 11px;")
                layout.addWidget(lbl_tc)

            # d) Cards updated (at a sync with update)
            if include_updates:
                lbl_upd = QLabel(f"<b>Updated:</b> {total_updated} card{'s' if total_updated != 1 else ''}")
                lbl_upd.setStyleSheet("color: #e5c07b; font-weight: 600;")
                layout.addWidget(lbl_upd)

            # Additional details: Media files
            if report.media_added_count > 0:
                lbl_med = QLabel(f"<b>Media:</b> {report.media_added_count} file{'s' if report.media_added_count != 1 else ''}")
                lbl_med.setStyleSheet("color: #61afef; font-size: 11px;")
                layout.addWidget(lbl_med)

            # Additional details: Deck movements
            if report.deck_moved_count > 0:
                lbl_mov = QLabel(f"<b>Moved Decks:</b> {report.deck_moved_count} card{'s' if report.deck_moved_count != 1 else ''}")
                lbl_mov.setStyleSheet("color: #c678dd; font-size: 11px;")
                layout.addWidget(lbl_mov)

            # Additional details: Warnings / Skipped
            issue_count = len(report.warnings) + len(report.skipped_files)
            if issue_count > 0:
                lbl_warn = QLabel(f"<b>Warnings:</b> {issue_count} (see Logs tab)")
                lbl_warn.setStyleSheet("color: #e06c75; font-size: 11px; font-weight: 600;")
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
