"""Static index of ``self.play()`` / ``self.wait()`` call sites (pure, stdlib only).

The timeline needs a *stable identity* for each call in a scene so edits made on the
timeline can be re-found after the source changes.  Block indices are useless for
that (inserting one ``play()`` shifts every later index), so every call gets a
**fingerprint**: the enclosing function, the call kind and a hash of the call with its
*duration* removed, plus an ordinal that separates textually identical calls.

Because the duration is excluded from the hash, retiming a call (by hand or through
the timeline) never changes its identity; editing its animations does.

Spans are character based (``ast`` reports UTF-8 byte columns, which are converted)
so the patch engine can splice text directly.  This module must stay free of Qt,
manimlib and moderngl imports.
"""

from __future__ import annotations

import ast
import hashlib
import re
from dataclasses import dataclass
from typing import Literal

__all__ = [
    "CallSite",
    "DurationArg",
    "SiteIndex",
    "Span",
    "index_source",
]

_LINE_SPLIT = re.compile(r"(?<=\r\n)|(?<=\n)|(?<=\r)(?!\n)")
_METHODS = ("play", "wait")


@dataclass(frozen=True, slots=True)
class Span:
    """A source range: 1-based lines, 0-based *character* columns, end exclusive."""

    line: int
    col: int
    end_line: int
    end_col: int

    @property
    def start(self) -> tuple[int, int]:
        return (self.line, self.col)

    @property
    def end(self) -> tuple[int, int]:
        return (self.end_line, self.end_col)


@dataclass(frozen=True, slots=True)
class DurationArg:
    """How a call spells its duration: ``run_time=`` for play, first arg for wait."""

    kind: Literal["literal", "expr", "absent"]
    value: float | None = None
    span: Span | None = None  #: the value expression (not the ``run_time=`` keyword)

    @property
    def editable(self) -> bool:
        """Literals can be rewritten in place; absent ones can be appended."""
        return self.kind in ("literal", "absent")


@dataclass(frozen=True, slots=True)
class CallSite:
    """One ``self.play(...)`` or ``self.wait(...)`` call in the source."""

    site_id: str
    kind: Literal["play", "wait"]
    function: str
    call: Span  #: the whole ``self.play(...)`` expression
    statement: Span | None  #: the ``Expr`` statement when the call *is* the statement
    deletable: bool  #: statement sits alone on its lines, so it can be replaced by ``pass``
    duration: DurationArg
    args_end: tuple[int, int]  #: where an appended argument goes (after the last arg)
    has_args: bool
    has_other_args: bool  #: wait: anything besides the duration; play: always has args
    unsafe_kwargs: bool  #: ``**spread`` present, so appending a keyword could duplicate it
    in_loop: bool
    lookup_key: tuple[int, int]  #: ``(line, UTF-8 byte column)`` of the call start
    label: str  #: the call's source text, trimmed, for UI display

    @property
    def line(self) -> int:
        return self.call.line


@dataclass(frozen=True, slots=True)
class SiteIndex:
    """Every play/wait call of one source text, in source order."""

    sites: tuple[CallSite, ...]

    def by_id(self, site_id: str) -> CallSite | None:
        for site in self.sites:
            if site.site_id == site_id:
                return site
        return None

    def at(self, line: int, byte_col: int) -> CallSite | None:
        """The site whose call expression starts at ``(line, byte_col)``."""
        for site in self.sites:
            if site.lookup_key == (line, byte_col):
                return site
        return None

    def at_line(self, line: int) -> CallSite | None:
        """The innermost site whose call spans ``line`` (first one starting there wins)."""
        best: CallSite | None = None
        for site in self.sites:
            if site.call.line <= line <= site.call.end_line and (
                best is None or site.call.line >= best.call.line
            ):
                best = site
        return best

    def ids(self) -> frozenset[str]:
        return frozenset(site.site_id for site in self.sites)


def split_lines(source: str) -> list[str]:
    """Split like the Python tokenizer: only ``\\n``, ``\\r\\n`` and ``\\r`` end a line."""
    parts = [part for part in _LINE_SPLIT.split(source) if part]
    return parts or [""]


class _Locator:
    """Converts ``ast`` (line, UTF-8 byte col) positions into character spans."""

    def __init__(self, source: str) -> None:
        self.lines = split_lines(source)

    def col(self, line: int, byte_col: int) -> int:
        if not 1 <= line <= len(self.lines):
            return byte_col
        raw = self.lines[line - 1].encode("utf-8")
        return len(raw[:byte_col].decode("utf-8", errors="ignore"))

    def span(self, node: ast.expr | ast.stmt | ast.keyword) -> Span:
        end_line = node.end_lineno if node.end_lineno is not None else node.lineno
        end_off = node.end_col_offset if node.end_col_offset is not None else node.col_offset
        return Span(
            node.lineno,
            self.col(node.lineno, node.col_offset),
            end_line,
            self.col(end_line, end_off),
        )

    def text(self, span: Span) -> str:
        if span.line == span.end_line:
            return self.lines[span.line - 1][span.col : span.end_col]
        first = self.lines[span.line - 1][span.col :]
        middle = self.lines[span.line : span.end_line - 1]
        last = self.lines[span.end_line - 1][: span.end_col]
        return first + "".join(middle) + last


def _numeric_literal(node: ast.expr) -> float | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        if isinstance(node.value, bool):
            return None
        return float(node.value)
    if (
        isinstance(node, ast.UnaryOp)
        and isinstance(node.op, (ast.USub, ast.UAdd))
        and isinstance(node.operand, ast.Constant)
        and isinstance(node.operand.value, (int, float))
        and not isinstance(node.operand.value, bool)
    ):
        value = float(node.operand.value)
        return -value if isinstance(node.op, ast.USub) else value
    return None


def _method_name(call: ast.Call) -> Literal["play", "wait"] | None:
    func = call.func
    if (
        isinstance(func, ast.Attribute)
        and func.attr in _METHODS
        and isinstance(func.value, ast.Name)
        and func.value.id == "self"
    ):
        return func.attr  # type: ignore[return-value]
    return None


def _split_duration(
    call: ast.Call, kind: Literal["play", "wait"]
) -> tuple[ast.expr | None, ast.keyword | None, int | None]:
    """Locate the duration expression: ``(value_node, keyword_node, positional_index)``."""
    if kind == "play":
        for keyword in call.keywords:
            if keyword.arg == "run_time":
                return keyword.value, keyword, None
        return None, None, None
    for keyword in call.keywords:
        if keyword.arg == "duration":
            return keyword.value, keyword, None
    if call.args and not isinstance(call.args[0], ast.Starred):
        return call.args[0], None, 0
    return None, None, None


def _fingerprint_payload(
    call: ast.Call, keyword: ast.keyword | None, positional: int | None
) -> str:
    """``ast.dump`` of the call with the duration removed (stable under retiming)."""
    args = [arg for i, arg in enumerate(call.args) if i != positional]
    keywords = [kw for kw in call.keywords if kw is not keyword]
    stripped = ast.Call(func=call.func, args=args, keywords=keywords)
    return ast.dump(stripped, annotate_fields=True, include_attributes=False)


class _Collector(ast.NodeVisitor):
    def __init__(self, locator: _Locator) -> None:
        self.locator = locator
        self.sites: list[CallSite] = []
        self._scope: list[str] = []
        self._loop_depth: list[int] = [0]
        self._statement_nodes: dict[int, ast.Expr] = {}
        self._ordinals: dict[tuple[str, str, str], int] = {}

    # -- scopes ------------------------------------------------------------

    def _function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        self._scope.append(node.name)
        self._loop_depth.append(0)
        self.generic_visit(node)
        self._loop_depth.pop()
        self._scope.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._function(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._function(node)

    def visit_Lambda(self, node: ast.Lambda) -> None:
        self._loop_depth.append(0)
        self.generic_visit(node)
        self._loop_depth.pop()

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self._scope.append(node.name)
        self.generic_visit(node)
        self._scope.pop()

    def _loop(self, node: ast.AST) -> None:
        self._loop_depth[-1] += 1
        self.generic_visit(node)
        self._loop_depth[-1] -= 1

    visit_For = _loop
    visit_AsyncFor = _loop
    visit_While = _loop
    visit_ListComp = _loop
    visit_SetComp = _loop
    visit_DictComp = _loop
    visit_GeneratorExp = _loop

    # -- statements and calls ------------------------------------------------

    def visit_Expr(self, node: ast.Expr) -> None:
        if isinstance(node.value, ast.Call):
            self._statement_nodes[id(node.value)] = node
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        kind = _method_name(node)
        if kind is not None:
            self.sites.append(self._site(node, kind))
        self.generic_visit(node)

    def _site(self, node: ast.Call, kind: Literal["play", "wait"]) -> CallSite:
        locator = self.locator
        call_span = locator.span(node)
        value, keyword, positional = _split_duration(node, kind)
        if value is None:
            duration = DurationArg("absent")
        else:
            literal = _numeric_literal(value)
            duration = DurationArg(
                "literal" if literal is not None else "expr", literal, locator.span(value)
            )
        function = ".".join(self._scope) or "<module>"
        payload = _fingerprint_payload(node, keyword, positional)
        digest = hashlib.sha1(payload.encode("utf-8")).hexdigest()[:10]
        key = (function, kind, digest)
        ordinal = self._ordinals.get(key, 0)
        self._ordinals[key] = ordinal + 1
        site_id = f"{function}:{kind}:{digest}:{ordinal}"

        pieces: list[ast.expr | ast.keyword] = [*node.args, *node.keywords]
        if pieces:
            last = max(pieces, key=lambda item: (item.end_lineno or 0, item.end_col_offset or 0))
            end_line = last.end_lineno or node.lineno
            args_end = (end_line, locator.col(end_line, last.end_col_offset or 0))
        else:
            # Right after the opening paren, i.e. one column before the closing one.
            args_end = (call_span.end_line, call_span.end_col - 1)

        statement_node = self._statement_nodes.get(id(node))
        statement: Span | None = None
        deletable = False
        if statement_node is not None:
            statement = locator.span(statement_node)
            deletable = self._alone_on_lines(statement)

        other = len(node.args) + len(node.keywords) - (0 if value is None else 1)
        text = " ".join(locator.text(call_span).split())
        return CallSite(
            site_id=site_id,
            kind=kind,
            function=function,
            call=call_span,
            statement=statement,
            deletable=deletable,
            duration=duration,
            args_end=args_end,
            has_args=bool(pieces),
            has_other_args=other > 0,
            unsafe_kwargs=any(kw.arg is None for kw in node.keywords),
            in_loop=self._loop_depth[-1] > 0,
            lookup_key=(node.lineno, node.col_offset),
            label=text if len(text) <= 80 else text[:77] + "...",
        )

    def _alone_on_lines(self, span: Span) -> bool:
        lines = self.locator.lines
        before = lines[span.line - 1][: span.col]
        after = lines[span.end_line - 1][span.end_col :].strip()
        return not before.strip() and (not after or after.startswith("#"))


def index_source(source: str) -> SiteIndex:
    """Index every ``self.play``/``self.wait`` call in ``source``.

    Raises :class:`SyntaxError` if the source does not parse.
    """
    tree = ast.parse(source)
    collector = _Collector(_Locator(source))
    collector.visit(tree)
    collector.sites.sort(key=lambda site: site.call.start)
    return SiteIndex(tuple(collector.sites))
