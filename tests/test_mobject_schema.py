"""The discovered mobject schema (ADR 0012): MRO flattening, widget kinds and colour resolution."""

from __future__ import annotations

from typing import Any

import pytest

from manim_studio.mobject_schema import (
    ParamSchema,
    SchemaError,
    fields_for,
    lookup,
    methods_for,
    parse_schema,
    resolve_colour,
    ui_kind,
)


def _param(name: str, *, annotation: str = "", default: Any = None, kind: str = "") -> dict:
    has_default = default is not None
    return {
        "name": name,
        "kind": kind or "positional_or_keyword",
        "annotation": annotation,
        "has_default": has_default,
        "default": default,
        "default_literal": has_default,
        "default_repr": repr(default) if has_default else None,
        "owner": "",
    }


def _payload() -> dict[str, Any]:
    base_params = [
        _param("stroke_color", annotation="ManimColor", default="#DDDDDD"),
        _param("fill_opacity", annotation="float", default=0.0),
        _param("kwargs", kind="var_keyword"),
    ]
    circle_params = [
        _param("radius", annotation="float", default=1.0),
        _param("stroke_color", annotation="ManimColor", default="#FC6255"),
    ]
    return {
        "schema_version": 1,
        "manimlib_version": "9.9.9",
        "exported": {"Shape": "m.Shape", "Circle": "m.Circle"},
        "colours": {"BLUE": "#58C4DD", "RED": "#FC6255"},
        "classes": {
            "m.Mobject": {
                "name": "Mobject",
                "module": "m",
                "mro": ["m.Mobject"],
                "params": [],
                "methods": [
                    {
                        "name": "shift",
                        "owner": "m.Mobject",
                        "params": [_param("vector")],
                        "doc": "Move.",
                    }
                ],
            },
            "m.Shape": {
                "name": "Shape",
                "module": "m",
                "mro": ["m.Shape", "m.Mobject"],
                "params": base_params,
                "methods": [
                    {
                        "name": "set_fill",
                        "owner": "m.Shape",
                        "params": [_param("color"), _param("opacity", annotation="float")],
                        "doc": "Fill.",
                    }
                ],
            },
            "m.Circle": {
                "name": "Circle",
                "module": "m",
                "mro": ["m.Circle", "m.Shape", "m.Mobject"],
                "params": circle_params,
                "methods": [],
            },
        },
    }


def _schema() -> Any:
    payload = _payload()
    # The probe records each parameter's declaring class; mirror that here.
    for key, cls in payload["classes"].items():
        for param in cls["params"]:
            param["owner"] = key
    return parse_schema(payload)


def test_lookup_accepts_exported_names_and_keys() -> None:
    schema = _schema()
    assert lookup(schema, "Circle").name == "Circle"  # type: ignore[union-attr]
    assert lookup(schema, "m.Circle").name == "Circle"  # type: ignore[union-attr]
    assert lookup(schema, "Nope") is None


def test_fields_flatten_along_the_mro_and_the_subclass_shadows_the_base() -> None:
    schema = _schema()
    circle = lookup(schema, "Circle")
    assert circle is not None
    fields = fields_for(schema, circle)
    assert [f.name for f in fields] == ["radius", "stroke_color", "fill_opacity"]
    stroke = next(f for f in fields if f.name == "stroke_color")
    assert stroke.default == "#FC6255"  # Circle's, not Shape's
    assert stroke.owner == "m.Circle"


def test_var_keyword_is_not_a_field() -> None:
    schema = _schema()
    circle = lookup(schema, "Circle")
    assert circle is not None
    assert all(f.kind != "var_keyword" for f in fields_for(schema, circle))


def test_methods_flatten_along_the_mro() -> None:
    schema = _schema()
    circle = lookup(schema, "Circle")
    assert circle is not None
    assert [m.name for m in methods_for(schema, circle)] == ["set_fill", "shift"]


def test_wrong_schema_version_is_refused() -> None:
    payload = _payload()
    payload["schema_version"] = 999
    with pytest.raises(SchemaError):
        parse_schema(payload)


def test_malformed_schema_is_refused() -> None:
    payload = _payload()
    del payload["classes"]
    with pytest.raises(SchemaError):
        parse_schema(payload)


def _field(
    name: str,
    *,
    annotation: str = "",
    default: Any = None,
    has_default: bool = True,
    kind: str = "positional_or_keyword",
) -> ParamSchema:
    return ParamSchema(
        name=name,
        kind=kind,
        annotation=annotation,
        has_default=has_default,
        default=default,
        default_literal=True,
        default_repr=repr(default),
        owner="m.X",
    )


@pytest.mark.parametrize(
    ("param", "kind"),
    [
        (_field("stroke_color", annotation="ManimColor", default="#FC6255"), "colour"),
        (_field("color", annotation="ManimColor | Iterable[ManimColor]"), "colour"),
        (_field("fill_color", default=None, has_default=False), "colour"),
        (_field("vmobjects", kind="var_positional"), "expr"),
        (_field("colors", kind="var_positional", annotation="ManimColor"), "colour_list"),
        (_field("fill_opacity", annotation="float", default=0.0), "opacity"),
        (_field("stroke_width", annotation="float | Iterable[float]", default=4.0), "float"),
        (_field("radius", annotation="float", default=1.0), "float"),
        (_field("font_size", annotation="int", default=48), "float"),
        (_field("n", annotation="int", default=6), "int"),
        (_field("flat_stroke", annotation="bool", default=False), "bool"),
        (_field("font", annotation="str", default=""), "font"),
        (_field("text", annotation="str", default=None, has_default=False), "text"),
        (_field("vector", annotation="Vect3"), "expr"),
        (_field("start", annotation="Vect3 | Mobject", default=None), "expr"),
    ],
)
def test_ui_kind(param: ParamSchema, kind: str) -> None:
    assert ui_kind(param) == kind


def test_resolve_colour_accepts_hex_and_manim_names() -> None:
    colours = {"BLUE": "#58C4DD"}
    assert resolve_colour('"#ff00aa"', colours) == "#FF00AA"
    assert resolve_colour("BLUE", colours) == "#58C4DD"
    assert resolve_colour("'#58c4dd'", colours) == "#58C4DD"
    assert resolve_colour("GREEN", colours) is None
    assert resolve_colour("#FFF", colours) is None
