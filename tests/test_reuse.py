"""plan_reuse: which cached blocks survive an edit (pure logic, no GL)."""

from __future__ import annotations

from manim_studio.reuse import ConstructMap, RunSnapshot, plan_reuse
from manim_studio.timeline_model import TimelineBlock

SCENE = """\
from manimlib import *

HELPER_COLOR = BLUE


def helper(scene):
    scene.play(FadeIn(Dot()), run_time=0.5)


class Demo(Scene):
    def setup(self):
        self.k = 1

    def construct(self):
        circle = Circle()
        self.play(ShowCreation(circle), run_time=1.0)
        self.wait(0.5)
        for _ in range(2):
            self.play(circle.animate.shift(RIGHT), run_time=0.5)
            circle.set_color(RED)
        self.play(FadeOut(circle), run_time=1.0)
        self.wait(1.0)
"""
# 1-based lines of the calls above.
LINES = {"create": 16, "wait1": 17, "loop_play": 19, "fade": 21, "wait2": 22}


def _snapshot(
    text: str,
    *,
    lines: dict[str, int] | None = None,
    durations: tuple[float, ...] = (1.0, 0.5, 0.5, 0.5, 1.0, 1.0),
    signature: object = ("720p", 30),
) -> RunSnapshot:
    lines = lines or LINES
    call_lines = (
        lines["create"],
        lines["wait1"],
        lines["loop_play"],
        lines["loop_play"],
        lines["fade"],
        lines["wait2"],
    )
    names = ("ShowCreation", "Wait", "Animate", "Animate", "FadeOut", "Wait")
    blocks, start = [], 0.0
    for i, (name, duration) in enumerate(zip(names, durations, strict=True)):
        blocks.append(TimelineBlock(i, name, start, duration))
        start += duration
    sites = tuple((f"site{line}", n) for n, line in enumerate(call_lines))
    return RunSnapshot(
        text=text,
        scene_name="Demo",
        blocks=tuple(blocks),
        call_lines=call_lines,
        sites=sites,
        signature=signature,
    )


ALL = frozenset(range(6))


class TestConstructMap:
    def test_reach_of_a_plain_statement_is_its_last_line(self) -> None:
        cmap = ConstructMap.parse(SCENE, "Demo")
        assert cmap is not None
        assert cmap.reach_end(LINES["create"]) == LINES["create"]
        assert cmap.reach_end(LINES["fade"]) == LINES["fade"]

    def test_reach_inside_a_loop_is_the_end_of_the_loop(self) -> None:
        cmap = ConstructMap.parse(SCENE, "Demo")
        assert cmap is not None
        assert cmap.reach_end(LINES["loop_play"]) == 20  # includes the set_color line

    def test_calls_in_helpers_are_never_reachable(self) -> None:
        cmap = ConstructMap.parse(SCENE, "Demo")
        assert cmap is not None
        assert cmap.reach_end(7) is None

    def test_lambda_bodies_are_not_trusted(self) -> None:
        text = (
            "class Demo(Scene):\n"
            "    def construct(self):\n"
            "        go = lambda: self.play(FadeIn(Dot()))\n"
            "        go()\n"
        )
        cmap = ConstructMap.parse(text, "Demo")
        assert cmap is not None
        assert cmap.reach_end(3) is None

    def test_unknown_scene_or_syntax_error(self) -> None:
        assert ConstructMap.parse(SCENE, "Missing") is None
        assert ConstructMap.parse("def (:", "Demo") is None


class TestPlanReuse:
    def test_identical_run_reuses_every_cached_block(self) -> None:
        old = _snapshot(SCENE)
        plan = plan_reuse(old, _snapshot(SCENE), ALL)
        assert plan.blocks == ALL
        assert plan.reason == "all blocks unchanged"

    def test_only_completed_blocks_are_reused(self) -> None:
        old = _snapshot(SCENE)
        assert plan_reuse(old, _snapshot(SCENE), {0, 2}).blocks == {0, 2}

    def test_no_cache_or_no_previous_run_reuses_nothing(self) -> None:
        assert plan_reuse(None, _snapshot(SCENE), ALL).blocks == frozenset()
        assert plan_reuse(_snapshot(SCENE), _snapshot(SCENE), set()).blocks == frozenset()

    def test_editing_the_last_block_keeps_everything_before_it(self) -> None:
        new_text = SCENE.replace("self.wait(1.0)", "self.wait(1.5)")
        new = _snapshot(new_text, durations=(1.0, 0.5, 0.5, 0.5, 1.0, 1.5))
        plan = plan_reuse(_snapshot(SCENE), new, ALL)
        assert plan.blocks == {0, 1, 2, 3, 4}

    def test_retiming_a_middle_block_keeps_only_blocks_before_it(self) -> None:
        new_text = SCENE.replace("self.wait(0.5)", "self.wait(0.75)")
        new = _snapshot(new_text, durations=(1.0, 0.75, 0.5, 0.5, 1.0, 1.0))
        plan = plan_reuse(_snapshot(SCENE), new, ALL)
        assert plan.blocks == {0}  # later blocks shifted in time

    def test_state_edit_between_blocks_invalidates_from_the_next_block(self) -> None:
        new_text = SCENE.replace("circle = Circle()", "circle = Circle(radius=2)")
        plan = plan_reuse(_snapshot(SCENE), _snapshot(new_text), ALL)
        assert plan.blocks == frozenset()  # the edit precedes every play

    def test_edit_after_a_loop_body_spares_the_loop_blocks(self) -> None:
        new_text = SCENE.replace("FadeOut(circle)", "FadeOut(circle, shift=UP)")
        plan = plan_reuse(_snapshot(SCENE), _snapshot(new_text), ALL)
        assert plan.blocks == {0, 1, 2, 3}

    def test_edit_inside_a_loop_body_invalidates_every_iteration(self) -> None:
        # Iteration 1's play is above the edit, but iteration 2 runs the edited line first.
        new_text = SCENE.replace("circle.set_color(RED)", "circle.set_color(GREEN)")
        plan = plan_reuse(_snapshot(SCENE), _snapshot(new_text), ALL)
        assert plan.blocks == {0, 1}

    def test_edits_outside_construct_reuse_nothing(self) -> None:
        for old, new in (
            ("HELPER_COLOR = BLUE", "HELPER_COLOR = RED"),
            ("self.k = 1", "self.k = 2"),
            ("run_time=0.5)\n\n\nclass", "run_time=0.6)\n\n\nclass"),
        ):
            plan = plan_reuse(_snapshot(SCENE), _snapshot(SCENE.replace(old, new)), ALL)
            assert plan.blocks == frozenset(), old
            assert plan.reason == "edit outside construct()"

    def test_two_edits_spanning_outside_construct_reuse_nothing(self) -> None:
        new_text = SCENE.replace("self.wait(1.0)", "self.wait(1.5)").replace(
            "self.k = 1", "self.k = 2"
        )
        plan = plan_reuse(_snapshot(SCENE), _snapshot(new_text), ALL)
        assert plan.blocks == frozenset()

    def test_changed_signature_reuses_nothing(self) -> None:
        plan = plan_reuse(_snapshot(SCENE), _snapshot(SCENE, signature=("1080p", 30)), ALL)
        assert plan.blocks == frozenset()
        assert "settings" in plan.reason

    def test_runtime_retime_of_an_iteration_stops_reuse_at_that_block(self) -> None:
        # Same text (per-iteration __studio__ edit), but block 3 now lasts longer.
        new = _snapshot(SCENE, durations=(1.0, 0.5, 0.5, 0.9, 1.0, 1.0))
        plan = plan_reuse(_snapshot(SCENE), new, ALL)
        assert plan.blocks == {0, 1, 2}

    def test_helper_blocks_are_not_reused(self) -> None:
        lines = {**LINES, "create": 7}  # the first block's call now lives in helper()
        old = _snapshot(SCENE, lines=lines)
        plan = plan_reuse(old, _snapshot(SCENE, lines=lines), ALL)
        assert 0 not in plan.blocks and {1, 2, 3, 4, 5} <= plan.blocks

    def test_inserting_lines_shifts_later_blocks_out_of_reuse(self) -> None:
        new_text = SCENE.replace(
            "        self.play(FadeOut(circle), run_time=1.0)",
            "        self.add(Dot())\n        self.play(FadeOut(circle), run_time=1.0)",
        )
        shifted = {**LINES, "fade": 22, "wait2": 23}
        plan = plan_reuse(_snapshot(SCENE), _snapshot(new_text, lines=shifted), ALL)
        assert plan.blocks == {0, 1, 2, 3}

    def test_zero_length_blocks_are_never_reused(self) -> None:
        old = _snapshot(SCENE, durations=(1.0, 0.0, 0.5, 0.5, 1.0, 1.0))
        plan = plan_reuse(old, _snapshot(SCENE, durations=(1.0, 0.0, 0.5, 0.5, 1.0, 1.0)), ALL)
        assert 1 not in plan.blocks
