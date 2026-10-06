"""Offscreen tests for the interactive timeline widget."""

from __future__ import annotations

import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QPixmap

from manim_editor.editor.timeline_widget import TimelineWidget, block_color, hash_name
from manim_editor.timeline_model import TimelineBlock, TimelineLog


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


class TestInteraction:
    def test_click_block_emits_index(self, timeline: TimelineWidget, qtbot) -> None:
        # total = 4s over 600px; t=1.5s -> x=225
        with qtbot.waitSignal(timeline.blockActivated, timeout=3000) as blocker:
            qtbot.mouseClick(timeline, Qt.MouseButton.LeftButton, pos=QPoint(225, 60))
        assert blocker.args[0] == 1

    def test_click_first_block(self, timeline: TimelineWidget, qtbot) -> None:
        with qtbot.waitSignal(timeline.blockActivated, timeout=3000) as blocker:
            qtbot.mouseClick(timeline, Qt.MouseButton.LeftButton, pos=QPoint(30, 60))
        assert blocker.args[0] == 0

    def test_drag_scrubs(self, timeline: TimelineWidget, qtbot) -> None:
        with qtbot.waitSignal(timeline.scrubRequested, timeout=3000) as blocker:
            qtbot.mousePress(timeline, Qt.MouseButton.LeftButton, pos=QPoint(100, 60))
            qtbot.mouseMove(timeline, QPoint(300, 60))
            qtbot.mouseRelease(timeline, Qt.MouseButton.LeftButton, pos=QPoint(300, 60))
        assert blocker.args[0] == pytest.approx(2.0, abs=0.2)

    def test_wheel_zooms(self, timeline: TimelineWidget) -> None:
        before = timeline.view().span
        timeline.wheelEvent(_wheel(120))
        assert timeline.view().span < before

    def test_wheel_zoom_out_clamps_to_fit(self, timeline: TimelineWidget) -> None:
        timeline.wheelEvent(_wheel(120))
        timeline.wheelEvent(_wheel(-120))
        assert timeline.view().span == pytest.approx(4.0, abs=0.01)

    def test_double_click_fits(self, timeline: TimelineWidget, qtbot) -> None:
        timeline.wheelEvent(_wheel(120))
        timeline.mouseDoubleClickEvent(_click())
        assert timeline.view().span == pytest.approx(4.0, abs=0.01)

    def test_set_log_resets_view(self, timeline: TimelineWidget) -> None:
        timeline.set_log(make_log())
        assert timeline.view().total == pytest.approx(4.0)


def _wheel(delta: int):
    from PySide6.QtGui import QWheelEvent

    return QWheelEvent(
        QPoint(300, 60).toPointF(),
        QPoint(300, 60).toPointF(),
        QPoint(0, 0),
        QPoint(0, delta),
        Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.NoModifier,
        Qt.ScrollPhase.NoScrollPhase,
        False,
    )


def _click():
    from PySide6.QtGui import QMouseEvent

    point = QPoint(300, 60).toPointF()
    return QMouseEvent(
        QMouseEvent.Type.MouseButtonDblClick,
        point,
        point,
        Qt.MouseButton.LeftButton,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
