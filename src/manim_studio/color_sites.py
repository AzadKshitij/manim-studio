"""Colour literals in scene source: find them, name them and spell them back (pure, stdlib only).

The editor shows a colour swatch beside each literal (``color=BLUE``, ``fill_color="#FF6B6B"``,
``set_color(GREEN)``) and writes the picked colour back into the source.  This module does the
source side only: it locates each literal with its exact UTF-8 byte span (``ast`` columns are
bytes, which is what Scintilla uses) and chooses how a picked colour is spelled.

Colour values are not part of a call's timeline identity (ADR 0011), so
:func:`neutralise_colors` gives :mod:`site_index` a fingerprint payload that ignores them.

The constant table mirrors ``manimlib.constants`` (ManimGL).  Values can be overridden by
``manim_config``; the test suite compares this table with the installed package.
"""

from __future__ import annotations

import ast
import copy
from dataclasses import dataclass

__all__ = [
    "COLOR_KEYWORDS",
    "COLOR_SETTERS",
    "MANIM_COLORS",
    "ColorSite",
    "find_color_sites",
    "literal_for",
    "neutralise_colors",
]

#: Constant name -> ``#RRGGBB``.  Plain names come before their ``_C`` aliases so that a
#: picked colour is written back as the plain name (``BLUE``, not ``BLUE_C``).
MANIM_COLORS: dict[str, str] = {
    "BLUE": "#58C4DD",
    "BLUE_C": "#58C4DD",
    "BLUE_A": "#C7E9F1",
    "BLUE_B": "#9CDCEB",
    "BLUE_D": "#29ABCA",
    "BLUE_E": "#1C758A",
    "TEAL": "#5CD0B3",
    "TEAL_C": "#5CD0B3",
    "GREEN": "#83C167",
    "GREEN_C": "#83C167",
    "YELLOW": "#FFFF00",
    "YELLOW_C": "#FFFF00",
    "GOLD": "#F0AC5F",
    "GOLD_C": "#F0AC5F",
    "RED": "#FC6255",
    "RED_C": "#FC6255",
    "RED_E": "#CF5044",
    "MAROON": "#C55F73",
    "MAROON_C": "#C55F73",
    "PURPLE": "#9A72AC",
    "PURPLE_C": "#9A72AC",
    "ORANGE": "#FF862F",
    "GREY": "#888888",
    "GREY_C": "#888888",
    "WHITE": "#FFFFFF",
    "BLACK": "#000000",
}

#: Keyword arguments whose value is a colour.
COLOR_KEYWORDS = frozenset({"color", "fill_color", "stroke_color", "background_color"})
#: Methods whose positional arguments are colours (or an opacity, which never matches).
COLOR_SETTERS = frozenset(
    {"set_color", "set_fill", "set_color_by_gradient", "set_fill_by_gradient"}
)

_NAME_FOR_HEX: dict[str, str] = {}
for _name, _hex in MANIM_COLORS.items():
    _NAME_FOR_HEX.setdefault(_hex, _name)


@dataclass(frozen=True, slots=True)
class ColorSite:
    """One colour literal: its line (1-based), UTF-8 byte span and resolved colour."""

    line: int
    start: int
    end: int
    text: str  #: the literal as written, e.g. ``BLUE`` or ``"#FF6B6B"``
    hex: str  #: resolved ``#RRGGBB``, uppercase


def _normalise_hex(value: str) -> str:
    return value.upper()


def _resolve(node: ast.expr) -> str | None:
    """The ``#RRGGBB`` value of a manim colour constant or a hex string literal."""
    if isinstance(node, ast.Name) and node.id in MANIM_COLORS:
        return MANIM_COLORS[node.id]
    if (
        isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and len(node.value) == 7
        and node.value.startswith("#")
        and all(ch in "0123456789abcdefABCDEF" for ch in node.value[1:])
    ):
        return _normalise_hex(node.value)
    return None


def _call_name(call: ast.Call) -> str:
    func = call.func
    if isinstance(func, ast.Attribute):
        return func.attr
    if isinstance(func, ast.Name):
        return func.id
    return ""


def _colour_nodes(call: ast.Call) -> list[ast.expr]:
    nodes = [kw.value for kw in call.keywords if kw.arg in COLOR_KEYWORDS]
    if _call_name(call) in COLOR_SETTERS:
        nodes.extend(call.args)
    return nodes


def find_color_sites(source: str) -> tuple[ColorSite, ...]:
    """Every colour literal in ``source``, in source order.

    Only literals on one line are reported; computed colours (``color=my_var``,
    ``interpolate_color(...)``) are left alone.  Raises :class:`SyntaxError` for invalid
    source; the caller decides what to show then.
    """
    data = source.encode("utf-8")
    line_starts = [0]
    for index, byte in enumerate(data):
        if byte == 0x0A:
            line_starts.append(index + 1)

    sites: list[ColorSite] = []
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Call):
            continue
        for value in _colour_nodes(node):
            hex_value = _resolve(value)
            if (
                hex_value is None
                or value.end_lineno != value.lineno
                or value.end_col_offset is None
            ):
                continue
            start = line_starts[value.lineno - 1] + value.col_offset
            end = line_starts[value.lineno - 1] + value.end_col_offset
            text = data[start:end].decode("utf-8")  # the literal exactly as written, quotes too
            sites.append(ColorSite(value.lineno, start, end, text, hex_value))
    return tuple(sorted(sites, key=lambda site: site.start))


def literal_for(hex_value: str, original: str) -> str:
    """How to spell ``hex_value`` in source, given the literal it replaces.

    A colour that matches a manim constant is written as that name.  Anything else becomes a
    quoted hex string, using the quote style of the original string when it was one.
    """
    name = _NAME_FOR_HEX.get(_normalise_hex(hex_value))
    if name is not None:
        return name
    quote = "'" if original.startswith("'") else '"'
    return f"{quote}{_normalise_hex(hex_value)}{quote}"


class _Neutraliser(ast.NodeTransformer):
    def visit_Call(self, node: ast.Call) -> ast.AST:
        self.generic_visit(node)
        for keyword in node.keywords:
            if keyword.arg in COLOR_KEYWORDS:
                keyword.value = ast.Constant(value=None)
        if _call_name(node) in COLOR_SETTERS:
            node.args = [ast.Constant(value=None) for _ in node.args]
        return node


def neutralise_colors(node: ast.AST) -> ast.AST:
    """A deep copy of ``node`` with every colour value replaced by ``None``.

    Keeps the shape of the call (a colour keyword stays a keyword), so adding or removing a
    colour still changes the fingerprint, but changing which colour it is does not.
    """
    return _Neutraliser().visit(copy.deepcopy(node))
