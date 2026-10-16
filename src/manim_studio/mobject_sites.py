"""Mobject targets in scene source: a constructor binding and the calls that follow it (ADR 0012).

A target is ``name = Ctor(...)`` (optionally with chained calls) plus the later statements in the
same block that call ``name.method(...)``.  Every call records the argument and keyword spans the
inspector edits, as UTF-8 byte offsets into the buffer (``ast`` columns are bytes, and Scintilla
works in bytes; see :mod:`manim_studio.color_sites`).

Only plain assignments are targets.  Constructors inside ``self.play(...)`` or ``.animate``
chains are not: editing them would change the ``play()`` fingerprint and orphan timeline edits
(ADR 0007).  Identity is ``scope:var#ordinal``; an edit that removes the binding makes the target
vanish, and it is never re-anchored onto a different call.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from typing import Any

__all__ = [
    "ArgRef",
    "CallRef",
    "KeywordRef",
    "MobjectTarget",
    "find_mobject_targets",
]


@dataclass(frozen=True, slots=True)
class ArgRef:
    """A positional argument: its index, byte span and source text."""

    index: int
    start: int
    end: int
    text: str
    literal: bool  #: the value is a Python literal; ``value`` holds it
    value: Any = None


@dataclass(frozen=True, slots=True)
class KeywordRef:
    """A keyword argument; ``start`` is the keyword name, ``end`` is the end of its value."""

    name: str
    start: int
    end: int
    value_start: int
    value_end: int
    text: str  #: the value's source text
    literal: bool
    value: Any = None


@dataclass(frozen=True, slots=True)
class CallRef:
    """One call in a target's chain.  The constructor call is first, setters follow in order."""

    method: str  #: the class name for the constructor, the method name otherwise
    constructor: bool
    start: int
    end: int
    open_paren: int  #: byte offset of ``(``
    args: tuple[ArgRef, ...]
    keywords: tuple[KeywordRef, ...]
    line: int  #: 1-based line the call starts on


@dataclass(frozen=True, slots=True)
class MobjectTarget:
    """A mobject bound to a name in one block, with the calls that configure it."""

    var: str
    cls: str
    scope: str  #: enclosing ``Class.function`` path, empty at module level
    ordinal: int  #: nth binding of ``var`` in ``scope``
    value_end: int  #: byte offset where the assigned expression ends (chained calls go here)
    calls: tuple[CallRef, ...]
    lines: tuple[int, ...]  #: 1-based lines that carry a gear for this target

    @property
    def identity(self) -> str:
        return f"{self.scope}:{self.var}#{self.ordinal}"


class _Draft:
    """A target under construction, frozen into :class:`MobjectTarget` at the end."""

    def __init__(self, var: str, cls: str, scope: str, ordinal: int, value_end: int) -> None:
        self.var = var
        self.cls = cls
        self.scope = scope
        self.ordinal = ordinal
        self.value_end = value_end
        self.calls: list[CallRef] = []
        self.lines: list[int] = []

    def freeze(self) -> MobjectTarget:
        return MobjectTarget(
            var=self.var,
            cls=self.cls,
            scope=self.scope,
            ordinal=self.ordinal,
            value_end=self.value_end,
            calls=tuple(self.calls),
            lines=tuple(sorted(set(self.lines))),
        )


class _Source:
    """Byte-level helpers for one source text."""

    def __init__(self, source: str) -> None:
        self.data = source.encode("utf-8")
        self._line_starts = [0]
        for index, byte in enumerate(self.data):
            if byte == 0x0A:
                self._line_starts.append(index + 1)

    def offset(self, line: int, col: int) -> int:
        return self._line_starts[line - 1] + col

    def span(self, node: ast.expr | ast.stmt | ast.keyword) -> tuple[int, int]:
        return (
            self.offset(node.lineno, node.col_offset),
            self.offset(node.end_lineno or node.lineno, node.end_col_offset or node.col_offset),
        )

    def text(self, start: int, end: int) -> str:
        return self.data[start:end].decode("utf-8")

    def open_paren(self, after: int) -> int:
        index = self.data.find(b"(", after)
        return index


def _literal(node: ast.expr) -> tuple[bool, Any]:
    try:
        return True, ast.literal_eval(node)
    except (ValueError, TypeError, SyntaxError, MemoryError, RecursionError):
        return False, None


def _call_ref(call: ast.Call, source: _Source, *, constructor: bool) -> CallRef:
    start, end = source.span(call)
    _, func_end = source.span(call.func)
    args: list[ArgRef] = []
    for index, arg in enumerate(call.args):
        arg_start, arg_end = source.span(arg)
        literal, value = _literal(arg)
        args.append(
            ArgRef(index, arg_start, arg_end, source.text(arg_start, arg_end), literal, value)
        )
    keywords: list[KeywordRef] = []
    for keyword in call.keywords:
        if keyword.arg is None:  # ``**mapping``: not something one value edits
            continue
        kw_start, kw_end = source.span(keyword)
        value_start, value_end = source.span(keyword.value)
        literal, value = _literal(keyword.value)
        keywords.append(
            KeywordRef(
                name=keyword.arg,
                start=kw_start,
                end=kw_end,
                value_start=value_start,
                value_end=value_end,
                text=source.text(value_start, value_end),
                literal=literal,
                value=value,
            )
        )
    method = call.func.id if isinstance(call.func, ast.Name) else ""
    if not constructor:
        method = call.func.attr if isinstance(call.func, ast.Attribute) else ""
    return CallRef(
        method=method,
        constructor=constructor,
        start=start,
        end=end,
        open_paren=source.open_paren(func_end),
        args=tuple(args),
        keywords=tuple(keywords),
        line=call.lineno,
    )


def _chain(expr: ast.expr) -> tuple[str | None, list[ast.Call]]:
    """The root name of a call chain and its calls, innermost first.

    ``Circle(a).set_fill(b)`` gives ``("Circle", [Circle(a), set_fill(b)])``.  Anything that is
    not a chain of calls on a name (subscripts, attribute reads) yields ``(None, [])``.
    """
    calls: list[ast.Call] = []
    node: ast.expr = expr
    while True:
        if isinstance(node, ast.Call):
            calls.append(node)
            node = node.func
        elif isinstance(node, ast.Attribute):
            node = node.value
        elif isinstance(node, ast.Name):
            return node.id, list(reversed(calls))
        else:
            return None, []


def _constructor_name(call: ast.Call) -> str | None:
    """The class a constructor call names (``Circle`` in ``Circle(...)``), else ``None``."""
    if isinstance(call.func, ast.Name) and call.func.id[:1].isupper():
        return call.func.id
    return None


def _is_setter(call: ast.Call) -> bool:
    return isinstance(call.func, ast.Attribute)


def _nested_bodies(stmt: ast.stmt) -> list[list[ast.stmt]]:
    bodies: list[list[ast.stmt]] = []
    for name in ("body", "orelse", "finalbody"):
        block = getattr(stmt, name, None)
        if isinstance(block, list) and block and isinstance(block[0], ast.stmt):
            bodies.append(block)
    for handler in getattr(stmt, "handlers", []):
        bodies.append(handler.body)
    return bodies


class _Scanner:
    def __init__(self, source: _Source) -> None:
        self.source = source
        self.drafts: list[_Draft] = []
        self._ordinals: dict[tuple[str, str], int] = {}

    def scan(self, body: list[ast.stmt], scope: str) -> None:
        bound: dict[str, _Draft] = {}  # the latest target per name, within this block
        for stmt in body:
            if isinstance(stmt, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
                self.scan(stmt.body, f"{scope}.{stmt.name}" if scope else stmt.name)
                continue
            if isinstance(stmt, ast.Assign):
                self._assignment(stmt, scope, bound)
            elif isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Call):
                self._setter_statement(stmt, bound)
            for block in _nested_bodies(stmt):
                self.scan(block, scope)

    def _assignment(self, stmt: ast.Assign, scope: str, bound: dict[str, _Draft]) -> None:
        if len(stmt.targets) != 1 or not isinstance(stmt.targets[0], ast.Name):
            return
        var = stmt.targets[0].id
        root, calls = _chain(stmt.value)
        if root is None or not calls or _constructor_name(calls[0]) != root:
            # Rebinding the name to something else ends the previous target's chain.
            bound.pop(var, None)
            return
        key = (scope, var)
        ordinal = self._ordinals.get(key, 0)
        self._ordinals[key] = ordinal + 1
        _, value_end = self.source.span(stmt.value)
        draft = _Draft(var, root, scope, ordinal, value_end)
        draft.lines.append(stmt.lineno)
        for position, call in enumerate(calls):
            if position > 0 and not _is_setter(call):
                break
            draft.calls.append(_call_ref(call, self.source, constructor=position == 0))
        self.drafts.append(draft)
        bound[var] = draft

    def _setter_statement(self, stmt: ast.Expr, bound: dict[str, _Draft]) -> None:
        if not isinstance(stmt.value, ast.Call):
            return
        root, calls = _chain(stmt.value)
        draft = bound.get(root or "")
        if draft is None or not calls or not all(_is_setter(call) for call in calls):
            return
        for call in calls:
            draft.calls.append(_call_ref(call, self.source, constructor=False))
        draft.lines.append(stmt.lineno)


def find_mobject_targets(source: str) -> tuple[MobjectTarget, ...]:
    """Every mobject target in ``source``, in source order.  Raises :class:`SyntaxError`."""
    tree = ast.parse(source)
    scanner = _Scanner(_Source(source))
    scanner.scan(tree.body, "")
    return tuple(draft.freeze() for draft in scanner.drafts)
