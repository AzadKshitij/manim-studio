"""Interactive timeline strip.

A real timeline: continuous drag scrubbing (the preview follows the pointer),
click-to-position, wheel panning, Ctrl+wheel zooming, zoom-to-block on
double-click, hover highlighting and keyboard nudging.  Dragging a block's right
edge retimes that section: the stretch is drawn live (visual only) and a
:attr:`TimelineWidget.blockResized` request is emitted on release, which the
application forwards to the host.  The widget is paint-only and talks to the
application exclusively through signals.
"""

from __future__ import annotations

import colorsys

from PySide6.QtCore import QPoint, QRectF, Qt, Signal
from PySide6.QtGui import (
    QColor,
    QFontMetrics,
    QKeyEvent,
    QMouseEvent,
    QPainter,
    QPaintEvent,
    QPen,
    QResizeEvent,
    QWheelEvent,
)
from PySide6.QtWidgets import QSizePolicy, QWidget

from manim_studio.timeline_model import TimelineBlock, TimelineLog, TimelineView, format_duration

__all__ = ["TimelineWidget"]

RULER_HEIGHT = 22.0
MIN_BLOCK_LABEL_WIDTH = 44.0
CLICK_SLOP_PX = 4.0
FRAME_STEP = 1.0 / 30.0
EDGE_GRAB_PX = 5.0
MIN_BLOCK_DURATION = 1.0 / 30.0


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
    """Custom-painted timeline with continuous scrubbing and zoom/pan."""

    scrubRequested = Signal(float)  # scene time under the pointer (continuous)
    playToggleRequested = Signal()
    viewChanged = Signal()
    blockResized = Signal(int, float)  # block index, requested duration (seconds)
    undoRequested = Signal()
    redoRequested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._log = TimelineLog(())
        self._view = TimelineView(total=1.0, width=float(max(self.width(), 1)))
        self._playhead = 0.0
        self._playing = False
        self._hover_index: int | None = None
        self._mode: str | None = None  # "scrub" | "pan" | "resize"
        self._press_pos: QPoint | None = None
        self._last_mouse_x = 0.0
        self._resize_index: int | None = None
        self._resize_duration: float | None = None
        self.setMinimumHeight(96)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setToolTip(
            "Drag: scrub · Drag a block's right edge: retime · Wheel: pan · "
            "Ctrl+wheel: zoom · Double-click: zoom to block\n"
            "←/→: step one frame · Shift+←/→: one second · Home/End · Space: play"
        )

    # ------------------------------------------------------------------
    # State
    # ------------------------------------------------------------------

    @property
    def log(self) -> TimelineLog:
        return self._log

    @property
    def playhead_time(self) -> float:
        return self._playhead

    @property
    def playing(self) -> bool:
        return self._playing

    def set_log(self, log: TimelineLog, *, keep_view: bool = False) -> None:
        self._log = log
        if not keep_view or self._view.total <= 0:
            self._view = TimelineView(
                total=max(log.total_duration, 0.01), width=float(max(self.width(), 1))
            )
        self._playhead = min(self._playhead, log.total_duration)
        self._hover_index = None
        if self._mode == "resize":
            self._mode = None
        self._resize_index = None
        self._resize_duration = None
        self.update()

    def set_playhead(self, seconds: float) -> None:
        self._playhead = max(0.0, float(seconds))
        self.update()

    def set_playing(self, playing: bool) -> None:
        self._playing = bool(playing)
        self.update()

    def view(self) -> TimelineView:
        return self._view

    def fit(self) -> None:
        self._view = self._view.fit()
        self.update()
        self.viewChanged.emit()

    def zoom_to_block(self, index: int) -> None:
        block = self._log.block_by_index(index)
        if block is None:
            return
        span = max(block.duration, 0.05) * 1.15
        self._view = TimelineView(
            total=self._view.total,
            width=float(max(self.width(), 1)),
            start=block.start - 0.05 * block.duration,
            span=span,
        )
        self.update()
        self.viewChanged.emit()

    # ------------------------------------------------------------------
    # Interactions
    # ------------------------------------------------------------------

    def resizeEvent(self, event: QResizeEvent) -> None:
        self._view = TimelineView(
            total=self._view.total,
            width=float(max(self.width(), 1)),
            start=self._view.start,
            span=self._view.span,
        )
        super().resizeEvent(event)

    def _request_scrub(self, x: float) -> None:
        time = self._view.x_to_time(x)
        self._playhead = time
        self.update()
        self.scrubRequested.emit(time)

    def _edge_hit(self, x: float) -> TimelineBlock | None:
        """Return the block whose right edge is within ``EDGE_GRAB_PX`` of ``x``.

        The shared boundary between two blocks belongs to the earlier block, so
        dragging it lengthens/shortens the section that ends there.
        """
        for block in self._log.blocks:
            if abs(x - self._view.time_to_x(block.end)) <= EDGE_GRAB_PX:
                return block
        return None

    def _display_log(self) -> TimelineLog:
        """The log to paint: the resize preview while dragging, else the trace."""
        if self._mode == "resize" and self._resize_index is not None:
            duration = self._resize_duration
            if duration is not None:
                return self._log.scaled_block_duration(self._resize_index, duration)
        return self._log

    def wheelEvent(self, event: QWheelEvent) -> None:
        if event.angleDelta().y() == 0:
            return
        steps = event.angleDelta().y() / 120.0
        zoom_modifiers = Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier
        if event.modifiers() & zoom_modifiers:
            factor = 1.25**steps if steps > 0 else 0.8 ** (-steps)
            self._view = self._view.zoom_at(event.position().x(), factor)
        else:
            self._view = self._view.pan_px(-steps * 60.0)
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
            self._mode = "pan"
            return
        if event.button() == Qt.MouseButton.LeftButton:
            edge = self._edge_hit(event.position().x())
            if edge is not None:
                self._mode = "resize"
                self._resize_index = edge.index
                self._resize_duration = edge.duration
                self._hover_index = edge.index
                self.update()
                return
            self._mode = "scrub"
            self._request_scrub(event.position().x())

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._mode == "pan":
            delta = self._last_mouse_x - event.position().x()
            self._view = self._view.pan_px(delta)
            self._last_mouse_x = event.position().x()
            self.update()
            self.viewChanged.emit()
            return
        if self._mode == "resize" and self._resize_index is not None:
            block = self._log.block_by_index(self._resize_index)
            if block is not None:
                duration = self._view.x_to_time(event.position().x()) - block.start
                self._resize_duration = max(MIN_BLOCK_DURATION, duration)
            self.update()
            return
        block = self._log.block_at(self._view.x_to_time(event.position().x()))
        hover = block.index if block is not None else None
        self.setCursor(
            Qt.CursorShape.SizeHorCursor
            if self._edge_hit(event.position().x()) is not None
            else Qt.CursorShape.ArrowCursor
        )
        if hover != self._hover_index:
            self._hover_index = hover
            if block is not None:
                self.setToolTip(
                    f"{block.name}  ·  #{block.index}  ·  "
                    f"{format_duration(block.start)} - {format_duration(block.end)}\n"
                    "Drag the right edge to retime this section"
                )
            self.update()
        if self._mode == "scrub":
            self._request_scrub(event.position().x())

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        was, self._mode = self._mode, None
        press, self._press_pos = self._press_pos, None
        if was == "resize" and self._resize_index is not None:
            index, duration = self._resize_index, self._resize_duration
            self._resize_index = None
            self._resize_duration = None
            self.unsetCursor()
            self.update()
            if duration is not None:
                self.blockResized.emit(index, duration)
        elif was == "scrub" and press is not None:
            self._request_scrub(event.position().x())
        event.accept()

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:
        block = self._log.block_at(self._view.x_to_time(event.position().x()))
        if block is not None:
            self.zoom_to_block(block.index)
        else:
            self.fit()
        event.accept()

    def keyPressEvent(self, event: QKeyEvent) -> None:
        step = FRAME_STEP
        if event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
            step = 1.0
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            if event.key() == Qt.Key.Key_Z and not (
                event.modifiers() & Qt.KeyboardModifier.ShiftModifier
            ):
                self.undoRequested.emit()
                event.accept()
                return
            if event.key() in (Qt.Key.Key_Y, Qt.Key.Key_Z):
                self.redoRequested.emit()
                event.accept()
                return
        if event.key() in (Qt.Key.Key_Left, Qt.Key.Key_Right):
            delta = -step if event.key() == Qt.Key.Key_Left else step
            self._request_scrub(self._view.time_to_x(self._playhead + delta))
            event.accept()
            return
        if event.key() == Qt.Key.Key_Home:
            self._request_scrub(0.0)
            event.accept()
            return
        if event.key() == Qt.Key.Key_End:
            self._request_scrub(self.width())
            event.accept()
            return
        if event.key() == Qt.Key.Key_Space:
            self.playToggleRequested.emit()
            event.accept()
            return
        if event.key() in (Qt.Key.Key_Plus, Qt.Key.Key_Equal):
            self._view = self._view.zoom_at(self.width() / 2, 1.25)
        elif event.key() == Qt.Key.Key_Minus:
            self._view = self._view.zoom_at(self.width() / 2, 0.8)
        else:
            super().keyPressEvent(event)
            return
        self.update()
        self.viewChanged.emit()
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
            display = self._display_log()
            current = display.index_at(self._playhead)
            for block in display.blocks:
                self._draw_block(
                    painter,
                    block,
                    track_top,
                    track_height,
                    metrics,
                    hovered=block.index == self._hover_index,
                    current=block.index == current,
                )
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
        *,
        hovered: bool,
        current: bool,
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
        if hovered or current:
            pen = QPen(QColor(0xFF, 0xFF, 0xFF) if hovered else QColor(0x7F, 0xC6, 0xFF))
            pen.setWidthF(1.5)
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(rect, 4, 4)
        if width >= MIN_BLOCK_LABEL_WIDTH:
            painter.setPen(QColor(0x10, 0x10, 0x10))
            text = metrics.elidedText(block.name, Qt.TextElideMode.ElideRight, int(width - 8))
            painter.drawText(rect.adjusted(4, 0, -4, 0), Qt.AlignmentFlag.AlignVCenter, text)

    def _draw_playhead(self, painter: QPainter, height: int) -> None:
        x = self._view.time_to_x(self._playhead)
        if x < -2 or x > self.width() + 2:
            return
        color = QColor(0xE6, 0x5A, 0x4C) if self._playing else QColor(0xFF, 0xFF, 0xFF)
        pen = QPen(color)
        pen.setWidthF(2.0)
        painter.setPen(pen)
        painter.drawLine(int(x), 0, int(x), height)
        # Grab handle
        painter.setBrush(color)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawEllipse(QRectF(x - 4, 0, 8, 8))
        # Time chip
        chip_text = format_duration(self._playhead)
        metrics = QFontMetrics(self.font())
        text_width = metrics.horizontalAdvance(chip_text) + 8
        chip = QRectF(
            min(max(x - text_width / 2, 2), self.width() - text_width - 2), 10, text_width, 16
        )
        painter.setBrush(QColor(0x2D, 0x2D, 0x30))
        painter.setPen(QPen(QColor(0x55, 0x55, 0x55)))
        painter.drawRoundedRect(chip, 3, 3)
        painter.setPen(QColor(0xDD, 0xDD, 0xDD))
        painter.drawText(chip, Qt.AlignmentFlag.AlignCenter, chip_text)
        painter.setPen(QPen(color))
        painter.drawLine(int(x), 26, int(x), 30)

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
