"""Offscreen tests for the timeline transport bar."""

from __future__ import annotations

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QSignalSpy

from manim_studio.editor.timeline_transport import SPEEDS, TimelineTransport


@pytest.fixture
def transport(qtbot) -> TimelineTransport:
    widget = TimelineTransport()
    qtbot.addWidget(widget)
    widget.show()
    return widget


class TestSignals:
    def test_play_pause_button(self, transport: TimelineTransport, qtbot) -> None:
        spy = QSignalSpy(transport.playPauseRequested)
        qtbot.mouseClick(transport._play_button, Qt.MouseButton.LeftButton)
        assert spy.count() == 1

    def test_step_buttons_report_direction(self, transport: TimelineTransport, qtbot) -> None:
        spy = QSignalSpy(transport.stepRequested)
        qtbot.mouseClick(transport._back_button, Qt.MouseButton.LeftButton)
        qtbot.mouseClick(transport._forward_button, Qt.MouseButton.LeftButton)
        assert [spy.at(i)[0] for i in range(spy.count())] == [-1, 1]

    def test_jump_buttons_report_target(self, transport: TimelineTransport, qtbot) -> None:
        spy = QSignalSpy(transport.jumpRequested)
        qtbot.mouseClick(transport._start_button, Qt.MouseButton.LeftButton)
        qtbot.mouseClick(transport._end_button, Qt.MouseButton.LeftButton)
        assert [spy.at(i)[0] for i in range(spy.count())] == ["start", "end"]

    def test_loop_checkbox(self, transport: TimelineTransport, qtbot) -> None:
        spy = QSignalSpy(transport.loopToggled)
        transport.set_loop(True)
        assert transport.loop_enabled() is True
        assert [spy.at(i)[0] for i in range(spy.count())] == [True]

    def test_speed_combo_reports_value(self, transport: TimelineTransport) -> None:
        spy = QSignalSpy(transport.speedChanged)
        transport.set_speed(0.25)
        transport.set_speed(2.0)
        assert transport.speed() == pytest.approx(2.0)
        assert [spy.at(i)[0] for i in range(spy.count())] == [0.25, 2.0]


class TestState:
    def test_default_speed_is_one(self, transport: TimelineTransport) -> None:
        assert transport.speed() == pytest.approx(1.0)
        assert 1.0 in SPEEDS

    def test_play_button_flips(self, transport: TimelineTransport) -> None:
        assert transport.playing() is False
        transport.set_playing(True)
        assert transport.playing() is True
        transport.set_playing(False)
        assert transport.playing() is False

    def test_unknown_speed_is_ignored(self, transport: TimelineTransport) -> None:
        transport.set_speed(3.14159)  # not in SPEEDS
        assert transport.speed() == pytest.approx(1.0)


class TestZoomButtons:
    def test_zoom_buttons_report_the_action(self, transport: TimelineTransport, qtbot) -> None:
        spy = QSignalSpy(transport.zoomRequested)
        qtbot.mouseClick(transport._fit_button, Qt.MouseButton.LeftButton)
        qtbot.mouseClick(transport._selection_button, Qt.MouseButton.LeftButton)
        qtbot.mouseClick(transport._zoom_out_button, Qt.MouseButton.LeftButton)
        qtbot.mouseClick(transport._zoom_in_button, Qt.MouseButton.LeftButton)
        assert [spy.at(i)[0] for i in range(spy.count())] == [
            "fit",
            "selection",
            "out",
            "in",
        ]
