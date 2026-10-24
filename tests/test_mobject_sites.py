"""Mobject targets in scene source (ADR 0012): bindings, chained setters and byte spans."""

from __future__ import annotations

import pytest

from manim_studio.mobject_sites import find_mobject_targets

SCENE = """from manimlib import *


class Intro(Scene):
    def construct(self):
        # café: a comment before the first target keeps the byte spans honest
        title = Text("Hello", font_size=64)
        title.set_color_by_gradient(BLUE, TEAL)
        circle = Circle(radius=1.5, stroke_color="#B3D4FF")
        circle.move_to([0, 0, 0])
        self.play(circle.animate.shift(UP))
        dot = Dot().set_fill(RED, 1)
        helper = compute_things()
"""


def _by_var(source: str) -> dict[str, object]:
    return {target.var: target for target in find_mobject_targets(source)}


def test_binding_and_later_setters_form_one_target() -> None:
    targets = _by_var(SCENE)
    title = targets["title"]
    assert title.identity == "Intro.construct:title#0"
    assert title.cls == "Text"
    assert [call.method for call in title.calls] == ["Text", "set_color_by_gradient"]
    assert title.calls[0].constructor is True
    assert title.calls[1].constructor is False
    assert title.lines == (7, 8)


def test_chained_calls_on_the_assignment_are_part_of_the_target() -> None:
    dot = _by_var(SCENE)["dot"]
    assert [call.method for call in dot.calls] == ["Dot", "set_fill"]
    assert dot.value_end > dot.calls[0].start


def test_play_and_non_constructor_assignments_are_not_targets() -> None:
    names = set(_by_var(SCENE))
    assert "helper" not in names  # ``compute_things()`` is not a constructor
    assert names == {"title", "circle", "dot"}


def test_keyword_and_argument_spans_are_utf8_byte_offsets() -> None:
    source = SCENE.replace("café", "café ünïcode 🙂")
    target = _by_var(source)["title"]
    data = source.encode("utf-8")
    text_arg = target.calls[0].args[0]
    assert data[text_arg.start : text_arg.end].decode("utf-8") == '"Hello"'
    font_size = target.calls[0].keywords[0]
    assert data[font_size.start : font_size.end].decode("utf-8") == "font_size=64"
    assert font_size.value == 64
    assert font_size.literal is True


def test_non_literal_values_are_reported_as_text() -> None:
    circle = _by_var(SCENE)["circle"]
    radius = next(k for k in circle.calls[0].keywords if k.name == "radius")
    assert radius.literal is True and radius.value == 1.5
    dot = _by_var(SCENE)["dot"]
    fill_arg = dot.calls[1].args[0]
    assert fill_arg.text == "RED"
    assert fill_arg.literal is False


def test_rebinding_gives_a_new_ordinal() -> None:
    source = "a = Circle()\na.shift(UP)\na = Square()\na.shift(DOWN)\n"
    first, second = find_mobject_targets(source)
    assert (first.var, first.ordinal, first.cls) == ("a", 0, "Circle")
    assert (second.var, second.ordinal, second.cls) == ("a", 1, "Square")
    assert [c.method for c in first.calls] == ["Circle", "shift"]
    assert [c.method for c in second.calls] == ["Square", "shift"]


def test_setters_inside_a_nested_block_belong_to_their_own_block() -> None:
    source = "dot = Dot()\nif True:\n    dot.shift(UP)\n"
    (target,) = find_mobject_targets(source)
    assert [c.method for c in target.calls] == ["Dot"]


def test_scope_names_separate_functions() -> None:
    source = (
        "class A(Scene):\n"
        "    def construct(self):\n"
        "        c = Circle()\n"
        "def other():\n"
        "    c = Circle()\n"
    )
    scopes = [target.identity for target in find_mobject_targets(source)]
    assert scopes == ["A.construct:c#0", "other:c#0"]


def test_syntax_errors_propagate() -> None:
    with pytest.raises(SyntaxError):
        find_mobject_targets("def broken(:\n")
