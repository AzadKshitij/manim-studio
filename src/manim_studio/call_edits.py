"""Source edits for mobject targets: set, remove and add call arguments (ADR 0012).

Each change is one minimal splice of UTF-8 bytes, so the comments, layout and quoting around
it survive.  Changes are applied one at a time and the target is found again after each one,
so an earlier change can never leave a later one pointing at stale offsets.  Chained calls
are added last: they shift the call indices of the calls after them.  The result must parse
as Python, or :class:`EditError` is raised and nothing is returned.
"""

from __future__ import annotations

import ast
import itertools
import json
import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from manim_studio.mobject_sites import CallRef, MobjectTarget, find_mobject_targets

__all__ = [
    "AddCall",
    "Change",
    "EditError",
    "RemoveArgument",
    "SetArgument",
    "apply_changes",
    "format_colour",
    "format_value",
]


class EditError(ValueError):
    """A change cannot be written: the target is gone, a value is not an expression, or the
    result would not parse."""


@dataclass(frozen=True, slots=True)
class SetArgument:
    """Set a keyword argument (``keyword``) or a positional one (``position``) of a call.

    An absent keyword is appended.  A positional index one past the last positional argument is
    appended after it.  ``value`` is Python source text.
    """

    call: int
    value: str
    keyword: str | None = None
    position: int | None = None


@dataclass(frozen=True, slots=True)
class RemoveArgument:
    """Remove a keyword or positional argument.  Removing one that is not there does nothing."""

    call: int
    keyword: str | None = None
    position: int | None = None


@dataclass(frozen=True, slots=True)
class AddCall:
    """Append a chained call to the target's assignment: ``name = Ctor(...).method(args)``."""

    method: str
    args: tuple[str, ...] = ()
    keywords: tuple[tuple[str, str], ...] = ()


Change = SetArgument | RemoveArgument | AddCall


def format_colour(hex_value: str, original: str) -> str:
    """How to spell a picked colour in source, given the literal it replaces.

    Always a quoted hex string, in the quote style of the literal it replaces.  Manim constant
    names (``BLUE``) are never written: a colour set from the inspector reads the same wherever
    the source is moved.  A colour the user did not change keeps its original spelling; that is
    decided by the caller, not here.
    """
    quote = "'" if original.startswith("'") else '"'
    return f"{quote}{hex_value.upper()}{quote}"


def format_value(value: Any) -> str:
    """Python source for a plain value: numbers, booleans, strings, ``None`` and lists of them."""
    if value is None:
        return "None"
    if isinstance(value, bool):
        return "True" if value else "False"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        if not math.isfinite(value):
            raise EditError(f"{value!r} has no source spelling")
        return repr(value)
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, list | tuple):
        return "[" + ", ".join(format_value(item) for item in value) + "]"
    raise EditError(f"{type(value).__name__} has no source spelling")


def apply_changes(source: str, target: MobjectTarget, changes: Sequence[Change]) -> str:
    """``source`` with ``changes`` applied to ``target``.  Raises :class:`EditError`."""
    ordered: list[Change] = [c for c in changes if not isinstance(c, AddCall)]
    ordered += [c for c in changes if isinstance(c, AddCall)]
    text = source
    for change in ordered:
        current = _find(text, target.identity)
        text = _apply_one(text, current, change)
    _check_parses(text)
    return text


def _find(text: str, identity: str) -> MobjectTarget:
    try:
        targets = find_mobject_targets(text)
    except SyntaxError as error:
        raise EditError(f"the source does not parse: {error.msg}") from error
    for target in targets:
        if target.identity == identity:
            return target
    raise EditError(f"{identity} is no longer in the source")


def _apply_one(text: str, target: MobjectTarget, change: Change) -> str:
    return _splice(text.encode("utf-8"), _splices(target, change)).decode("utf-8")


def _splices(target: MobjectTarget, change: Change) -> list[tuple[int, int, bytes]]:
    if isinstance(change, AddCall):
        return [_add_call_splice(target, change)]
    call = _call(target, change.call)
    if isinstance(change, SetArgument):
        return [_set_splice(call, change)]
    return _remove_splices(call, change)


def _call(target: MobjectTarget, index: int) -> CallRef:
    if not 0 <= index < len(target.calls):
        raise EditError(f"call {index} is not part of {target.identity}")
    return target.calls[index]


def _checked_value(value: str) -> bytes:
    text = value.strip()
    if not text or "\n" in text:
        raise EditError("a value must be one line of Python source")
    try:
        ast.parse(text, mode="eval")
    except SyntaxError as error:
        raise EditError(f"{text!r} is not a Python expression") from error
    return text.encode("utf-8")


def _checked_name(name: str) -> str:
    if not name.isidentifier():
        raise EditError(f"{name!r} is not a keyword name")
    return name


def _set_splice(call: CallRef, change: SetArgument) -> tuple[int, int, bytes]:
    value = _checked_value(change.value)
    if change.keyword is not None:
        name = _checked_name(change.keyword)
        existing = next((k for k in call.keywords if k.name == name), None)
        if existing is not None:
            return existing.value_start, existing.value_end, value
        return _append_element(call, f"{name}=".encode() + value)
    if change.position is None:
        raise EditError("a SetArgument needs a keyword or a position")
    count = len(call.args)
    if change.position < count:
        arg = call.args[change.position]
        return arg.start, arg.end, value
    if change.position > count:
        raise EditError(f"position {change.position} skips an argument")
    if call.args:
        last = call.args[-1].end
        return last, last, b", " + value
    at = call.open_paren + 1
    if call.keywords:
        return at, at, value + b", "
    return at, at, value


def _append_element(call: CallRef, snippet: bytes) -> tuple[int, int, bytes]:
    """Insert ``snippet`` after the last argument, or straight after ``(`` if there are none."""
    ends = [arg.end for arg in call.args] + [kw.end for kw in call.keywords]
    if ends:
        last = max(ends)
        return last, last, b", " + snippet
    at = call.open_paren + 1
    return at, at, snippet


def _remove_splices(call: CallRef, change: RemoveArgument) -> list[tuple[int, int, bytes]]:
    if change.keyword is not None:
        name = _checked_name(change.keyword)
        found = next((k for k in call.keywords if k.name == name), None)
        if found is None:
            return []
        span = (found.start, found.end)
    elif change.position is not None:
        if change.position >= len(call.args):
            return []
        arg = call.args[change.position]
        span = (arg.start, arg.end)
    else:
        raise EditError("a RemoveArgument needs a keyword or a position")
    elements = sorted(
        [(arg.start, arg.end) for arg in call.args] + [(kw.start, kw.end) for kw in call.keywords]
    )
    index = elements.index(span)
    # Take the comma with the element: the one before it, or the one after it if it is first.
    if index > 0:
        return [(elements[index - 1][1], span[1], b"")]
    if index + 1 < len(elements):
        return [(span[0], elements[index + 1][0], b"")]
    return [(span[0], span[1], b"")]


def _add_call_splice(target: MobjectTarget, change: AddCall) -> tuple[int, int, bytes]:
    method = change.method
    if not method.isidentifier():
        raise EditError(f"{method!r} is not a method name")
    parts = [_checked_value(arg).decode("utf-8") for arg in change.args]
    for name, value in change.keywords:
        parts.append(f"{_checked_name(name)}={_checked_value(value).decode('utf-8')}")
    text = f".{method}({', '.join(parts)})".encode()
    return target.value_end, target.value_end, text


def _splice(data: bytes, splices: list[tuple[int, int, bytes]]) -> bytes:
    ordered = sorted(splices, key=lambda item: (item[0], item[1]))
    for previous, following in itertools.pairwise(ordered):
        if previous[1] > following[0]:
            raise EditError("two edits overlap")
    result = data
    for start, end, text in reversed(ordered):
        result = result[:start] + text + result[end:]
    return result


def _check_parses(text: str) -> None:
    try:
        ast.parse(text)
    except SyntaxError as error:
        raise EditError(f"the edit would break the source: {error.msg}") from error
