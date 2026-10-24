"""Call-argument edits for mobject targets (ADR 0012): minimal splices that keep source valid."""

from __future__ import annotations

import pytest

from manim_studio.call_edits import (
    AddCall,
    EditError,
    RemoveArgument,
    SetArgument,
    apply_changes,
    format_colour,
    format_value,
)
from manim_studio.mobject_sites import find_mobject_targets


def _edit(source: str, var: str, *changes: object) -> str:
    target = next(t for t in find_mobject_targets(source) if t.var == var)
    return apply_changes(source, target, list(changes))


def test_replaces_an_existing_keyword_value() -> None:
    source = "circle = Circle(radius=1.5)\n"
    out = _edit(source, "circle", SetArgument(0, "2.0", keyword="radius"))
    assert out == "circle = Circle(radius=2.0)\n"


def test_appends_an_absent_keyword_after_the_last_argument() -> None:
    assert _edit("c = Circle()\n", "c", SetArgument(0, "3", keyword="stroke_width")) == (
        "c = Circle(stroke_width=3)\n"
    )
    assert _edit("c = Circle(radius=1)\n", "c", SetArgument(0, "3", keyword="stroke_width")) == (
        "c = Circle(radius=1, stroke_width=3)\n"
    )


def test_appending_keeps_a_trailing_comma_valid() -> None:
    source = "c = Circle(\n    radius=1,\n)\n"
    out = _edit(source, "c", SetArgument(0, "RED", keyword="stroke_color"))
    assert out == "c = Circle(\n    radius=1, stroke_color=RED,\n)\n"


def test_comments_survive_an_edit() -> None:
    source = "c = Circle(  # the outline\n    radius=1,  # keep this\n)\n"
    out = _edit(source, "c", SetArgument(0, "2", keyword="radius"))
    assert "# the outline" in out
    assert "# keep this" in out
    assert "radius=2," in out


def test_replaces_a_positional_argument_and_appends_one_after_the_last() -> None:
    source = 'title = Text("a")\n'
    assert _edit(source, "title", SetArgument(0, '"b"', position=0)) == 'title = Text("b")\n'
    assert _edit(source, "title", SetArgument(0, '"b"', position=1)) == ('title = Text("a", "b")\n')


def test_positional_argument_goes_before_existing_keywords() -> None:
    source = "t = Text(font_size=64)\n"
    out = _edit(source, "t", SetArgument(0, '"hi"', position=0))
    assert out == 't = Text("hi", font_size=64)\n'


def test_removes_a_keyword_from_the_middle_first_and_last() -> None:
    source = "c = Circle(radius=1, stroke_width=2, fill_opacity=0.5)\n"
    assert _edit(source, "c", RemoveArgument(0, keyword="stroke_width")) == (
        "c = Circle(radius=1, fill_opacity=0.5)\n"
    )
    assert _edit(source, "c", RemoveArgument(0, keyword="radius")) == (
        "c = Circle(stroke_width=2, fill_opacity=0.5)\n"
    )
    assert _edit(source, "c", RemoveArgument(0, keyword="fill_opacity")) == (
        "c = Circle(radius=1, stroke_width=2)\n"
    )


def test_removing_an_absent_argument_changes_nothing() -> None:
    source = "c = Circle(radius=1)\n"
    assert _edit(source, "c", RemoveArgument(0, keyword="stroke_width")) == source


def test_removing_the_only_argument_leaves_an_empty_call() -> None:
    assert _edit("c = Circle(radius=1)\n", "c", RemoveArgument(0, keyword="radius")) == (
        "c = Circle()\n"
    )


def test_adds_a_chained_call_to_the_assignment() -> None:
    source = "title = Text('x')\ntitle.shift(UP)\n"
    out = _edit(source, "title", AddCall("move_to", ("[1, 0, 0]",)))
    assert out == "title = Text('x').move_to([1, 0, 0])\ntitle.shift(UP)\n"


def test_setter_index_is_addressed_on_the_original_target() -> None:
    source = "dot = Dot().set_fill(RED, 1)\ndot.shift(UP)\n"
    out = _edit(
        source,
        "dot",
        SetArgument(1, "BLUE", position=0),
        AddCall("scale", ("2",)),
    )
    assert out == "dot = Dot().set_fill(BLUE, 1).scale(2)\ndot.shift(UP)\n"


def test_gradient_colours_can_be_replaced_one_by_one_and_extended() -> None:
    source = "t.set_color_by_gradient(BLUE, TEAL)\n"
    source = "t = Text('x')\n" + source
    out = _edit(
        source,
        "t",
        SetArgument(1, "GREEN", position=0),
        SetArgument(1, "YELLOW", position=2),
    )
    assert out.endswith("t.set_color_by_gradient(GREEN, TEAL, YELLOW)\n")


def test_removes_one_colour_from_a_gradient() -> None:
    source = "t = Text('x')\nt.set_color_by_gradient(BLUE, TEAL, GREEN)\n"
    out = _edit(source, "t", RemoveArgument(1, position=1))
    assert out.endswith("t.set_color_by_gradient(BLUE, GREEN)\n")


def test_unicode_before_the_target_does_not_shift_the_edit() -> None:
    source = "# ünïcode 🙂\nc = Circle(radius=1)\n"
    out = _edit(source, "c", SetArgument(0, "4", keyword="radius"))
    assert out == "# ünïcode 🙂\nc = Circle(radius=4)\n"


def test_no_changes_returns_the_same_source() -> None:
    source = "c = Circle(radius=1)\n"
    assert apply_changes(source, next(iter(find_mobject_targets(source))), []) == source


def test_a_value_that_is_not_an_expression_is_refused() -> None:
    source = "c = Circle()\n"
    for bad in ("1 +", "a = 2", "x\ny", ""):
        with pytest.raises(EditError):
            _edit(source, "c", SetArgument(0, bad, keyword="radius"))


def test_an_invalid_keyword_name_is_refused() -> None:
    with pytest.raises(EditError):
        _edit("c = Circle()\n", "c", SetArgument(0, "1", keyword="not a name"))


def test_a_target_that_no_longer_exists_is_refused() -> None:
    source = "c = Circle()\n"
    target = next(iter(find_mobject_targets(source)))
    with pytest.raises(EditError):
        apply_changes("d = Circle()\n", target, [SetArgument(0, "1", keyword="radius")])


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, "None"),
        (True, "True"),
        (3, "3"),
        (1.5, "1.5"),
        ("hi", '"hi"'),
        ([1, "a"], '[1, "a"]'),
    ],
)
def test_format_value(value: object, expected: str) -> None:
    assert format_value(value) == expected


def test_format_value_refuses_non_finite_floats_and_objects() -> None:
    with pytest.raises(EditError):
        format_value(float("nan"))
    with pytest.raises(EditError):
        format_value(object())


def test_format_colour_is_always_hex_and_keeps_the_quote_style() -> None:
    # A manim constant name is never written, even when the hex matches one.
    assert format_colour("#58C4DD", '"#000000"') == '"#58C4DD"'
    assert format_colour("#58c4dd", "'#000000'") == "'#58C4DD'"
    assert format_colour("#123456", "'#000000'") == "'#123456'"
