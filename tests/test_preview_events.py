"""Tests for the preview-host event router and its session state."""

from __future__ import annotations

from typing import Any

import pytest
from PySide6.QtCore import QObject, Signal
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QLabel

from manim_studio.editor.preview_events import PreviewEvents
from manim_studio.timeline_model import TimelineLog

pytestmark = pytest.mark.usefixtures("qtbot")  # the host-state label is a widget

SAMPLE_LOG: dict[str, Any] = {
    "blocks": [
        {"name": "ShowCreation", "start": 0.0, "duration": 1.0, "index": 0},
        {"name": "Wait", "start": 1.0, "duration": 0.5, "index": 1},
    ],
}


class _StubEditor:
    def __init__(self) -> None:
        self.cleared = 0
        self.marks: list[tuple[int, str]] = []

    def clear_error_marker(self) -> None:
        self.cleared += 1

    def set_error_marker(self, line: int, text: str) -> None:
        self.marks.append((line, text))


class _StubTimeline:
    def __init__(self) -> None:
        self.log = TimelineLog(())
        self.playhead_time = 0.0
        self.playing = False

    def set_log(self, log: TimelineLog, *, keep_view: bool = False) -> None:
        self.log = log
        self.keep_view = keep_view

    def set_playhead(self, time: float) -> None:
        self.playhead_time = time

    def set_playing(self, playing: bool) -> None:
        self.playing = playing

    def set_fps(self, fps: float | int | None) -> None:
        self.fps = fps


class _StubPanel:
    def __init__(self) -> None:
        self.statuses: list[tuple[str, str]] = []
        self.times: list[tuple[float, float]] = []
        self.frames: list[QImage] = []
        self.cache_status = "sentinel"

    def set_status(self, state: str, note: str = "") -> None:
        self.statuses.append((state, note))

    def set_time(self, seconds: float, total: float) -> None:
        self.times.append((seconds, total))

    def set_frame(self, image: QImage) -> None:
        self.frames.append(image)

    def set_cache_status(self, text: str) -> None:
        self.cache_status = text


class _StubShell:
    def __init__(self) -> None:
        self.lines: list[str] = []

    def append(self, text: str) -> None:
        self.lines.append(text)


class _StubController(QObject):
    logReceived = Signal(dict)
    timingChanged = Signal(dict)
    statusChanged = Signal(str, dict)
    tickReceived = Signal(float, int, bool)
    stoppedReceived = Signal(dict)
    errorReceived = Signal(dict)
    frameReceived = Signal(QImage)
    stateChanged = Signal(str)


class _Harness:
    def __init__(self, initial_frame: str = "end") -> None:
        self.editor = _StubEditor()
        self.timeline = _StubTimeline()
        self.panel = _StubPanel()
        self.shell = _StubShell()
        self.controller = _StubController()
        self.label = QLabel()
        self.statuses: list[str] = []
        self.latexs = 0
        self.video = False
        self.events = PreviewEvents(
            editor=self.editor,
            timeline=self.timeline,
            preview_panel=self.panel,
            shell=self.shell,
            controller=self.controller,
            host_state_label=self.label,
            initial_frame=initial_frame,
            status_line=self.statuses.append,
            refresh_latex=self._refresh_latex,
            video_mode_active=lambda: self.video,
        )

    def _refresh_latex(self) -> None:
        self.latexs += 1


class TestOnLog:
    def test_populates_timeline_and_state(self) -> None:
        h = _Harness()
        h.events.on_log(SAMPLE_LOG)
        assert h.timeline.log.num_blocks == 2
        assert h.events.total_duration == pytest.approx(1.5)
        assert h.events.play_from_index is None
        assert h.editor.cleared == 1
        assert h.panel.cache_status == ""
        assert h.timeline.playhead_time == pytest.approx(1.5)
        assert h.statuses and h.statuses[-1].startswith("trace: 2 blocks")

    def test_initial_frame_start_keeps_playhead_at_zero(self) -> None:
        h = _Harness(initial_frame="start")
        h.events.on_log(SAMPLE_LOG)
        assert h.timeline.playhead_time == 0.0

    def test_malformed_payload_reports_and_keeps_timeline(self) -> None:
        h = _Harness()
        h.events.on_log({"blocks": [{"nope": True}]})
        assert h.timeline.log.is_empty
        assert h.shell.lines and "bad trace payload" in h.shell.lines[0]

    def test_controller_signal_is_wired(self) -> None:
        h = _Harness()
        h.controller.logReceived.emit(SAMPLE_LOG)
        assert h.timeline.log.num_blocks == 2


class TestOnTiming:
    def test_applies_blocks_and_keeps_the_view(self) -> None:
        h = _Harness()
        h.events.on_log(SAMPLE_LOG)
        h.events.on_timing(
            {
                "blocks": [
                    {"name": "ShowCreation", "start": 0.0, "duration": 2.0, "index": 0},
                    {"name": "Wait", "start": 2.0, "duration": 0.5, "index": 1},
                ]
            }
        )
        assert h.timeline.keep_view is True
        assert h.events.total_duration == pytest.approx(2.5)
        assert h.panel.times[-1][1] == pytest.approx(2.5)

    def test_clamps_the_playhead_to_the_new_total(self) -> None:
        h = _Harness()
        h.events.on_log(SAMPLE_LOG)  # total 1.5, playhead 1.5
        h.events.on_timing(
            {"blocks": [{"name": "ShowCreation", "start": 0.0, "duration": 0.4, "index": 0}]}
        )
        assert h.events.total_duration == pytest.approx(0.4)
        assert h.timeline.playhead_time == pytest.approx(0.4)

    def test_malformed_payload_reports_and_keeps_timeline(self) -> None:
        h = _Harness()
        h.events.on_log(SAMPLE_LOG)
        h.events.on_timing({"blocks": [{"nope": True}]})
        assert h.timeline.log.num_blocks == 2
        assert h.shell.lines and "bad timing payload" in h.shell.lines[-1]

    def test_controller_signal_is_wired(self) -> None:
        h = _Harness()
        h.controller.timingChanged.emit(
            {"blocks": [{"name": "ShowCreation", "start": 0.0, "duration": 1.0, "index": 0}]}
        )
        assert h.events.total_duration == pytest.approx(1.0)


class TestCallLines:
    def test_call_lines_are_stored_and_exposed(self) -> None:
        h = _Harness()
        h.events.on_log({**SAMPLE_LOG, "call_sites": [12, None]})
        assert h.events.call_line(0) == 12
        assert h.events.call_line(1) is None
        assert h.events.call_line(5) is None

    def test_missing_or_bad_call_sites_are_ignored(self) -> None:
        h = _Harness()
        h.events.on_log(SAMPLE_LOG)
        assert h.events.call_line(0) is None
        h.events.on_log({**SAMPLE_LOG, "call_sites": "nope"})
        assert h.events.call_line(0) is None

    def test_call_lines_property_and_block_lookup(self) -> None:
        h = _Harness()
        h.events.on_log({**SAMPLE_LOG, "call_sites": [12, 20]})
        assert h.events.call_lines == (12, 20)
        assert h.events.block_at_line(11) is None  # before the first play()
        assert h.events.block_at_line(12) == 0
        assert h.events.block_at_line(19) == 0  # still inside block 0's call
        assert h.events.block_at_line(20) == 1
        assert h.events.block_at_line(99) == 1


class TestStatusAndTicks:
    def test_on_status_sets_playing(self) -> None:
        h = _Harness()
        h.events.on_status("playing", {})
        assert h.events.playing is True
        assert h.timeline.playing is True
        h.events.on_status("paused", {"note": "n"})
        assert h.events.playing is False
        assert h.panel.statuses[-1] == ("paused", "n")

    def test_tick_moves_playhead_only_when_live(self) -> None:
        h = _Harness()
        h.events.on_log(SAMPLE_LOG)
        h.events.on_tick(1.0, 0, False)
        assert h.timeline.playhead_time == pytest.approx(1.0)
        h.video = True
        h.events.on_tick(0.5, 0, False)
        assert h.timeline.playhead_time == pytest.approx(1.0)

    def test_tick_while_scrubbing_is_ignored(self) -> None:
        h = _Harness()
        h.events.on_tick(0.7, 0, True)
        assert h.timeline.playhead_time == 0.0

    def test_stopped_reports_reason(self) -> None:
        h = _Harness()
        h.events.on_stopped({"user_stopped": True})
        assert h.statuses[-1] == "playback stopped"
        h.events.on_stopped({})
        assert h.statuses[-1] == "playback finished"


class TestErrorsAndHostState:
    def test_error_marks_editor_and_shell(self) -> None:
        h = _Harness()
        h.events.on_error({"message": "ValueError: boom", "line": 3, "traceback": "tb"})
        assert h.editor.marks == [(3, "ValueError: boom")]
        assert any("ERROR" in line for line in h.shell.lines)
        assert any("preview error" in text for text in h.statuses)

    def test_latex_error_refreshes_indicator(self) -> None:
        h = _Harness()
        h.events.on_error({"message": "LaTeX failed"})
        assert h.latexs == 1

    def test_host_state_updates_label_and_crash_status(self) -> None:
        h = _Harness()
        h.events.on_host_state("crashed")
        assert "crashed" in h.label.text()
        assert ("crashed", "host process died — press Reload") in h.panel.statuses
