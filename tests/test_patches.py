"""Unit tests for anchored patches and the text-splice engine."""

from __future__ import annotations

import ast

import pytest

from manim_studio.patch_runtime import StudioRuntime
from manim_studio.patches import (
    Anchor,
    Delete,
    PatchError,
    SetDuration,
    apply_patches,
    format_seconds,
    parse_patch,
    with_patch,
)
from manim_studio.site_index import index_source

SCENE = """\
class Demo(Scene):
    def construct(self):
        c = Circle()
        self.play(ShowCreation(c))  # intro
        self.wait(1)
        self.play(c.animate.shift(RIGHT), run_time=2)
        for i in range(3):
            self.play(FadeIn(c), run_time=0.5)
        self.wait()
"""


def site_ids() -> list[str]:
    return [site.site_id for site in index_source(SCENE).sites]


def apply(*patches: SetDuration | Delete, source: str = SCENE, runtime: bool = True):
    return apply_patches(source, patches, runtime=runtime)


class TestSetDuration:
    def test_replaces_an_existing_wait_literal(self) -> None:
        ids = site_ids()
        result = apply(SetDuration(Anchor(ids[1]), 2.5))
        assert "self.wait(2.5)" in result.text
        assert result.applied and not result.orphaned

    def test_replaces_an_existing_run_time_literal_only(self) -> None:
        ids = site_ids()
        result = apply(SetDuration(Anchor(ids[2]), 4))
        assert "self.play(c.animate.shift(RIGHT), run_time=4.0)" in result.text

    def test_appends_run_time_when_absent_and_keeps_comments(self) -> None:
        ids = site_ids()
        result = apply(SetDuration(Anchor(ids[0]), 1.5))
        assert "self.play(ShowCreation(c), run_time=1.5)  # intro" in result.text

    def test_adds_a_duration_to_a_bare_wait(self) -> None:
        ids = site_ids()
        result = apply(SetDuration(Anchor(ids[4]), 3))
        assert result.text.splitlines()[-1] == "        self.wait(3.0)"

    def test_wait_with_other_args_gets_a_keyword(self) -> None:
        src = "class S(Scene):\n    def construct(self):\n        self.wait(stop_condition=f)\n"
        (site,) = index_source(src).sites
        result = apply_patches(src, [SetDuration(Anchor(site.site_id), 2)])
        assert "self.wait(stop_condition=f, duration=2.0)" in result.text

    def test_play_without_args_gets_a_bare_keyword(self) -> None:
        src = "class S(Scene):\n    def construct(self):\n        self.play()\n"
        (site,) = index_source(src).sites
        result = apply_patches(src, [SetDuration(Anchor(site.site_id), 2)])
        assert "self.play(run_time=2.0)" in result.text

    def test_expression_durations_can_be_replaced_by_a_literal(self) -> None:
        src = "class S(Scene):\n    def construct(self):\n        self.wait(t * 2)\n"
        (site,) = index_source(src).sites
        result = apply_patches(src, [SetDuration(Anchor(site.site_id), 1)])
        assert "self.wait(1.0)" in result.text

    def test_multiline_call_appends_after_the_last_argument(self) -> None:
        src = (
            "class S(Scene):\n"
            "    def construct(self):\n"
            "        self.play(\n"
            "            FadeIn(a),\n"
            "            FadeIn(b),\n"
            "        )\n"
        )
        (site,) = index_source(src).sites
        result = apply_patches(src, [SetDuration(Anchor(site.site_id), 2)])
        assert ast.parse(result.text)
        assert "FadeIn(b), run_time=2.0," in result.text

    def test_kwargs_spread_without_duration_is_orphaned(self) -> None:
        src = "class S(Scene):\n    def construct(self):\n        self.play(A(), **opts)\n"
        (site,) = index_source(src).sites
        result = apply_patches(src, [SetDuration(Anchor(site.site_id), 2)])
        assert result.text == src
        assert "**kwargs" in result.orphaned[0].reason

    def test_every_result_still_parses_and_keeps_line_count(self) -> None:
        patches = [SetDuration(Anchor(sid), 1.25) for sid in site_ids()]
        result = apply(*patches)
        assert ast.parse(result.text)
        assert result.text.count("\n") == SCENE.count("\n")
        assert len(result.applied) == 5


class TestDelete:
    def test_replaces_the_statement_with_pass(self) -> None:
        ids = site_ids()
        result = apply(Delete(Anchor(ids[1])))
        assert "        pass\n" in result.text
        assert "self.wait(1)" not in result.text
        assert result.text.count("\n") == SCENE.count("\n")

    def test_keeps_a_trailing_comment(self) -> None:
        ids = site_ids()
        result = apply(Delete(Anchor(ids[0])))
        assert "        pass  # intro" in result.text

    def test_multiline_statement_keeps_the_line_count(self) -> None:
        src = (
            "class S(Scene):\n"
            "    def construct(self):\n"
            "        self.play(\n"
            "            FadeIn(a),\n"
            "        )  # end\n"
            "        self.wait(1)\n"
        )
        (play, _wait) = index_source(src).sites
        result = apply_patches(src, [Delete(Anchor(play.site_id))])
        assert ast.parse(result.text)
        assert result.text.count("\n") == src.count("\n")
        # The wait stays on the same line it started on.
        assert result.text.splitlines()[5] == "        self.wait(1)"

    def test_delete_supersedes_a_duration_edit_of_the_same_site(self) -> None:
        ids = site_ids()
        result = apply(SetDuration(Anchor(ids[1]), 5), Delete(Anchor(ids[1])))
        assert "self.wait" not in result.text.splitlines()[4]
        assert len(result.applied) == 1 and not result.orphaned

    def test_not_deletable_when_sharing_a_line(self) -> None:
        src = "class S(Scene):\n    def construct(self):\n        self.wait(2); z = 2\n"
        (site,) = index_source(src).sites
        result = apply_patches(src, [Delete(Anchor(site.site_id))])
        assert result.text == src
        assert "cannot be deleted" in result.orphaned[0].reason

    def test_single_iteration_delete_is_not_supported(self) -> None:
        ids = site_ids()
        result = apply(Delete(Anchor(ids[3], occurrence=1)))
        assert result.text == SCENE
        assert result.orphaned


class TestOrphans:
    def test_unknown_site_is_orphaned_not_guessed(self) -> None:
        result = apply(SetDuration(Anchor("construct:wait:deadbeef00:0"), 2))
        assert result.text == SCENE
        assert not result.applied
        assert "no longer exists" in result.orphaned[0].reason

    def test_orphaning_one_patch_does_not_block_the_others(self) -> None:
        ids = site_ids()
        result = apply(
            SetDuration(Anchor("gone:wait:0000000000:0"), 9),
            SetDuration(Anchor(ids[1]), 2),
        )
        assert "self.wait(2.0)" in result.text
        assert len(result.applied) == 1 and len(result.orphaned) == 1

    def test_patches_follow_their_site_when_code_is_added_above(self) -> None:
        ids = site_ids()
        shifted = SCENE.replace("c = Circle()", "c = Circle()\n        d = Square()\n        e = 1")
        result = apply(SetDuration(Anchor(ids[1]), 2), source=shifted)
        assert "self.wait(2.0)" in result.text
        assert not result.orphaned


class TestPerIteration:
    def test_compiles_to_a_runtime_helper_call(self) -> None:
        ids = site_ids()
        result = apply(SetDuration(Anchor(ids[3], occurrence=1), 2))
        assert "run_time=__studio__.dur(" in result.text
        assert "{1: 2.0}, 0.5)" in result.text
        assert ast.parse(result.text)

    def test_helper_resolves_the_right_iteration_at_runtime(self) -> None:
        ids = site_ids()
        patches = [
            SetDuration(Anchor(ids[3], occurrence=1), 2),
            SetDuration(Anchor(ids[3], occurrence=2), 3),
        ]
        result = apply_patches(SCENE, patches)
        seen: list[float] = []

        class Stub:
            """Stands in for every mobject/animation the scene touches."""

            def __init__(self, *_args: object) -> None:
                self.animate = self

            def shift(self, *_args: object) -> Stub:
                return self

        class Recorder:
            def play(self, *_args: object, run_time: float = 1.0) -> None:
                seen.append(run_time)

            def wait(self, *_args: object, **_kwargs: object) -> None:
                return None

        namespace: dict[str, object] = {
            "Scene": Recorder,
            "Circle": Stub,
            "ShowCreation": Stub,
            "FadeIn": Stub,
            "RIGHT": 0,
            "__studio__": StudioRuntime(),
        }
        exec(compile(result.text, "<patched>", "exec"), namespace)
        namespace["Demo"]().construct()  # type: ignore[operator]
        # intro (default), shift (2.0 literal), then the three loop iterations
        assert seen == [1.0, 2.0, 0.5, 2.0, 3.0]

    def test_whole_site_value_becomes_the_default(self) -> None:
        ids = site_ids()
        result = apply(
            SetDuration(Anchor(ids[3]), 1),
            SetDuration(Anchor(ids[3], occurrence=0), 4),
        )
        assert "{0: 4.0}, 1.0)" in result.text

    def test_absent_duration_needs_a_whole_site_value_first(self) -> None:
        src = (
            "class S(Scene):\n"
            "    def construct(self):\n"
            "        for i in range(2):\n"
            "            self.play(FadeIn(a))\n"
        )
        (site,) = index_source(src).sites
        result = apply_patches(src, [SetDuration(Anchor(site.site_id, 0), 2)])
        assert result.text == src
        assert "all iterations first" in result.orphaned[0].reason
        both = apply_patches(
            src,
            [SetDuration(Anchor(site.site_id), 1), SetDuration(Anchor(site.site_id, 0), 2)],
        )
        assert "run_time=__studio__.dur(" in both.text and not both.orphaned

    def test_unbakeable_when_the_helper_is_not_allowed(self) -> None:
        ids = site_ids()
        result = apply(SetDuration(Anchor(ids[3], occurrence=1), 2), runtime=False)
        assert result.text == SCENE
        assert "per-iteration" in result.orphaned[0].reason


class TestRuntime:
    def test_counts_and_resets(self) -> None:
        runtime = StudioRuntime()
        assert runtime.dur("a", {1: 9.0}, 1.0) == 1.0
        assert runtime.dur("a", {1: 9.0}, 1.0) == 9.0
        assert runtime.count("a") == 2
        runtime.reset()
        assert runtime.count("a") == 0
        assert runtime.dur("a", {1: 9.0}, 1.0) == 1.0


class TestSerialization:
    def test_round_trip(self) -> None:
        for patch in (
            SetDuration(Anchor("f:play:abc:0"), 2.5),
            SetDuration(Anchor("f:play:abc:0", 3), 0.0),
            Delete(Anchor("f:wait:abc:1")),
        ):
            assert parse_patch(patch.to_dict()) == patch

    @pytest.mark.parametrize(
        "data",
        [
            "nope",
            {"op": "set_duration", "site": "a"},
            {"op": "set_duration", "site": "a", "seconds": "2"},
            {"op": "bogus", "site": "a"},
            {"op": "delete"},
            {"op": "delete", "site": "a", "occurrence": -1},
            {"op": "delete", "site": "a", "occurrence": True},
        ],
    )
    def test_rejects_malformed_payloads(self, data: object) -> None:
        with pytest.raises(PatchError):
            parse_patch(data)

    @pytest.mark.parametrize("seconds", [-1.0, float("nan"), float("inf")])
    def test_rejects_bad_durations(self, seconds: float) -> None:
        with pytest.raises(PatchError):
            SetDuration(Anchor("a"), seconds)


class TestWithPatch:
    def test_later_duration_replaces_the_earlier_one(self) -> None:
        a = SetDuration(Anchor("s"), 1)
        b = SetDuration(Anchor("s"), 2)
        assert with_patch([a], b) == (b,)

    def test_different_iterations_coexist(self) -> None:
        a = SetDuration(Anchor("s", 0), 1)
        b = SetDuration(Anchor("s", 1), 2)
        assert with_patch([a], b) == (a, b)

    def test_delete_supersedes_the_sites_durations_but_not_other_sites(self) -> None:
        keep = SetDuration(Anchor("other"), 1)
        old = SetDuration(Anchor("s"), 1)
        gone = Delete(Anchor("s"))
        assert with_patch([keep, old], gone) == (keep, gone)


def test_format_seconds() -> None:
    assert format_seconds(1) == "1.0"
    assert format_seconds(0.25) == "0.25"
    assert format_seconds(2.0004) == "2.0"
