"""What the inspector can show for each mobject class (ADR 0012).

Pure data, stdlib only.  Reads the JSON that :mod:`manim_studio.schema_probe` prints, flattens
constructor parameters and chainable methods along each class's MRO (a subclass's parameter
shadows the same name on its bases, the way ``**kwargs`` forwarding works at runtime) and decides
which editor widget each parameter gets.  Nothing here is hand-written per class: a new manimlib
release changes the discovered schema, not this module.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from manim_studio.schema_probe import SCHEMA_VERSION

__all__ = [
    "UI_KINDS",
    "ClassSchema",
    "MethodSchema",
    "MobjectSchema",
    "ParamSchema",
    "SchemaError",
    "fields_for",
    "lookup",
    "methods_for",
    "parse_schema",
    "resolve_colour",
    "ui_kind",
]

#: Editor widgets a parameter can get; see :func:`ui_kind`.
UI_KINDS: tuple[str, ...] = (
    "bool",
    "int",
    "float",
    "opacity",
    "text",
    "font",
    "colour",
    "colour_list",
    "expr",
)

_HEX = re.compile(r"#[0-9A-Fa-f]{6}")


class SchemaError(ValueError):
    """The schema JSON is not one this build understands."""


@dataclass(frozen=True, slots=True)
class ParamSchema:
    """One parameter of a constructor or method, as discovered."""

    name: str
    kind: str  #: ``positional_or_keyword``, ``keyword_only``, ``var_positional``, ``var_keyword``
    annotation: str
    has_default: bool
    default: Any  #: JSON value of the default (``None`` when it is not a plain literal)
    default_literal: bool  #: the default can be written into source as it is
    default_repr: str | None
    owner: str  #: MRO key of the class that declares it


@dataclass(frozen=True, slots=True)
class MethodSchema:
    """A public chainable method (one whose body returns ``self``)."""

    name: str
    owner: str
    params: tuple[ParamSchema, ...]
    doc: str


@dataclass(frozen=True, slots=True)
class ClassSchema:
    """One manimlib class: its MRO, its own parameters and its own chainable methods."""

    key: str  #: ``module.qualname``, unique across manimlib
    name: str
    module: str
    lineage: tuple[str, ...]  #: keys along the MRO, this class first
    params: tuple[ParamSchema, ...]
    methods: tuple[MethodSchema, ...]


@dataclass(frozen=True, slots=True)
class MobjectSchema:
    """Everything discovered for one manimlib version."""

    manimlib_version: str
    classes: Mapping[str, ClassSchema]  #: by key
    exported: Mapping[str, str]  #: ``Circle`` -> key, as ``from manimlib import *`` exposes it
    colours: Mapping[str, str] = field(default_factory=dict)  #: constant name -> ``#RRGGBB``


def _param(data: Mapping[str, Any]) -> ParamSchema:
    return ParamSchema(
        name=str(data["name"]),
        kind=str(data["kind"]),
        annotation=str(data.get("annotation", "")),
        has_default=bool(data.get("has_default", False)),
        default=data.get("default"),
        default_literal=bool(data.get("default_literal", False)),
        default_repr=data.get("default_repr"),
        owner=str(data["owner"]),
    )


def parse_schema(data: Mapping[str, Any]) -> MobjectSchema:
    """Build a :class:`MobjectSchema` from the probe's JSON.  Raises :class:`SchemaError`."""
    if data.get("schema_version") != SCHEMA_VERSION:
        raise SchemaError(f"schema version {data.get('schema_version')!r} is not supported")
    try:
        classes: dict[str, ClassSchema] = {}
        for key, raw in data["classes"].items():
            methods = tuple(
                MethodSchema(
                    name=str(method["name"]),
                    owner=str(method["owner"]),
                    params=tuple(_param(p) for p in method["params"]),
                    doc=str(method.get("doc", "")),
                )
                for method in raw["methods"]
            )
            classes[key] = ClassSchema(
                key=key,
                name=str(raw["name"]),
                module=str(raw["module"]),
                lineage=tuple(str(item) for item in raw["mro"]),
                params=tuple(_param(p) for p in raw["params"]),
                methods=methods,
            )
        return MobjectSchema(
            manimlib_version=str(data["manimlib_version"]),
            classes=classes,
            exported={str(k): str(v) for k, v in data["exported"].items()},
            colours={str(k): str(v) for k, v in data.get("colours", {}).items()},
        )
    except (KeyError, TypeError, AttributeError) as error:
        raise SchemaError(f"malformed schema: {error}") from error


def lookup(schema: MobjectSchema, name: str) -> ClassSchema | None:
    """The class a source name refers to (``Circle``), or a full key; ``None`` when unknown."""
    key = schema.exported.get(name, name)
    return schema.classes.get(key)


def fields_for(schema: MobjectSchema, cls: ClassSchema) -> tuple[ParamSchema, ...]:
    """Constructor keyword parameters of ``cls`` along its MRO, most derived first.

    ``**kwargs`` entries are skipped (they only forward to the next class up the MRO), and a
    parameter that a subclass redeclares (``stroke_color`` on ``Circle``) shadows the base's.
    """
    return _flatten(schema, cls, lambda klass: klass.params)


def methods_for(schema: MobjectSchema, cls: ClassSchema) -> tuple[MethodSchema, ...]:
    """Chainable methods of ``cls`` along its MRO, most derived first, one per name."""
    seen: set[str] = set()
    out: list[MethodSchema] = []
    for key in cls.lineage:
        base = schema.classes.get(key)
        if base is None:
            continue
        for method in base.methods:
            if method.name not in seen:
                seen.add(method.name)
                out.append(method)
    return tuple(out)


def _flatten(
    schema: MobjectSchema,
    cls: ClassSchema,
    own: Any,
) -> tuple[ParamSchema, ...]:
    seen: set[str] = set()
    out: list[ParamSchema] = []
    for key in cls.lineage:
        base = schema.classes.get(key)
        if base is None:
            continue
        for param in own(base):
            if param.kind == "var_keyword" or param.name in seen:
                continue
            seen.add(param.name)
            out.append(param)
    return tuple(out)


def _is_number(value: Any) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool)


def ui_kind(param: ParamSchema) -> str:
    """The editor widget for ``param``, from its annotation, default and name.

    Typed defaults and annotations decide first; a few name hints (``*opacity``, ``font``,
    ``font_size``) refine them.  Anything not understood is ``expr``: a text field holding the
    Python expression, which is always safe to show and to write back.
    """
    annotation = param.annotation
    name = param.name
    if param.kind == "var_positional":
        return "colour_list" if "Color" in annotation or name.endswith("colors") else "expr"
    if "Color" in annotation or name.endswith("color"):
        return "colour"
    if name == "font":
        return "font"
    if name == "font_size":
        return "float"
    if name.endswith("opacity"):
        return "opacity"
    default = param.default if param.has_default else None
    if annotation == "bool" or isinstance(default, bool):
        return "bool"
    is_int = annotation == "int" or (isinstance(default, int) and not isinstance(default, bool))
    if is_int and "float" not in annotation:
        return "int"
    if "float" in annotation or isinstance(default, float):
        return "float"
    if annotation == "str" or isinstance(default, str):
        return "text"
    return "expr"


def resolve_colour(text: str, colours: Mapping[str, str]) -> str | None:
    """``#RRGGBB`` for a colour written as a hex literal or a manim constant name, else ``None``.

    The hex form is returned uppercase.  Quotes around a hex string are accepted, so the value
    of a literal node can be passed straight in.
    """
    stripped = text.strip().strip("'\"")
    if _HEX.fullmatch(stripped):
        return stripped.upper()
    value = colours.get(stripped)
    return value.upper() if value else None
