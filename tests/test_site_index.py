"""Unit tests for the static play/wait call-site index."""

from __future__ import annotations

import pytest

from manim_studio.site_index import index_source

SCENE = """\
from manimlib import *


class Demo(Scene):
    def construct(self):
        c = Circle()
        self.play(ShowCreation(c))
        self.wait(1)
        self.play(c.animate.shift(RIGHT), run_time=2)
        for i in range(3):
            self.play(FadeIn(c), run_time=0.5)
        self.wait()
"""


def test_finds_every_play_and_wait_in_source_order() -> None:
    index = index_source(SCENE)
    assert [(s.kind, s.line) for s in index.sites] == [
        ("play", 7),
        ("wait", 8),
        ("play", 9),
        ("play", 11),
        ("wait", 12),
    ]


def test_duration_kinds() -> None:
    sites = index_source(SCENE).sites
    assert sites[0].duration.kind == "absent"
    assert (sites[1].duration.kind, sites[1].duration.value) == ("literal", 1.0)
    assert (sites[2].duration.kind, sites[2].duration.value) == ("literal", 2.0)
    assert (sites[3].duration.kind, sites[3].duration.value) == ("literal", 0.5)
    assert sites[4].duration.kind == "absent"


def test_non_literal_duration_is_an_expression() -> None:
    src = "class S(Scene):\n    def construct(self):\n        self.wait(t * 2)\n"
    (site,) = index_source(src).sites
    assert site.duration.kind == "expr"
    assert site.duration.editable is False


def test_negative_literal_is_still_a_literal() -> None:
    src = "class S(Scene):\n    def construct(self):\n        self.wait(-1)\n"
    (site,) = index_source(src).sites
    assert (site.duration.kind, site.duration.value) == ("literal", -1.0)


def test_loop_flag() -> None:
    sites = index_source(SCENE).sites
    assert [s.in_loop for s in sites] == [False, False, False, True, False]


def test_fingerprint_survives_retiming_and_unrelated_edits() -> None:
    before = index_source(SCENE)
    edited = SCENE.replace("run_time=2", "run_time=7").replace(
        "        self.wait()\n", "        self.wait()\n        x = 1\n"
    )
    edited = edited.replace("c = Circle()", "c = Circle()\n        d = Square()")
    after = index_source(edited)
    assert before.ids() == after.ids()


def test_fingerprint_changes_when_the_animation_changes() -> None:
    before = index_source(SCENE)
    after = index_source(SCENE.replace("ShowCreation(c)", "FadeIn(c)"))
    assert before.sites[0].site_id != after.sites[0].site_id
    assert before.sites[1].site_id == after.sites[1].site_id


def test_identical_calls_get_distinct_ordinals() -> None:
    src = "class S(Scene):\n    def construct(self):\n        self.wait(1)\n        self.wait(2)\n"
    a, b = index_source(src).sites
    assert a.site_id != b.site_id
    # Same call text apart from the duration, so only the ordinal differs.
    assert a.site_id.rsplit(":", 1)[0] == b.site_id.rsplit(":", 1)[0]


def test_function_scope_is_part_of_the_identity() -> None:
    src = (
        "class S(Scene):\n"
        "    def construct(self):\n"
        "        self.wait(1)\n"
        "    def other(self):\n"
        "        self.wait(1)\n"
    )
    a, b = index_source(src).sites
    assert a.function == "S.construct"
    assert b.function == "S.other"
    assert a.site_id != b.site_id


def test_statement_and_deletable() -> None:
    src = (
        "class S(Scene):\n"
        "    def construct(self):\n"
        "        self.wait(1)\n"
        "        x = self.play(A()); y = 1\n"
        "        self.wait(2); z = 2\n"
    )
    first, second, third = index_source(src).sites
    assert first.statement is not None and first.deletable is True
    assert second.statement is None and second.deletable is False
    assert third.statement is not None and third.deletable is False


def test_spans_are_character_based_with_non_ascii_source() -> None:
    src = 'class S(Scene):\n    def construct(self):\n        t = "héllo"; self.wait(1)\n'
    (site,) = index_source(src).sites
    line = src.splitlines()[2]
    assert line[site.call.col : site.call.end_col] == "self.wait(1)"
    assert site.duration.span is not None
    assert line[site.duration.span.col : site.duration.span.end_col] == "1"


def test_multiline_call_span_and_label() -> None:
    src = (
        "class S(Scene):\n"
        "    def construct(self):\n"
        "        self.play(\n"
        "            FadeIn(a),\n"
        "            run_time=2,\n"
        "        )\n"
    )
    (site,) = index_source(src).sites
    assert (site.call.line, site.call.end_line) == (3, 6)
    assert site.label == "self.play( FadeIn(a), run_time=2, )"
    assert site.deletable is True


def test_lookup_helpers() -> None:
    index = index_source(SCENE)
    second = index.sites[1]
    assert index.at(*second.lookup_key) is second
    assert index.at_line(8) is second
    assert index.at_line(1) is None
    assert index.by_id(second.site_id) is second
    assert index.by_id("nope") is None


def test_kwargs_spread_is_flagged_unsafe() -> None:
    src = "class S(Scene):\n    def construct(self):\n        self.play(A(), **opts)\n"
    (site,) = index_source(src).sites
    assert site.unsafe_kwargs is True


def test_syntax_error_propagates() -> None:
    with pytest.raises(SyntaxError):
        index_source("def broken(:\n")


def test_ignores_other_receivers_and_methods() -> None:
    src = (
        "class S(Scene):\n"
        "    def construct(self):\n"
        "        other.play(A())\n"
        "        self.add(a)\n"
        "        self.wait_until(x)\n"
    )
    assert index_source(src).sites == ()
