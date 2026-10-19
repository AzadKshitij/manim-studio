"""Colour literal discovery, spelling and timeline identity (pure, no Qt)."""

from __future__ import annotations

import ast

import pytest

from manim_studio.color_sites import (
    MANIM_COLORS,
    find_color_sites,
    literal_for,
    neutralise_colors,
)
from manim_studio.site_index import index_source


def _bytes_of(source: str, site_start: int, site_end: int) -> str:
    return source.encode("utf-8")[site_start:site_end].decode("utf-8")


def test_finds_keyword_and_setter_literals_in_source_order() -> None:
    source = (
        "from manimlib import *\n"
        "circle = Circle(radius=1.5, color=BLUE)\n"
        'square = Square(fill_color="#FF6B6B")\n'
        "circle.set_color(GREEN)\n"
    )
    sites = find_color_sites(source)
    assert [(s.line, s.text, s.hex) for s in sites] == [
        (2, "BLUE", "#58C4DD"),
        (3, '"#FF6B6B"', "#FF6B6B"),
        (4, "GREEN", "#83C167"),
    ]


def test_spans_are_utf8_byte_offsets_even_after_non_ascii_text() -> None:
    source = 'label = Text("héllo wörld ✓", color=YELLOW)\n'
    (site,) = find_color_sites(source)
    assert _bytes_of(source, site.start, site.end) == "YELLOW"


def test_finds_literals_nested_inside_play_calls() -> None:
    source = (
        "class Demo(Scene):\n"
        "    def construct(self):\n"
        "        self.play(ShowCreation(circle, color=RED_C), run_time=2)\n"
    )
    (site,) = find_color_sites(source)
    assert (site.line, site.text, site.hex) == (3, "RED_C", "#FC6255")


def test_gradient_setter_reports_every_colour_argument() -> None:
    sites = find_color_sites("title.set_color_by_gradient(BLUE, TEAL, GREEN)\n")
    assert [s.text for s in sites] == ["BLUE", "TEAL", "GREEN"]


def test_computed_colours_are_not_reported() -> None:
    source = (
        "Circle(color=my_colour)\n"
        "Dot(color=interpolate_color(BLUE, RED, 0.5))\n"
        "Square(color='not-a-hex')\n"
        'Line(color="#12345")\n'
    )
    sites = find_color_sites(source)
    # ``BLUE`` and ``RED`` sit inside interpolate_color(), not in a colour keyword.
    assert sites == ()


def test_broken_source_raises_syntax_error() -> None:
    with pytest.raises(SyntaxError):
        find_color_sites("Circle(color=BLUE\n")


@pytest.mark.parametrize(
    ("hex_value", "original", "expected"),
    [
        ("#58C4DD", "BLUE", "BLUE"),  # a manim constant wins over a hex string
        ("#58c4dd", "BLUE", "BLUE"),  # case-insensitive
        ("#58C4DD", "BLUE_C", "BLUE"),  # plain name wins over the _C alias
        ("#123456", "BLUE", '"#123456"'),
        ("#123456", '"#FF6B6B"', '"#123456"'),  # keeps the original quote style
        ("#123456", "'#FF6B6B'", "'#123456'"),
    ],
)
def test_literal_for_prefers_manim_names_and_keeps_quotes(
    hex_value: str, original: str, expected: str
) -> None:
    assert literal_for(hex_value, original) == expected


def test_palette_table_resolves_every_constant_it_lists() -> None:
    # Every spelling must resolve to a hex value, so the swatch can show its colour.
    for name, hex_value in MANIM_COLORS.items():
        (site,) = find_color_sites(f"Circle(color={name})\n")
        assert site.hex == hex_value


def test_palette_table_matches_installed_manimlib() -> None:
    constants = pytest.importorskip("manimlib.constants")
    mismatches = {
        name: (getattr(constants, name), hex_value)
        for name, hex_value in MANIM_COLORS.items()
        if str(getattr(constants, name, "")).upper() != hex_value
    }
    assert mismatches == {}


def test_neutralise_ignores_which_colour_but_not_whether_one_is_given() -> None:
    def dump(source: str) -> str:
        call = ast.parse(source, mode="eval").body
        assert isinstance(call, ast.Call)
        return ast.dump(neutralise_colors(call), include_attributes=False)

    assert dump("Circle(radius=1, color=BLUE)") == dump('Circle(radius=1, color="#FF0000")')
    assert dump("Circle(radius=1, color=BLUE)") != dump("Circle(radius=1)")
    assert dump("c.set_color(BLUE)") == dump("c.set_color(GREEN)")


def test_recolouring_keeps_the_timeline_site_identity() -> None:
    before = index_source(
        "class Demo(Scene):\n"
        "    def construct(self):\n"
        "        self.play(ShowCreation(circle, color=BLUE), run_time=2)\n"
    )
    after = index_source(
        "class Demo(Scene):\n"
        "    def construct(self):\n"
        '        self.play(ShowCreation(circle, color="#FF0000"), run_time=2)\n'
    )
    assert [s.site_id for s in before.sites] == [s.site_id for s in after.sites]


def test_changing_the_animation_still_changes_the_site_identity() -> None:
    before = index_source("self.play(ShowCreation(circle, color=BLUE), run_time=2)\n")
    after = index_source("self.play(Write(circle, color=BLUE), run_time=2)\n")
    assert before.sites[0].site_id != after.sites[0].site_id
