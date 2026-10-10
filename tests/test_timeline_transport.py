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


class TestIconButtons:
    BUTTONS = (
        "_start_button",
        "_back_button",
        "_play_button",
        "_forward_button",
        "_end_button",
        "_loop_check",
        "_fit_button",
        "_selection_button",
        "_zoom_out_button",
        "_zoom_in_button",
    )

    @pytest.mark.parametrize("name", BUTTONS)
    def test_buttons_are_icon_only_with_a_tooltip(
        self, transport: TimelineTransport, name: str
    ) -> None:
        button = getattr(transport, name)
        assert button.text() == ""
        assert not button.icon().isNull()
        assert len(button.toolTip()) > 6

    def test_play_button_swaps_icon_and_tooltip(self, transport: TimelineTransport) -> None:
        assert transport.playing() is False
        assert transport._play_button.toolTip().startswith("Play")
        transport.set_playing(True)
        assert transport.playing() is True
        assert transport._play_button.toolTip().startswith("Pause")
        assert transport._play_button.accessibleName() == "Pause"
        transport.set_playing(False)
        assert transport._play_button.toolTip().startswith("Play")


class TestEditOperations:
    def test_operations_start_disabled_and_follow_set_edit_state(
        self, transport: TimelineTransport
    ) -> None:
        from manim_studio.editor.timeline_transport import EDIT_ACTIONS

        ids = [action_id for action_id, _glyph, _tip in EDIT_ACTIONS]
        assert all(not transport.edit_button(i).isEnabled() for i in ids)
        transport.set_edit_state({"undo": True, "apply": True})
        assert transport.edit_button("undo").isEnabled()
        assert transport.edit_button("apply").isEnabled()
        assert not transport.edit_button("delete").isEnabled()
        transport.set_edit_state({})
        assert all(not transport.edit_button(i).isEnabled() for i in ids)

    def test_clicking_an_enabled_operation_emits_its_id(
        self, transport: TimelineTransport, qtbot
    ) -> None:
        spy = QSignalSpy(transport.editRequested)
        transport.set_edit_state({"delete": True, "hold": True})
        qtbot.mouseClick(transport.edit_button("delete"), Qt.MouseButton.LeftButton)
        qtbot.mouseClick(transport.edit_button("hold"), Qt.MouseButton.LeftButton)
        assert [spy.at(i)[0] for i in range(spy.count())] == ["delete", "hold"]

    def test_disabled_operations_do_not_emit(self, transport: TimelineTransport, qtbot) -> None:
        spy = QSignalSpy(transport.editRequested)
        qtbot.mouseClick(transport.edit_button("apply"), Qt.MouseButton.LeftButton)
        assert spy.count() == 0

    def test_the_summary_chip_shows_only_when_there_is_text(
        self, transport: TimelineTransport
    ) -> None:
        assert transport.edits_label.isHidden()
        transport.set_edits_summary("2 timeline edits")
        assert not transport.edits_label.isHidden()
        assert transport.edits_label.text() == "2 timeline edits"
        transport.set_edits_summary("")
        assert transport.edits_label.isHidden()
