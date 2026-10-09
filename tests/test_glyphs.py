"""Tests for the vector icon set and icon-only buttons."""

from __future__ import annotations

import pytest
from PySide6.QtGui import QColor

from manim_studio.editor.glyphs import GLYPH_NAMES, glyph, icon_button
from manim_studio.editor.timeline_transport import EDIT_ACTIONS


@pytest.mark.parametrize("name", GLYPH_NAMES)
def test_every_glyph_renders_visible_pixels(qtbot, name: str) -> None:
    icon = glyph(name, QColor("#ff0000"))
    image = icon.pixmap(18, 18).toImage()
    visible = sum(1 for x in range(18) for y in range(18) if image.pixelColor(x, y).alpha() > 0)
    assert visible > 10, f"{name} drew nothing"


def test_disabled_variant_is_dimmer(qtbot) -> None:
    from PySide6.QtGui import QIcon

    icon = glyph("play", QColor("#ff0000"))
    normal = icon.pixmap(18, 18, QIcon.Mode.Normal).toImage()
    dimmed = icon.pixmap(18, 18, QIcon.Mode.Disabled).toImage()
    peak = lambda image: max(  # noqa: E731
        image.pixelColor(x, y).alpha() for x in range(18) for y in range(18)
    )
    assert peak(dimmed) < peak(normal)


def test_unknown_glyph_is_an_error(qtbot) -> None:
    with pytest.raises(KeyError):
        glyph("nope")


def test_icon_buttons_have_no_text_but_a_full_name_tooltip(qtbot) -> None:
    button = icon_button("undo", "Undo timeline edit  (Ctrl+Z on the timeline)")
    qtbot.addWidget(button)
    assert button.text() == ""
    assert not button.icon().isNull()
    assert "Undo timeline edit" in button.toolTip()
    assert button.accessibleName() == "Undo timeline edit"


def test_every_toolbar_operation_has_a_glyph() -> None:
    assert {glyph_name for _id, glyph_name, _tip in EDIT_ACTIONS} <= set(GLYPH_NAMES)
