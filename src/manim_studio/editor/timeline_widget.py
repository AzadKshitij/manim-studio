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
    QContextMenuEvent,
    QFontMetrics,
    QKeyEvent,
    QMouseEvent,
    QPainter,
    QPaintEvent,
    QPen,
    QResizeEvent,
    QWheelEvent,
)
from PySide6.QtWidgets import QMenu, QScrollBar, QSizePolicy, QWidget

from manim_studio.timeline_model import (
    TimelineBlock,
    TimelineLog,
    TimelineView,
    format_duration,
    snap_time,
)

__all__ = ["TimelineWidget"]

#: Height of the horizontal scrollbar strip at the bottom of the widget.
SCROLLBAR_HEIGHT = 14
RULER_HEIGHT = 22.0
MIN_BLOCK_LABEL_WIDTH = 44.0
CLICK_SLOP_PX = 4.0
FRAME_STEP = 1.0 / 30.0
EDGE_GRAB_PX = 5.0
MIN_BLOCK_DURATION = 1.0 / 30.0
#: How close (in pixels) a scrub/retime must be to snap onto a boundary.  Kept a
#: little larger than the edge-grab window so a pointer can snap onto an edge
#: before it becomes a resize handle.
SNAP_PX = 8.0


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
    blockActivated = Signal(int)  # Ctrl+double-click: jump to the block's source line
    blockDurationEditRequested = Signal(int)  # double-click: type an exact duration
    rangeChanged = Signal(float, float)  # loop/export range [start, end] seconds
    blockPlayRequested = Signal(int)
    blockLoopRequested = Signal(int)
    blockExportRangeRequested = Signal(int)
    blockResetTimingRequested = Signal(int)
    blockStillRequested = Signal(int)
    undoRequested = Signal()
    redoRequested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._log = TimelineLog(())
        self._view = TimelineView(total=1.0, width=float(max(self.width(), 1)))
        self._playhead = 0.0
        self._playing = False
        self._hover_index: int | None = None
        self._active_index: int | None = None  # block under the editor caret
        self._snapping = True
        self._fps = 1.0 / FRAME_STEP
        self._range: tuple[float, float] | None = None  # loop / export range
        self._range_anchor = 0.0
        self._timing_clipboard: float | None = None
        self._context_menu: QMenu | None = None  # kept for tests
        self._looping = False  # mirrors the transport's loop toggle
        self._mode: str | None = None  # "scrub" | "pan" | "resize"
        self._press_pos: QPoint | None = None
        self._last_mouse_x = 0.0
        self._resize_index: int | None = None
        self._resize_duration: float | None = None
        self._syncing_scrollbar = False
        self._scrollbar = QScrollBar(Qt.Orientation.Horizontal, self)
        self._scrollbar.setSingleStep(1)
        self._scrollbar.setToolTip("Scroll the zoomed timeline")
        self._scrollbar.setEnabled(False)
        self._scrollbar.valueChanged.connect(self._on_scrollbar_value)
        self.setMinimumHeight(96 + SCROLLBAR_HEIGHT)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setToolTip(
            "Drag: scrub · Drag a block's right edge: retime · Wheel: pan · "
            "Ctrl+wheel: zoom · Double-click a block: set duration · "
            "Double-click empty: fit · Ctrl+double-click: go to code\n"
            "←/→: step one frame · Shift+←/→: one second · Home/End · Space: play · "
            "Ctrl+Z: undo retime"
        )
        self._layout_scrollbar()
        self._sync_scrollbar()

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

    @property
    def active_block(self) -> int | None:
        """Block highlighted for the editor caret, or ``None``."""
        return self._active_index

    def set_active_block(self, index: int | None) -> None:
        """Highlight the block the editor caret currently sits in (or clear it)."""
        if index != self._active_index:
            self._active_index = index
            self.update()

    # -- loop / export range ----------------------------------------------

    def range(self) -> tuple[float, float] | None:
        """The selected ``(start, end)`` range in seconds, or ``None``."""
        return self._range

    def set_range(self, start: float, end: float) -> None:
        """Set the loop/export range (times clamped to ``>= 0``)."""
        lo, hi = sorted((max(0.0, float(start)), max(0.0, float(end))))
        if hi - lo < 1e-3:
            hi = lo + 1e-3
        self._range = (lo, hi)
        self.update()
        self.rangeChanged.emit(lo, hi)

    def clear_range(self) -> None:
        """Drop the loop/export range."""
        if self._range is not None:
            self._range = None
            self.update()

    # -- context menu ------------------------------------------------------

    @property
    def timing_clipboard(self) -> float | None:
        """The copied block duration (seconds), or ``None``."""
        return self._timing_clipboard

    def set_looping(self, enabled: bool) -> None:
        """Mirror the transport's loop toggle so the menu's checkbox is accurate."""
        self._looping = bool(enabled)

    def block_is_looped(self, index: int) -> bool:
        """True when looping is on and the range is exactly this block's span."""
        block = self._log.block_by_index(index)
        if not self._looping or self._range is None or block is None:
            return False
        start, end = self._range
        return abs(start - block.start) < 1e-6 and abs(end - block.end) < 1e-6

    def context_menu(self, index: int | None) -> QMenu:
        """Build the block context menu; ``index`` is the right-clicked block."""
        menu = QMenu(self)
        if index is None:
            menu.addAction("Fit timeline").triggered.connect(self.fit)
            if self._range is not None:
                menu.addAction("Clear selection").triggered.connect(self.clear_range)
            return menu
        menu.addAction("Play this block").triggered.connect(
            lambda: self.blockPlayRequested.emit(index)
        )
        loop = menu.addAction("Loop this block")
        loop.setCheckable(True)
        loop.setChecked(self.block_is_looped(index))
        loop.triggered.connect(lambda _checked=False: self.blockLoopRequested.emit(index))
        menu.addSeparator()
        menu.addAction("Set as export range").triggered.connect(
            lambda: self.blockExportRangeRequested.emit(index)
        )
        menu.addAction("Render still at block end").triggered.connect(
            lambda: self.blockStillRequested.emit(index)
        )
        menu.addSeparator()
        menu.addAction("Copy timing").triggered.connect(lambda: self._copy_timing(index))
        paste = menu.addAction("Paste timing")
        paste.setEnabled(self._timing_clipboard is not None)
        paste.triggered.connect(lambda: self._paste_timing(index))
        menu.addAction("Reset retime").triggered.connect(
            lambda: self.blockResetTimingRequested.emit(index)
        )
        menu.addSeparator()
        menu.addAction("Jump to code").triggered.connect(lambda: self.blockActivated.emit(index))
        menu.addAction("Zoom to block").triggered.connect(lambda: self.zoom_to_block(index))
        return menu

    def _copy_timing(self, index: int) -> None:
        block = self._log.block_by_index(index)
        if block is not None:
            self._timing_clipboard = block.duration

    def _paste_timing(self, index: int) -> None:
        if self._timing_clipboard is not None:
            self.blockResized.emit(index, self._timing_clipboard)

    def contextMenuEvent(self, event: QContextMenuEvent) -> None:
        block = self._log.block_at(self._view.x_to_time(event.pos().x()))
        menu = self.context_menu(block.index if block is not None else None)
        self._context_menu = menu
        menu.exec(event.globalPos())

    @property
    def snapping(self) -> bool:
        """Whether scrubbing/retiming snaps to frames, 0.1s and block edges."""
        return self._snapping

    def set_snapping(self, enabled: bool) -> None:
        """Toggle snapping (the ``View > Snap timeline`` action drives this)."""
        self._snapping = bool(enabled)

    def set_fps(self, fps: float | int | None) -> None:
        """Set the frame rate used for frame snapping (defaults to 30)."""
        try:
            value = float(fps)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return
        if value > 0:
            self._fps = value

    @property
    def frame_step(self) -> float:
        """Duration of one frame (``1/fps``) for single-frame stepping."""
        return 1.0 / self._fps

    def set_log(self, log: TimelineLog, *, keep_view: bool = False) -> None:
        self._log = log
        if not keep_view or self._view.total <= 0:
            self._view = TimelineView(
                total=max(log.total_duration, 0.01), width=float(max(self.width(), 1))
            )
        self._playhead = min(self._playhead, log.total_duration)
        self._hover_index = None
        self._active_index = None
        self._range = None
        if self._mode == "resize":
            self._mode = None
        self._resize_index = None
        self._resize_duration = None
        self.update()
        self._sync_scrollbar()

    def set_playhead(self, seconds: float) -> None:
        self._playhead = max(0.0, float(seconds))
        self.update()

    def set_playing(self, playing: bool) -> None:
        self._playing = bool(playing)
        self.update()

    def view(self) -> TimelineView:
        return self._view

    @property
    def scrollbar(self) -> QScrollBar:
        """The horizontal scrollbar that pans the zoomed view."""
        return self._scrollbar

    def _layout_scrollbar(self) -> None:
        self._scrollbar.setGeometry(
            0,
            max(0, self.height() - SCROLLBAR_HEIGHT),
            self.width(),
            SCROLLBAR_HEIGHT,
        )

    def _sync_scrollbar(self) -> None:
        """Mirror the visible view window onto the scrollbar (values in milliseconds)."""
        bar = self._scrollbar
        total = self._view.total
        span = self._view.span or total
        page = max(1, round(span * 1000.0))
        maximum = max(0, round(total * 1000.0) - page)
        value = round(self._view.start * 1000.0)
        self._syncing_scrollbar = True
        try:
            bar.setRange(0, maximum)
            bar.setPageStep(page)
            bar.setValue(min(max(value, 0), maximum))
        finally:
            self._syncing_scrollbar = False
        bar.setEnabled(maximum > 0)

    def _on_scrollbar_value(self, value: int) -> None:
        """Pan the view when the user drags the scrollbar (ignores sync echoes)."""
        if self._syncing_scrollbar:
            return
        self._view = TimelineView(
            total=self._view.total,
            width=float(max(self.width(), 1)),
            start=value / 1000.0,
            span=self._view.span,
        )
        self.update()
        self.viewChanged.emit()

    def fit(self) -> None:
        self._view = self._view.fit()
        self.update()
        self._sync_scrollbar()
        self.viewChanged.emit()

    def zoom_by(self, factor: float) -> None:
        """Zoom in/out around the widget centre (``factor`` > 1 zooms in)."""
        if factor <= 0:
            return
        self._view = self._view.zoom_at(self.width() / 2, factor)
        self.update()
        self._sync_scrollbar()
        self.viewChanged.emit()

    def fit_selection(self) -> None:
        """Frame the selected range, else the caret's block, else fit everything."""
        start: float | None = None
        end: float | None = None
        if self._range is not None:
            start, end = self._range
        elif self._active_index is not None:
            block = self._log.block_by_index(self._active_index)
            if block is not None:
                start, end = block.start, block.end
        if start is None or end is None:
            self.fit()
            return
        span = max(end - start, 0.05) * 1.15
        self._view = TimelineView(
            total=self._view.total,
            width=float(max(self.width(), 1)),
            start=start - 0.05 * span,
            span=span,
        )
        self.update()
        self._sync_scrollbar()
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
        self._sync_scrollbar()
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
        self._layout_scrollbar()
        self._sync_scrollbar()
        super().resizeEvent(event)

    def _request_scrub(self, x: float, *, snap: bool = True) -> None:
        time = self._view.x_to_time(x)
        if snap:
            time = self._snap(time)
        self._playhead = time
        self.update()
        self.scrubRequested.emit(time)

    def _snap(
        self, time: float, *, exclude: int | None = None, include_playhead: bool = False
    ) -> float:
        """Snap ``time`` onto a block edge/playhead, else the frame/0.1s grid."""
        if not self._snapping:
            return time
        tolerance = SNAP_PX * (self._view.span or 1.0) / max(self.width(), 1)
        candidates = self._snap_candidates(exclude=exclude, include_playhead=include_playhead)
        return snap_time(time, fps=self._fps, candidates=candidates, tolerance=tolerance)

    def _snap_candidates(self, *, exclude: int | None, include_playhead: bool) -> tuple[float, ...]:
        """Block boundaries (minus the block being dragged) and optionally the playhead."""
        points: list[float] = []
        for block in self._log.blocks:
            if block.index == exclude:
                continue
            points.append(block.start)
            points.append(block.end)
        if include_playhead:
            points.append(self._playhead)
        return tuple(points)

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
        self._sync_scrollbar()
        self.viewChanged.emit()
        event.accept()

    def mousePressEvent(self, event: QMouseEvent) -> None:
        self.setFocus()
        self._press_pos = event.position().toPoint()
        self._last_mouse_x = event.position().x()
        if (
            event.button() == Qt.MouseButton.LeftButton
            and event.modifiers() & Qt.KeyboardModifier.ShiftModifier
            and event.position().y() <= RULER_HEIGHT
        ):
            # Shift-drag on the ruler selects a loop/export range.
            self._mode = "range"
            self._range_anchor = self._view.x_to_time(event.position().x())
            self._range = (self._range_anchor, self._range_anchor)
            self.update()
            return
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
        if self._mode == "range":
            current = self._view.x_to_time(event.position().x())
            self._range = tuple(sorted((self._range_anchor, current)))  # type: ignore[assignment]
            self.update()
            return
        if self._mode == "pan":
            delta = self._last_mouse_x - event.position().x()
            self._view = self._view.pan_px(delta)
            self._last_mouse_x = event.position().x()
            self.update()
            self._sync_scrollbar()
            self.viewChanged.emit()
            return
        if self._mode == "resize" and self._resize_index is not None:
            block = self._log.block_by_index(self._resize_index)
            if block is not None:
                edge = self._snap(
                    self._view.x_to_time(event.position().x()),
                    exclude=self._resize_index,
                    include_playhead=True,
                )
                self._resize_duration = max(MIN_BLOCK_DURATION, edge - block.start)
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
        if was == "range":
            if self._range is not None:
                start, end = self._range
                if end - start < 1e-3:
                    self._range = None
                else:
                    self.rangeChanged.emit(start, end)
            self.update()
            event.accept()
            return
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
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            if block is not None:
                self.blockActivated.emit(block.index)
            else:
                self.fit()
        elif block is not None:
            # Double-click a block to type an exact duration for it.
            self.blockDurationEditRequested.emit(block.index)
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
            # Frame stepping must be exact: never let it snap back to an edge.
            self._request_scrub(self._view.time_to_x(self._playhead + delta), snap=False)
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
        self._sync_scrollbar()
        self.viewChanged.emit()
        event.accept()

    # ------------------------------------------------------------------
    # Painting
    # ------------------------------------------------------------------

    def paintEvent(self, event: QPaintEvent) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        width = self.width()
        height = max(0, self.height() - SCROLLBAR_HEIGHT)
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
                    active=block.index == self._active_index,
                )
        self._draw_range(painter, width, height)
        self._draw_playhead(painter, height)
        painter.end()

    def _draw_range(self, painter: QPainter, width: int, height: int) -> None:
        """Draw the selected loop/export range as a band with edge markers."""
        if self._range is None:
            return
        start, end = self._range
        left = self._view.time_to_x(start)
        right = self._view.time_to_x(end)
        if right < -2 or left > width + 2:
            return
        band = QRectF(left, 0.0, max(1.0, right - left), float(height))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(0x5F, 0xE3, 0xC8, 36))
        painter.drawRect(band)
        pen = QPen(QColor(0x5F, 0xE3, 0xC8))
        pen.setWidthF(1.5)
        painter.setPen(pen)
        painter.drawLine(int(left), 0, int(left), height)
        painter.drawLine(int(right), 0, int(right), height)

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
        active: bool = False,
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
        if active:
            # The block the editor caret sits in: a bright aquamarine outline plus
            # a top notch so it reads differently from hover/current/playing.
            pen = QPen(QColor(0x5F, 0xE3, 0xC8))
            pen.setWidthF(2.0)
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(rect.adjusted(-1, -1, 1, 1), 5, 5)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(0x5F, 0xE3, 0xC8))
            painter.drawRect(QRectF(left + 1, track_top + 2, max(2.0, min(width - 2, 10.0)), 3))
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
