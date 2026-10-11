"""Tests for the procedural icon generator (``packaging/make_icon.py``)."""

from __future__ import annotations

import importlib.util
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

import pytest

SVG_NS = "{http://www.w3.org/2000/svg}"


def _load_make_icon() -> Any:
    path = Path(__file__).resolve().parents[1] / "packaging" / "make_icon.py"
    spec = importlib.util.spec_from_file_location("make_icon", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def make_icon() -> Any:
    return _load_make_icon()


def test_tiled_icon_has_an_opaque_background(make_icon: Any) -> None:
    image = make_icon._render(64)
    assert image.getpixel((32, 1))[3] == 255


def test_transparent_icon_leaves_the_background_clear(make_icon: Any) -> None:
    image = make_icon._render(64, background=False)
    assert image.getpixel((32, 1))[3] == 0
    # ... but the mark itself is drawn.
    assert image.getchannel("A").getextrema()[1] == 255


def test_transparent_png_is_square_at_the_target_size(make_icon: Any) -> None:
    image = make_icon._render(make_icon.PNG_SIZE, background=False)
    assert image.size == (make_icon.PNG_SIZE, make_icon.PNG_SIZE)


def test_svg_mark_is_well_formed_vector_art(make_icon: Any) -> None:
    root = ET.fromstring(make_icon._svg_mark())
    assert root.tag == f"{SVG_NS}svg"
    assert root.get("viewBox") == "0 0 1000 1000"
    tags = [child.tag.rsplit("}", 1)[-1] for child in root]
    assert "path" in tags and "circle" in tags and "polygon" in tags
    strokes = {child.get("stroke") for child in root.iter() if child.get("stroke")}
    assert {"#58c4dd", "#83c167"} <= strokes  # the cyan and green S segments


def test_svg_mark_has_no_background_fill(make_icon: Any) -> None:
    root = ET.fromstring(make_icon._svg_mark())
    rectangles = [child for child in root if child.tag == f"{SVG_NS}rect"]
    assert rectangles, "the timeline strip is drawn with rects"
    # A tiled background would be a full-canvas rect; none may reach the viewBox edge.
    assert all(float(rect.get("width", "0")) < 1000 for rect in rectangles)
