"""Interactive timeline strip.

v2 behavior (per ManimEditorDoc.md): wheel zoom, drag pan, click a block to jump
the live preview, drag the playhead to scrub.  The widget is paint-only and talks
to the rest of the application exclusively through signals.
"""

from __future__ import annotations

import colorsys

from PySide6.QtCore import QPoint, QRectF, Qt, Signal
from PySide6.QtGui import (
    QColor,
    QFontMetrics,
    QMouseEvent,
    QPainter,
    QPaintEvent,
    QPen,
    QResizeEvent,
    QWheelEvent,
)
from PySide6.QtWidgets import QSizePolicy, QWidget

from manim_editor.timeline_model import TimelineBlock, TimelineLog, TimelineView, format_duration

__all__ = ["TimelineWidget"]

RULER_HEIGHT = 22.0
MIN_BLOCK_LABEL_WIDTH = 44.0
CLICK_SLOP_PX = 4.0


def block_color(name: str) -> QColor:
    """Stable pastel colour derived from the animation name."""
    hue = (hash_name(name) % 360) / 360.0
    red, green, blue = colorsys.hsv_to_rgb(hue, 0.45, 0.85)
    return QColor(int(red * 255), int(green * 255), int(blue * 255))


def hash_name(name: str) -> int:
    """Deterministic, platform-independent hash (Python's hash is salted)."""
    value = 0
    for char in name:
        value = (value * 31 + ord(char)) % 1_000_003
    return value


class TimelineWidget(QWidget):
    """Custom-painted timeline strip with zoom/pan/seek interactions."""

    blockActivated = Signal(int)  # click on a block: jump the live preview there
    scrubRequested = Signal(float)  # drag release: scene time to show
    viewChanged = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._log = TimelineLog(())
        self._view = TimelineView(total=1.0, width=float(max(self.width(), 1)))
        self._playhead = 0.0
        self._playing = False
        self._dragging_playhead = False
        self._panning = False
        self._press_pos: QPoint | None = None
        self._last_mouse_x = 0.0
        self.setMinimumHeight(92)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setToolTip("Wheel: zoom · Shift+wheel / middle-drag: pan · Click: jump · Drag: scrub")

    # ------------------------------------------------------------------
    # State
    # ------------------------------------------------------------------

    @property
    def log(self) -> TimelineLog:
        return self._log

    @property
    def playhead_time(self) -> float:
        return self._playhead

    def set_log(self, log: TimelineLog) -> None:
        self._log = log
        self._view = TimelineView(
            total=max(log.total_duration, 0.01), width=float(max(self.width(), 1))
        )
        self._playhead = min(self._playhead, log.total_duration)
        self.update()

    def set_playhead(self, seconds: float) -> None:
        self._playhead = max(0.0, float(seconds))
        self.update()

    def set_playing(self, playing: bool) -> None:
        self._playing = playing
        self.update()

    def view(self) -> TimelineView:
        return self._view

    def fit(self) -> None:
        self._view = self._view.fit()
        self.update()
        self.viewChanged.emit()

    # ------------------------------------------------------------------
    # Qt events
    # ------------------------------------------------------------------

    def resizeEvent(self, event: QResizeEvent) -> None:
        self._view = TimelineView(
            total=self._view.total,
            width=float(max(self.width(), 1)),
            start=self._view.start,
            span=self._view.span,
        )
        super().resizeEvent(event)

    def wheelEvent(self, event: QWheelEvent) -> None:
        if event.angleDelta().y() == 0:
            return
        steps = event.angleDelta().y() / 120.0
        if event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
            self._view = self._view.pan_px(-steps * 60.0)
        else:
            factor = 1.25**steps if steps > 0 else 0.8 ** (-steps)
            self._view = self._view.zoom_at(event.position().x(), factor)
        self.update()
        self.viewChanged.emit()
        event.accept()

    def mousePressEvent(self, event: QMouseEvent) -> None:
        self.setFocus()
        self._press_pos = event.position().toPoint()
        self._last_mouse_x = event.position().x()
        if event.button() == Qt.MouseButton.MiddleButton or (
            event.button() == Qt.MouseButton.LeftButton
            and event.modifiers() & Qt.KeyboardModifier.ShiftModifier
        ):
            self._panning = True
            return
        if event.button() == Qt.MouseButton.LeftButton:
            self._dragging_playhead = True
            time = self._view.x_to_time(event.position().x())
            self.set_playhead(time)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._panning:
            delta = self._last_mouse_x - event.position().x()
            self._view = self._view.pan_px(delta)
            self._last_mouse_x = event.position().x()
            self.update()
            self.viewChanged.emit()
            return
        if self._dragging_playhead:
            self.set_playhead(self._view.x_to_time(event.position().x()))

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        was_panning, self._panning = self._panning, False
        dragged, self._dragging_playhead = self._dragging_playhead, False
        press, self._press_pos = self._press_pos, None
        if was_panning or press is None:
            return
        moved = (event.position().toPoint() - press).manhattanLength()
        if dragged and moved <= CLICK_SLOP_PX:
            block = self._block_at_x(event.position().x())
            if block is not None:
                self.blockActivated.emit(block.index)
            event.accept()
            return
        if dragged:
            self.scrubRequested.emit(self._view.x_to_time(event.position().x()))
            event.accept()

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:
        self.fit()
        event.accept()

    # ------------------------------------------------------------------
    # Painting
    # ------------------------------------------------------------------

    def paintEvent(self, event: QPaintEvent) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        width = self.width()
        height = self.height()
        painter.fillRect(self.rect(), QColor(0x18, 0x18, 0x18))
        self._draw_ruler(painter, width)
        track_top = RULER_HEIGHT
        track_height = max(24.0, height - RULER_HEIGHT - 10.0)
        if self._log.is_empty:
            painter.setPen(QColor(0x88, 0x88, 0x88))
            painter.drawText(
                self.rect(), Qt.AlignmentFlag.AlignCenter, "No trace yet — reload the preview"
            )
        else:
            metrics = QFontMetrics(self.font())
            for block in self._log.blocks:
                self._draw_block(painter, block, track_top, track_height, metrics)
        self._draw_playhead(painter, height)
        painter.end()

    def _draw_ruler(self, painter: QPainter, width: int) -> None:
        painter.setPen(QColor(0x55, 0x55, 0x55))
        painter.drawLine(0, int(RULER_HEIGHT), width, int(RULER_HEIGHT))
        step = self._nice_step(self._view.span or 1.0, width)
        painter.setPen(QColor(0x99, 0x99, 0x99))
        time = 0.0
        while time <= self._view.total + 1e-9:
            x = self._view.time_to_x(time)
            if -40 <= x <= width + 40:
                painter.drawLine(int(x), int(RULER_HEIGHT) - 6, int(x), int(RULER_HEIGHT))
                painter.drawText(int(x) + 4, 14, format_duration(time))
            time += step

    def _draw_block(
        self,
        painter: QPainter,
        block: TimelineBlock,
        track_top: float,
        track_height: float,
        metrics: QFontMetrics,
    ) -> None:
        left = self._view.time_to_x(block.start)
        right = self._view.time_to_x(block.end)
        if right < 0 or left > self.width():
            return
        width = max(2.0, right - left)
        color = block_color(block.name)
        playing = self._playing and self._playhead >= block.start and self._playhead < block.end
        rect = QRectF(left + 1, track_top + 4, max(1.0, width - 2), track_height - 8)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(color.lighter(125) if playing else color)
        painter.drawRoundedRect(rect, 4, 4)
        if width >= MIN_BLOCK_LABEL_WIDTH:
            painter.setPen(QColor(0x10, 0x10, 0x10))
            text = metrics.elidedText(block.name, Qt.TextElideMode.ElideRight, int(width - 8))
            painter.drawText(rect.adjusted(4, 0, -4, 0), Qt.AlignmentFlag.AlignVCenter, text)

    def _draw_playhead(self, painter: QPainter, height: int) -> None:
        x = self._view.time_to_x(self._playhead)
        if x < -2 or x > self.width() + 2:
            return
        pen = QPen(QColor(0xE6, 0x5A, 0x4C) if self._playing else QColor(0xFF, 0xFF, 0xFF))
        pen.setWidthF(2.0)
        painter.setPen(pen)
        painter.drawLine(int(x), 0, int(x), height)
        painter.setBrush(pen.color())
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawEllipse(QRectF(x - 4, 0, 8, 8))

    @staticmethod
    def _nice_step(span: float, width: int) -> float:
        """Pick a ruler step that keeps labels roughly 90px apart."""
        target = span * (90.0 / max(width, 1))
        for candidate in (0.05, 0.1, 0.25, 0.5, 1, 2, 5, 10, 15, 30, 60, 120, 300, 600):
            if candidate >= target:
                return candidate
        return 600.0

    def _block_at_x(self, x: float) -> TimelineBlock | None:
        time = self._view.x_to_time(x)
        return self._log.block_at(time)
