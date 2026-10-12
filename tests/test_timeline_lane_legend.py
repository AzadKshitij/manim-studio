"""Each lane is named after the mobject it animates: the timeline's lane legend."""

from __future__ import annotations

from manim_studio.editor.timeline_widget import TimelineWidget, lane_legend
from manim_studio.tracer import ClipTrace


def _clip(animation: str, target: str, lane: int) -> ClipTrace:
    return ClipTrace(animation, target, lane, 0.0, 0.5)


def test_a_lane_is_named_after_the_target_it_animates_most() -> None:
    clips = [
        (_clip("Write", "title", 0), _clip("FadeIn", "dot", 1)),
        (_clip("Rotate", "title", 0), _clip("Transform", "square", 2)),
        (_clip("Rotate", "title", 0),),
    ]
    assert lane_legend(clips) == {0: "title", 1: "dot", 2: "square"}


def test_lanes_without_a_target_have_no_name() -> None:
    assert lane_legend([(_clip("Wait", "", 0),)]) == {}


def test_the_widget_keeps_the_legend_and_paints_with_it(qtbot) -> None:  # type: ignore[no-untyped-def]
    timeline = TimelineWidget()
    qtbot.addWidget(timeline)
    timeline.resize(640, 200)
    timeline.set_clips([(_clip("Write", "title", 0),), (_clip("FadeIn", "dot", 1),)])
    assert timeline.lane_names() == {0: "title", 1: "dot"}
    timeline.grab()  # paints the lane rows and their names without raising
