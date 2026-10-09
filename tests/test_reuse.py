"""plan_reuse: which cached blocks survive an edit (pure logic, no GL)."""

from __future__ import annotations

from manim_studio.reuse import ConstructMap, RunSnapshot, plan_reuse, source_is_time_dependent
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


class TestTimingOnlyReuse:
    """A retime moves later blocks along the timeline without changing their pixels."""

    @staticmethod
    def _independent(snapshot: RunSnapshot) -> RunSnapshot:
        import dataclasses

        return dataclasses.replace(snapshot, time_independent=True)

    def _plan(self, durations: tuple[float, ...], **kwargs):  # type: ignore[no-untyped-def]
        old = self._independent(_snapshot(SCENE))
        new = self._independent(_snapshot(SCENE, durations=durations))
        return plan_reuse(old, new, ALL, timing_only=True, **kwargs)

    def test_later_blocks_are_reused_and_shifted_by_the_whole_frame_delta(self) -> None:
        # wait 0.5 s -> 1.0 s pushes every later block 15 frames (0.5 s at 30 fps) along.
        plan = self._plan((1.0, 1.0, 0.5, 0.5, 1.0, 1.0))
        assert plan.blocks == {0, 2, 3, 4, 5}  # the retimed block 1 is re-rendered
        assert plan.reason == "timing-only edit"
        assert plan.shift_of(0) == 0
        assert [plan.shift_of(i) for i in (2, 3, 4, 5)] == [15, 15, 15, 15]

    def test_without_the_flag_a_retime_still_keeps_only_the_prefix(self) -> None:
        old = self._independent(_snapshot(SCENE))
        new = self._independent(_snapshot(SCENE, durations=(1.0, 1.0, 0.5, 0.5, 1.0, 1.0)))
        assert plan_reuse(old, new, ALL).blocks == {0}

    def test_time_dependent_scenes_fall_back_to_the_prefix_rule(self) -> None:
        old = _snapshot(SCENE)  # time_independent defaults to False: be safe
        new = _snapshot(SCENE, durations=(1.0, 1.0, 0.5, 0.5, 1.0, 1.0))
        plan = plan_reuse(old, new, ALL, timing_only=True)
        assert plan.blocks == {0} and plan.shifts == ()

    def test_a_structural_change_is_not_a_timing_edit(self) -> None:
        old = self._independent(_snapshot(SCENE))
        moved = {**LINES, "fade": 22, "wait2": 23}
        new = self._independent(
            _snapshot(SCENE, lines=moved, durations=(1.0, 1.0, 0.5, 0.5, 1.0, 1.0))
        )
        plan = plan_reuse(old, new, ALL, timing_only=True)
        assert plan.shifts == () and plan.reason != "timing-only edit"

    def test_a_different_block_count_is_not_a_timing_edit(self) -> None:
        import dataclasses

        old = self._independent(_snapshot(SCENE))
        new = dataclasses.replace(
            old, blocks=old.blocks[:-1], call_lines=old.call_lines[:-1], sites=old.sites[:-1]
        )
        assert plan_reuse(old, new, ALL, timing_only=True).reason != "timing-only edit"

    def test_only_cached_blocks_are_reused(self) -> None:
        old = self._independent(_snapshot(SCENE))
        new = self._independent(_snapshot(SCENE, durations=(1.0, 1.0, 0.5, 0.5, 1.0, 1.0)))
        plan = plan_reuse(old, new, frozenset({2, 5}), timing_only=True)
        assert plan.blocks == {2, 5}

    def test_a_block_whose_frame_count_changes_with_the_shift_is_dropped(self) -> None:
        # 0.15 s blocks are 4.5 frames long, so a 0.05 s (1.5 frame) shift changes how many
        # frames some blocks round to.  Those must be re-rendered, never shown with a hole.
        old = self._independent(_snapshot(SCENE, durations=(0.15,) * 6))
        new = self._independent(_snapshot(SCENE, durations=(0.15, 0.2, 0.15, 0.15, 0.15, 0.15)))
        plan = plan_reuse(old, new, ALL, timing_only=True)
        assert 1 not in plan.blocks  # retimed
        assert 0 in plan.blocks  # before the edit
        assert plan.blocks < ALL - {1}  # and at least one later block lost to rounding
        for index in plan.blocks:
            a, b = old.blocks[index], new.blocks[index]
            old_len = max(round(a.end * 30), round(a.start * 30) + 1) - round(a.start * 30)
            new_len = max(round(b.end * 30), round(b.start * 30) + 1) - round(b.start * 30)
            assert old_len == new_len
            assert plan.shift_of(index) == round(b.start * 30) - round(a.start * 30)

    def test_zero_length_blocks_are_never_reused(self) -> None:
        old = self._independent(_snapshot(SCENE, durations=(1.0, 0.0, 0.5, 0.5, 1.0, 1.0)))
        new = self._independent(_snapshot(SCENE, durations=(1.0, 0.0, 0.5, 0.5, 1.0, 2.0)))
        plan = plan_reuse(old, new, ALL, timing_only=True)
        assert 1 not in plan.blocks and 5 not in plan.blocks

    def test_changed_render_settings_still_win(self) -> None:
        old = self._independent(_snapshot(SCENE))
        new = self._independent(_snapshot(SCENE, signature=("1080p", 30)))
        assert plan_reuse(old, new, ALL, timing_only=True).blocks == frozenset()


class TestTimeDependence:
    def test_plain_scenes_are_time_independent(self) -> None:
        assert source_is_time_dependent(SCENE) is False

    def test_updaters_and_the_scene_clock_are_time_dependent(self) -> None:
        for fragment in (
            "dot.add_updater(lambda m, dt: m.shift(dt * RIGHT))",
            "always_redraw(lambda: Dot())",
            "x = self.time",
            "f_always(m.set_x, lambda: 1)",
            "m.clear_updaters()",
        ):
            assert source_is_time_dependent(SCENE + "        " + fragment + "\n"), fragment

    def test_words_that_merely_contain_the_tokens_do_not_count(self) -> None:
        assert source_is_time_dependent("self.runtime = 1\nhandle = 2\n") is False


class TestRuntimeEvidenceAndSourceEdits:
    """Typing a duration into the file, in scenes that use updaters (judged at runtime)."""

    OLD = (1.0, 0.5, 0.5, 0.5, 1.0, 1.0)
    NEW = (1.0, 1.0, 0.5, 0.5, 1.0, 1.0)  # the first wait: 0.5 s -> 1.0 s
    RETIMED = SCENE.replace("self.wait(0.5)", "self.wait(1.0)")

    @staticmethod
    def _run(
        text: str,
        durations: tuple[float, ...],
        *,
        dynamic: tuple[bool, ...] | None = (False,) * 6,
        no_user_modules: bool = True,
    ) -> RunSnapshot:
        import dataclasses

        return dataclasses.replace(
            _snapshot(text, durations=durations), dynamic=dynamic, no_user_modules=no_user_modules
        )

    def test_a_retime_typed_into_the_source_is_detected_from_the_text(self) -> None:
        plan = plan_reuse(self._run(SCENE, self.OLD), self._run(self.RETIMED, self.NEW), ALL)
        assert plan.blocks == {0, 2, 3, 4, 5}  # only the edited wait is re-rendered
        assert plan.reason == "timing-only edit"
        assert [plan.shift_of(i) for i in (0, 2, 3, 4, 5)] == [0, 15, 15, 15, 15]

    def test_a_play_run_time_edit_is_a_timing_edit_too(self) -> None:
        new_text = SCENE.replace(
            "ShowCreation(circle), run_time=1.0", "ShowCreation(circle), run_time=1.5"
        )
        new = self._run(new_text, (1.5, 0.5, 0.5, 0.5, 1.0, 1.0))
        plan = plan_reuse(self._run(SCENE, self.OLD), new, ALL)
        assert plan.blocks == {1, 2, 3, 4, 5}
        assert plan.shift_of(1) == 15

    def test_a_retimed_block_with_a_dt_updater_keeps_only_the_blocks_before_it(self) -> None:
        dynamic = (False, True, True, True, True, True)  # a dt updater alive from block 1 on
        plan = plan_reuse(
            self._run(SCENE, self.OLD, dynamic=dynamic),
            self._run(self.RETIMED, self.NEW, dynamic=dynamic),
            ALL,
        )
        assert plan.blocks == {0}
        assert "time-based updater" in plan.reason

    def test_an_updater_that_starts_after_the_retimed_block_does_not_matter(self) -> None:
        dynamic = (False, False, True, True, True, True)  # block 1 (retimed) ran without one
        plan = plan_reuse(
            self._run(SCENE, self.OLD, dynamic=dynamic),
            self._run(self.RETIMED, self.NEW, dynamic=dynamic),
            ALL,
        )
        assert plan.blocks == {0, 2, 3, 4, 5}

    def test_unknown_runtime_evidence_falls_back_to_the_prefix_rule(self) -> None:
        plan = plan_reuse(
            self._run(SCENE, self.OLD, dynamic=None),
            self._run(self.RETIMED, self.NEW, dynamic=None),
            ALL,
        )
        assert plan.blocks == {0} and plan.shifts == ()

    def test_scenes_with_user_modules_fall_back_to_the_prefix_rule(self) -> None:
        plan = plan_reuse(
            self._run(SCENE, self.OLD, no_user_modules=False),
            self._run(self.RETIMED, self.NEW, no_user_modules=False),
            ALL,
        )
        assert plan.blocks == {0}

    def test_a_scene_that_reads_the_clock_falls_back_to_the_prefix_rule(self) -> None:
        clock = "        now = self.time\n"
        old = self._run(SCENE + clock, self.OLD)
        new = self._run(self.RETIMED + clock, self.NEW)
        assert plan_reuse(old, new, ALL).blocks == {0}

    def test_a_code_edit_mixed_with_a_timing_edit_is_not_timing_only(self) -> None:
        mixed = self.RETIMED.replace("Circle()", "Circle(radius=2)")
        plan = plan_reuse(self._run(SCENE, self.OLD), self._run(mixed, self.NEW), ALL)
        assert plan.shifts == () and plan.reason != "timing-only edit"

    def test_a_run_time_argument_outside_play_is_not_a_duration(self) -> None:
        old_text = SCENE + "        helper(run_time=3)\n"
        new_text = SCENE + "        helper(run_time=4)\n"
        plan = plan_reuse(self._run(old_text, self.OLD), self._run(new_text, self.OLD), ALL)
        assert plan.reason != "timing-only edit"

    def test_a_comment_only_edit_reuses_every_block(self) -> None:
        commented = SCENE.replace("circle = Circle()", "circle = Circle()  # round")
        plan = plan_reuse(self._run(SCENE, self.OLD), self._run(commented, self.OLD), ALL)
        assert plan.blocks == ALL and plan.shifts == ()
