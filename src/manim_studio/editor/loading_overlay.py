"""Spinner and caption drawn over the preview while a scene loads.

The overlay is passive: :class:`~manim_studio.editor.preview_events.PreviewEvents`
decides the stage, and the overlay only debounces it (no flicker on quick reloads),
keeps it up long enough to read, and can dim the last frame underneath so a reload
does not go black.  Timing is injected through ``clock`` so tests stay deterministic.
"""

from __future__ import annotations

import time
from collections.abc import Callable

from PySide6.QtCore import QPoint, QRect, Qt, QTimer
from PySide6.QtGui import QColor, QPainter, QPaintEvent, QPen
from PySide6.QtWidgets import QWidget

from manim_studio import loading
from manim_studio.theme import current_theme

__all__ = ["LoadingOverlay"]


class LoadingOverlay(QWidget):
    """A spinning arc with a caption, painted above the preview viewport."""

    FRAME_MS = 60
    STEP_DEGREES = 30
    ARC_DEGREES = 270
    RADIUS = 16
    VEIL_ALPHA = 140  # how much of the last frame is hidden during a reload (~55 %)

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        clock: Callable[[], float] = time.monotonic,
        show_delay_ms: int = loading.SHOW_DELAY_MS,
        min_visible_ms: int = loading.MIN_VISIBLE_MS,
    ) -> None:
        super().__init__(parent)
        # Clicks and keys go to the preview underneath, never to the spinner.
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self._clock = clock
        self._show_delay_ms = show_delay_ms
        self._min_visible_ms = min_visible_ms
        self._caption = ""
        self._veil = False
        self._angle = 0
        self._shown_at: float | None = None

        self._show_timer = QTimer(self)
        self._show_timer.setSingleShot(True)
        self._show_timer.timeout.connect(self._show_due)
        self._hide_timer = QTimer(self)
        self._hide_timer.setSingleShot(True)
        self._hide_timer.timeout.connect(self._hide_now)
        self._frame_timer = QTimer(self)
        self._frame_timer.setInterval(self.FRAME_MS)
        self._frame_timer.timeout.connect(self._advance_frame)
        self.hide()

    # ------------------------------------------------------------------
    # State (read-only views for callers and tests)
    # ------------------------------------------------------------------

    @property
    def is_showing(self) -> bool:
        return self._shown_at is not None

    @property
    def is_pending(self) -> bool:
        """Loading started but the debounce delay has not elapsed yet."""
        return self._show_timer.isActive()

    @property
    def is_hiding(self) -> bool:
        """Loading finished, but the spinner is still held for its minimum time."""
        return self._hide_timer.isActive()

    @property
    def is_animating(self) -> bool:
        return self._frame_timer.isActive()

    @property
    def caption(self) -> str:
        return self._caption

    @property
    def veil(self) -> bool:
        return self._veil

    @property
    def angle(self) -> int:
        return self._angle

    # ------------------------------------------------------------------
    # Control
    # ------------------------------------------------------------------

    def set_caption(self, text: str, *, veil: bool = False) -> None:
        """Start (or update) the loading spinner with ``text``."""
        self._caption = text
        self._veil = veil
        self._hide_timer.stop()  # loading resumed: cancel any pending hide
        if self._shown_at is not None:
            self.update()
            return
        if not self._show_timer.isActive():
            self._show_timer.start(self._show_delay_ms)

    def clear(self) -> None:
        """Loading finished; hide now, or once the spinner has been up long enough."""
        if self._show_timer.isActive():
            self._show_timer.stop()  # it never appeared, so nothing to undo
            return
        if self._shown_at is None or self._hide_timer.isActive():
            return
        wait = loading.remaining_visible_ms(self._shown_at, self._clock(), self._min_visible_ms)
        if wait > 0:
            self._hide_timer.start(wait)
        else:
            self._hide_now()

    def _show_due(self) -> None:
        self._shown_at = self._clock()
        self._frame_timer.start()
        self.show()
        self.raise_()
        self.update()

    def _hide_now(self) -> None:
        self._frame_timer.stop()
        self._shown_at = None
        self.hide()

    def _advance_frame(self) -> None:
        self._angle = (self._angle + self.STEP_DEGREES) % 360
        self.update()

    # ------------------------------------------------------------------
    # Painting
    # ------------------------------------------------------------------

    def paintEvent(self, _event: QPaintEvent) -> None:
        colors = current_theme().colors
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        if self._veil:
            veil = QColor(colors.preview)
            veil.setAlpha(self.VEIL_ALPHA)
            painter.fillRect(self.rect(), veil)
        centre = self.rect().center() + QPoint(0, -10)
        ring = QRect(
            centre.x() - self.RADIUS,
            centre.y() - self.RADIUS,
            2 * self.RADIUS,
            2 * self.RADIUS,
        )
        pen = QPen(QColor(colors.accent), 3)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        # Qt measures arcs counter-clockwise in 1/16 degree units.
        painter.drawArc(ring, -self._angle * 16, self.ARC_DEGREES * 16)
        painter.setPen(QColor(colors.muted))
        label = QRect(0, centre.y() + self.RADIUS + 12, self.width(), 24)
        painter.drawText(
            label,
            Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop,
            self._caption,
        )
        painter.end()
