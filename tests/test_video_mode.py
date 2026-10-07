"""Tests for the rendered-preview (video) mode controller."""

from __future__ import annotations

import pytest
from PySide6.QtCore import QObject, Signal
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QLabel, QSlider

from manim_editor.editor.video_mode import VideoModeController

pytestmark = pytest.mark.usefixtures("qtbot")  # widgets need a QApplication


class _StubTransport(QObject):
    frameReady = Signal(QImage)
    positionChanged = Signal(float)
    durationChanged = Signal(float)

    def __init__(self) -> None:
        super().__init__()
        self.duration = 10.0
        self.seeks: list[float] = []

    def seek(self, seconds: float) -> None:
        self.seeks.append(seconds)


class _StubPreview:
    def __init__(self) -> None:
        self.frames: list[QImage] = []
        self.times: list[tuple[float, float]] = []

    def set_frame(self, image: QImage) -> None:
        self.frames.append(image)

    def set_time(self, seconds: float, total: float) -> None:
        self.times.append((seconds, total))


class _StubTimeline:
    def __init__(self) -> None:
        self.playheads: list[float] = []

    def set_playhead(self, time: float) -> None:
        self.playheads.append(time)


class _Harness:
    def __init__(self) -> None:
        self.transport = _StubTransport()
        self.slider = QSlider()
        self.label = QLabel("—")
        self.preview = _StubPreview()
        self.timeline = _StubTimeline()
        self.stops = 0
        self.statuses: list[str] = []
        self.controller = VideoModeController(
            transport=self.transport,
            slider=self.slider,
            position_label=self.label,
            preview_panel=self.preview,
            timeline=self.timeline,
            stop_playback=self._stop,
            status_line=self.statuses.append,
        )

    def _stop(self) -> None:
        self.stops += 1


class TestEnterExit:
    def test_enter_stops_playback_once_and_reports(self) -> None:
        h = _Harness()
        h.controller.enter()
        assert h.controller.active is True
        assert h.stops == 1
        assert h.statuses and "scrubbing rendered preview" in h.statuses[0]
        h.controller.enter()
        assert h.stops == 1
        assert len(h.statuses) == 1

    def test_exit_deactivates(self) -> None:
        h = _Harness()
        h.controller.enter()
        h.controller.exit()
        assert h.controller.active is False


class TestTransport:
    def test_slider_moved_enters_and_seeks(self) -> None:
        h = _Harness()
        h.slider.sliderMoved.emit(500)
        assert h.controller.active is True
        assert h.transport.seeks == [0.5]

    def test_frames_only_apply_in_video_mode(self) -> None:
        h = _Harness()
        image = QImage(4, 4, QImage.Format.Format_RGBA8888)
        h.transport.frameReady.emit(image)
        assert h.preview.frames == []
        h.controller.enter()
        h.transport.frameReady.emit(image)
        assert h.preview.frames == [image]

    def test_position_updates_playhead_time_and_slider(self) -> None:
        h = _Harness()
        h.controller.enter()
        h.transport.durationChanged.emit(10.0)  # range 0..10000 ms
        h.transport.positionChanged.emit(2.5)
        assert h.timeline.playheads == [2.5]
        assert h.preview.times == [(2.5, 10.0)]
        assert h.slider.value() == 2500

    def test_position_does_not_move_a_held_slider(self) -> None:
        h = _Harness()
        h.controller.enter()
        h.transport.durationChanged.emit(10.0)
        h.slider.setSliderDown(True)
        h.transport.positionChanged.emit(3.0)
        assert h.timeline.playheads == [3.0]
        assert h.slider.value() == 0

    def test_position_ignored_when_inactive(self) -> None:
        h = _Harness()
        h.transport.positionChanged.emit(2.5)
        assert h.timeline.playheads == []
        assert h.preview.times == []

    def test_duration_sets_slider_range_and_label(self) -> None:
        h = _Harness()
        h.transport.durationChanged.emit(72.5)
        assert h.slider.maximum() == 72500
        assert "1:12.5" in h.label.text()
