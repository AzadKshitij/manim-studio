"""The probe against the installed manimlib (ADR 0012): what the discovered schema really holds."""

from __future__ import annotations

import importlib.util
import json

import pytest

if importlib.util.find_spec("manimlib") is None:
    pytest.skip("manimlib is not installed", allow_module_level=True)

from manim_studio.mobject_schema import (
    fields_for,
    lookup,
    methods_for,
    parse_schema,
    ui_kind,
)
from manim_studio.schema_probe import SCHEMA_VERSION, probe


@pytest.fixture(scope="module")
def schema():  # type: ignore[no-untyped-def]
    return parse_schema(json.loads(json.dumps(probe())))


def test_probe_output_is_json_and_versioned(schema) -> None:  # type: ignore[no-untyped-def]
    assert schema.manimlib_version != ""
    assert SCHEMA_VERSION == 2


def test_every_exported_mobject_class_is_discovered(schema) -> None:  # type: ignore[no-untyped-def]
    for name in ("Circle", "Dot", "Square", "Rectangle", "Line", "Arrow", "Text", "VGroup"):
        assert lookup(schema, name) is not None, name


def test_circle_fields_come_from_its_whole_mro_with_real_defaults(schema) -> None:  # type: ignore[no-untyped-def]
    circle = lookup(schema, "Circle")
    names = [f.name for f in fields_for(schema, circle)]
    assert "radius" in names  # from Arc
    assert "fill_opacity" in names  # from VMobject
    assert "start_angle" in names
    assert "kwargs" not in names
    stroke = next(f for f in fields_for(schema, circle) if f.name == "stroke_color")
    assert stroke.default == "#FC6255"  # RED resolved to its hex by the real module
    assert stroke.default_literal is True
    assert ui_kind(stroke) == "colour"


def test_text_exposes_font_size_and_font(schema) -> None:  # type: ignore[no-untyped-def]
    fields = {f.name: f for f in fields_for(schema, lookup(schema, "Text"))}
    assert {"text", "font_size", "font", "t2c", "gradient"} <= fields.keys()
    assert ui_kind(fields["font_size"]) == "float"
    assert ui_kind(fields["font"]) == "font"


def test_chainable_setters_are_discovered(schema) -> None:  # type: ignore[no-untyped-def]
    circle = lookup(schema, "Circle")
    methods = {m.name for m in methods_for(schema, circle)}
    assert {"set_fill", "set_stroke", "set_color", "shift", "move_to"} <= methods
    assert "set_color_by_gradient" in {m.name for m in methods_for(schema, lookup(schema, "Text"))}


def test_colour_constants_are_hex(schema) -> None:  # type: ignore[no-untyped-def]
    assert schema.colours["BLUE"] == "#58C4DD"
    assert all(value.startswith("#") and len(value) == 7 for value in schema.colours.values())


def test_every_discovered_field_maps_to_a_widget(schema) -> None:  # type: ignore[no-untyped-def]
    from manim_studio.mobject_schema import UI_KINDS

    for name in schema.exported:
        cls = lookup(schema, name)
        for field in fields_for(schema, cls):
            assert ui_kind(field) in UI_KINDS, (name, field.name)


def test_array_defaults_are_vectors_and_none_defaults_keep_their_type(schema) -> None:  # type: ignore[no-untyped-def]
    circle = {f.name: f for f in fields_for(schema, lookup(schema, "Circle"))}
    assert circle["arc_center"].default == [0.0, 0.0, 0.0]  # a numpy array, read as a list
    assert circle["arc_center"].default_literal is True
    assert ui_kind(circle["arc_center"]) == "vector"
    assert ui_kind(circle["shading"]) == "vector"
    assert ui_kind(circle["fill_color"]) == "colour"  # default None, annotation says colour
    assert ui_kind(circle["color"]) == "colour"
    assert ui_kind(circle["background_image_file"]) == "text"
    text = {f.name: f for f in fields_for(schema, lookup(schema, "Text"))}
    assert ui_kind(text["t2c"]) == "colour_map"


def test_every_default_carries_its_type_name(schema) -> None:  # type: ignore[no-untyped-def]
    circle = {f.name: f for f in fields_for(schema, lookup(schema, "Circle"))}
    assert circle["radius"].default_type == "float"
    assert circle["fill_color"].default_type == "NoneType"
