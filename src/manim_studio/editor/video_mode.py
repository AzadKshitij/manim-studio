"""Rendered-preview (video) transport mode.

When the user scrubs the rendered preview mp4, the editor must stop mirroring
live host frames and let the transport drive the preview panel and playhead
instead.  This controller owns that mode and the slider/transport wiring; live
interactions (play, seek, timeline scrub) leave the mode through ``exit()``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import QObject
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QLabel, QSlider

from manim_studio.timeline_model import format_duration

if TYPE_CHECKING:
    from collections.abc import Callable

    from manim_studio.editor.preview_panel import PreviewPanel
    from manim_studio.editor.timeline_controller import MediaTransport
    from manim_studio.editor.timeline_widget import TimelineWidget

__all__ = ["VideoModeController"]


class VideoModeController(QObject):
    """Owns the video-scrub mode and its slider/transport handlers."""

    def __init__(
        self,
        *,
        transport: MediaTransport,
        slider: QSlider,
        position_label: QLabel,
        preview_panel: PreviewPanel,
        timeline: TimelineWidget,
        stop_playback: Callable[[], object],
        status_line: Callable[[str], None],
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._transport = transport
        self._slider = slider
        self._position_label = position_label
        self._preview_panel = preview_panel
        self._timeline = timeline
        self._stop_playback = stop_playback
        self._status_line = status_line
        self._active = False

        slider.sliderMoved.connect(self._on_slider_moved)
        slider.sliderReleased.connect(self._on_slider_released)
        transport.frameReady.connect(self._on_frame)
        transport.positionChanged.connect(self._on_position)
        transport.durationChanged.connect(self._on_duration)

    @property
    def active(self) -> bool:
        """True while transport frames (not live host frames) drive the preview."""
        return self._active

    def enter(self) -> None:
        """Switch to video mode, stopping live playback on the first entry."""
        if self._active:
            return
        self._active = True
        self._stop_playback()
        self._status_line("scrubbing rendered preview (timeline click returns to live)")

    def exit(self) -> None:
        """Leave video mode (live play/seek/scrub call this)."""
        self._active = False

    # -- transport / slider handlers ---------------------------------------

    def _on_slider_moved(self, milliseconds: int) -> None:
        self.enter()
        self._transport.seek(milliseconds / 1000.0)

    def _on_slider_released(self) -> None:
        self.enter()

    def _on_frame(self, image: QImage) -> None:
        if self._active:
            self._preview_panel.set_frame(image)

    def _on_position(self, seconds: float) -> None:
        if not self._active:
            return
        self._timeline.set_playhead(seconds)
        self._preview_panel.set_time(seconds, self._transport.duration)
        if not self._slider.isSliderDown():
            self._slider.blockSignals(True)
            self._slider.setValue(int(seconds * 1000))
            self._slider.blockSignals(False)

    def _on_duration(self, seconds: float) -> None:
        self._slider.setRange(0, int(seconds * 1000))
        self._position_label.setText(f"0:00.0 / {format_duration(seconds)}")
