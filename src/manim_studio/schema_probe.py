"""Discover ManimGL's mobject API by introspection and print it as JSON (ADR 0012).

``python -m manim_studio.schema_probe`` imports ``manimlib`` in its own process, so the editor
never imports it (AGENTS.md rule 2).  One dump holds everything the inspector needs: for every
``Mobject`` class in the MRO of an exported class, its own ``__init__`` parameters with their real
default values, its public chainable methods (methods whose body returns ``self``) and the first
line of their docstrings; plus the hex value of every colour constant.

Stdout carries only the JSON document; manimlib's import-time warnings go to stderr.
:mod:`manim_studio.mobject_schema` reads the result.
"""

from __future__ import annotations

import ast
import contextlib
import importlib.metadata
import inspect
import json
import re
import sys
import textwrap
from collections.abc import Callable
from typing import Any

__all__ = ["SCHEMA_VERSION", "main", "probe"]

#: Bumped whenever the shape of the JSON changes; old caches are then rebuilt.
SCHEMA_VERSION = 1

_HEX = re.compile(r"#[0-9A-Fa-f]{6}")


def _encode(value: Any) -> tuple[Any, bool]:
    """``(json_value, is_literal)``.  Literal values can be written back to source as they are."""
    if value is None or isinstance(value, bool | int | float | str):
        return value, True
    if isinstance(value, list | tuple):
        parts = [_encode(item) for item in value]
        return [part for part, _ in parts], all(ok for _, ok in parts)
    if isinstance(value, dict) and all(isinstance(key, str) for key in value):
        encoded = {key: _encode(item) for key, item in value.items()}
        plain = {key: part for key, (part, _) in encoded.items()}
        return plain, all(ok for _, ok in encoded.values())
    return None, False


def _annotation(annotation: Any) -> str:
    if annotation is inspect.Parameter.empty:
        return ""
    if isinstance(annotation, str):
        return annotation
    return inspect.formatannotation(annotation)


def _param(param: inspect.Parameter, owner: str) -> dict[str, Any]:
    has_default = param.default is not inspect.Parameter.empty
    default, literal = _encode(param.default) if has_default else (None, False)
    return {
        "name": param.name,
        "kind": param.kind.name.lower(),
        "annotation": _annotation(param.annotation),
        "has_default": has_default,
        "default": default,
        "default_literal": literal,
        "default_repr": repr(param.default) if has_default else None,
        "owner": owner,
    }


def _signature_params(function: Callable[..., Any], owner: str) -> list[dict[str, Any]] | None:
    try:
        parameters = list(inspect.signature(function).parameters.values())
    except (TypeError, ValueError):
        return None
    # The first parameter is ``self``.
    return [_param(param, owner) for param in parameters[1:]]


def _returns_self(function: Callable[..., Any]) -> bool:
    """True for manimlib's chaining convention: a ``return self`` somewhere in the body."""
    try:
        tree = ast.parse(textwrap.dedent(inspect.getsource(function)))
    except (OSError, TypeError, SyntaxError):
        return False
    return any(
        isinstance(node, ast.Return)
        and isinstance(node.value, ast.Name)
        and node.value.id == "self"
        for node in ast.walk(tree)
    )


def _first_line(function: Callable[..., Any]) -> str:
    doc = inspect.getdoc(function) or ""
    return doc.strip().splitlines()[0] if doc.strip() else ""


def _key(cls: type) -> str:
    return f"{cls.__module__}.{cls.__qualname__}"


def _describe(cls: type, key: str) -> dict[str, Any]:
    params: list[dict[str, Any]] = []
    own_init = cls.__dict__.get("__init__")
    if inspect.isfunction(own_init):
        params = _signature_params(own_init, key) or []
    methods: list[dict[str, Any]] = []
    for name, member in sorted(vars(cls).items()):
        if name.startswith("_") or not inspect.isfunction(member) or not _returns_self(member):
            continue
        method_params = _signature_params(member, key)
        if method_params is None:
            continue
        methods.append(
            {"name": name, "owner": key, "params": method_params, "doc": _first_line(member)}
        )
    return {
        "name": cls.__name__,
        "module": cls.__module__,
        "mro": [],  # filled by the caller once every class is known
        "params": params,
        "methods": methods,
    }


def probe() -> dict[str, Any]:
    """The full description of manimlib's mobject classes, as a JSON-ready dict."""
    sys.argv = sys.argv[:1]  # manimgl parses argv when it is imported
    with contextlib.redirect_stdout(sys.stderr):
        import manimlib
        from manimlib import constants
        from manimlib.mobject.mobject import Mobject

    live: dict[str, type] = {}
    exported: dict[str, str] = {}
    for name, obj in sorted(vars(manimlib).items()):
        if not (inspect.isclass(obj) and issubclass(obj, Mobject)):
            continue
        exported[name] = _key(obj)
        for base in obj.__mro__:
            if issubclass(base, Mobject) and base.__module__.startswith("manimlib"):
                live.setdefault(_key(base), base)

    classes: dict[str, dict[str, Any]] = {}
    for key, cls in live.items():
        classes[key] = _describe(cls, key)
    # Each MRO is recorded as keys so the editor can flatten inherited parameters.
    for key, cls in live.items():
        classes[key]["mro"] = [_key(base) for base in cls.__mro__ if _key(base) in classes]

    colours = {
        name: value
        for name, value in sorted(vars(constants).items())
        if name.isupper() and isinstance(value, str) and _HEX.fullmatch(value)
    }
    return {
        "schema_version": SCHEMA_VERSION,
        "manimlib_version": _manimlib_version(),
        "classes": classes,
        "exported": exported,
        "colours": colours,
    }


def _manimlib_version() -> str:
    try:
        return importlib.metadata.version("manimgl")
    except importlib.metadata.PackageNotFoundError:
        return "unknown"


def main() -> int:
    json.dump(probe(), sys.stdout, ensure_ascii=False)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
