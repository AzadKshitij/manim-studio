"""Offscreen tests for the interactive timeline widget."""

from __future__ import annotations

import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QPixmap
from PySide6.QtTest import QSignalSpy

from manim_studio.editor.timeline_widget import FRAME_STEP, TimelineWidget, block_color, hash_name
from manim_studio.timeline_model import TimelineBlock, TimelineLog


def make_log() -> TimelineLog:
    return TimelineLog(
        (
            TimelineBlock(0, "ShowCreation", 0.0, 1.0),
            TimelineBlock(1, "Wait", 1.0, 1.0),
            TimelineBlock(2, "Transform", 2.0, 2.0),
        )
    )


@pytest.fixture
def timeline(qtbot) -> TimelineWidget:
    widget = TimelineWidget()
    qtbot.addWidget(widget)
    widget.resize(600, 110)
    widget.set_log(make_log())
    widget.show()
    widget.setFocus()
    return widget


class TestHelpers:
    def test_hash_is_deterministic(self) -> None:
        assert hash_name("ShowCreation") == hash_name("ShowCreation")
        assert hash_name("ShowCreation") != hash_name("Transform")

    def test_block_color_stable(self) -> None:
        assert block_color("Wait") == block_color("Wait")
        assert block_color("Wait") != block_color("Rotate")


class TestPainting:
    def test_paints_with_blocks(self, timeline: TimelineWidget) -> None:
        pixmap = QPixmap(timeline.size())
        timeline.render(pixmap)
        assert not pixmap.isNull()

    def test_paints_without_trace(self, qtbot) -> None:
        widget = TimelineWidget()
        qtbot.addWidget(widget)
        widget.resize(400, 110)
        pixmap = QPixmap(widget.size())
        widget.render(pixmap)
        assert not pixmap.isNull()

    def test_playhead_updates(self, timeline: TimelineWidget) -> None:
        timeline.set_playhead(1.5)
        assert timeline.playhead_time == pytest.approx(1.5)
        timeline.set_playing(True)
        timeline.render(QPixmap(timeline.size()))


class TestScrubbing:
    def test_click_positions_playhead_exactly(self, timeline: TimelineWidget, qtbot) -> None:
        # total = 4s over 600px; x=225 -> t=1.5 (middle of the Wait block)
        spy = QSignalSpy(timeline.scrubRequested)
        qtbot.mouseClick(timeline, Qt.MouseButton.LeftButton, pos=QPoint(225, 60))
        assert spy.count() >= 1
        assert spy.at(spy.count() - 1)[0] == pytest.approx(1.5, abs=0.02)
        assert timeline.playhead_time == pytest.approx(1.5, abs=0.02)

    def test_drag_scrubs_continuously(self, timeline: TimelineWidget, qtbot) -> None:
        spy = QSignalSpy(timeline.scrubRequested)
        qtbot.mousePress(timeline, Qt.MouseButton.LeftButton, pos=QPoint(100, 60))
        qtbot.mouseMove(timeline, QPoint(200, 60))
        qtbot.mouseMove(timeline, QPoint(300, 60))
        qtbot.mouseMove(timeline, QPoint(400, 60))
        qtbot.mouseRelease(timeline, Qt.MouseButton.LeftButton, pos=QPoint(400, 60))
        values = [spy.at(i)[0] for i in range(spy.count())]
        assert len(values) >= 4  # one per move plus press/release
        assert values == sorted(values)  # monotonically rightwards
        assert values[-1] == pytest.approx(400 / 600 * 4.0, abs=0.02)

    def test_scrub_is_not_snapped_to_blocks(self, timeline: TimelineWidget, qtbot) -> None:
        spy = QSignalSpy(timeline.scrubRequested)
        qtbot.mouseClick(timeline, Qt.MouseButton.LeftButton, pos=QPoint(375, 60))
        assert spy.at(spy.count() - 1)[0] == pytest.approx(2.5, abs=0.02)

    def test_hover_tracks_blocks(self, timeline: TimelineWidget, qtbot) -> None:
        qtbot.mouseMove(timeline, QPoint(60, 60))  # inside block 0
        assert timeline._hover_index == 0
        qtbot.mouseMove(timeline, QPoint(510, 60))  # inside block 2
        assert timeline._hover_index == 2


class TestViewNavigation:
    def test_plain_wheel_pans(self, timeline: TimelineWidget) -> None:
        timeline.wheelEvent(_wheel(120, Qt.KeyboardModifier.ControlModifier))  # zoom in first
        before = timeline.view().start
        timeline.wheelEvent(_wheel(-120))
        assert timeline.view().start > before
        assert timeline.view().span < 4.0

    def test_ctrl_wheel_zooms(self, timeline: TimelineWidget) -> None:
        before = timeline.view().span
        timeline.wheelEvent(_wheel(120, Qt.KeyboardModifier.ControlModifier))
        assert timeline.view().span < before

    def test_zoom_out_clamps_to_fit(self, timeline: TimelineWidget) -> None:
        timeline.wheelEvent(_wheel(120, Qt.KeyboardModifier.ControlModifier))
        timeline.wheelEvent(_wheel(-120, Qt.KeyboardModifier.ControlModifier))
        assert timeline.view().span == pytest.approx(4.0, abs=0.01)

    def test_double_click_zooms_to_block(self, timeline: TimelineWidget, qtbot) -> None:
        # Block 2 spans [2,4] -> span 2.3s, start clamped so the view stays inside [0,4]
        pos = QPoint(int(3.0 / 4.0 * 600), 60)
        qtbot.mouseDClick(timeline, Qt.MouseButton.LeftButton, pos=pos)
        assert timeline.view().span == pytest.approx(2.0 * 1.15, abs=0.05)
        assert timeline.view().start == pytest.approx(4.0 - 2.3, abs=0.05)

    def test_fit_resets(self, timeline: TimelineWidget) -> None:
        timeline.zoom_to_block(2)
        timeline.fit()
        assert timeline.view().span == pytest.approx(4.0, abs=0.01)
        assert timeline.view().start == 0.0


class TestKeyboard:
    def test_arrow_steps_one_frame(self, timeline: TimelineWidget, qtbot) -> None:
        spy = QSignalSpy(timeline.scrubRequested)
        qtbot.keyClick(timeline, Qt.Key.Key_Right)
        assert spy.at(spy.count() - 1)[0] == pytest.approx(FRAME_STEP, abs=1e-6)
        qtbot.keyClick(timeline, Qt.Key.Key_Left)
        assert spy.at(spy.count() - 1)[0] == pytest.approx(0.0, abs=1e-6)

    def test_shift_arrow_steps_one_second(self, timeline: TimelineWidget, qtbot) -> None:
        spy = QSignalSpy(timeline.scrubRequested)
        qtbot.keyClick(timeline, Qt.Key.Key_Right, Qt.KeyboardModifier.ShiftModifier)
        assert spy.at(spy.count() - 1)[0] == pytest.approx(1.0, abs=1e-6)

    def test_home_and_end(self, timeline: TimelineWidget, qtbot) -> None:
        spy = QSignalSpy(timeline.scrubRequested)
        qtbot.keyClick(timeline, Qt.Key.Key_End)
        assert spy.at(spy.count() - 1)[0] == pytest.approx(4.0, abs=0.02)
        qtbot.keyClick(timeline, Qt.Key.Key_Home)
        assert spy.at(spy.count() - 1)[0] == pytest.approx(0.0, abs=0.02)

    def test_space_requests_play_toggle(self, timeline: TimelineWidget, qtbot) -> None:
        spy = QSignalSpy(timeline.playToggleRequested)
        qtbot.keyClick(timeline, Qt.Key.Key_Space)
        assert spy.count() == 1


def _wheel(delta: int, modifiers: Qt.KeyboardModifier = Qt.KeyboardModifier.NoModifier):
    from PySide6.QtGui import QWheelEvent

    return QWheelEvent(
        QPoint(300, 60).toPointF(),
        QPoint(300, 60).toPointF(),
        QPoint(0, 0),
        QPoint(0, delta),
        Qt.MouseButton.NoButton,
        modifiers,
        Qt.ScrollPhase.NoScrollPhase,
        False,
    )
