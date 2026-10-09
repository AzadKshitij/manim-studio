"""Which cached blocks survive an edit (pure, stdlib only).

After a reload the filmstrip used to be thrown away.  A block's frames only depend on
the code that ran *before and during* it, so frames stay valid when everything that
executed up to that block is unchanged.  This module decides that conservatively,
by comparing the previous and the new *executed* source text (after timeline patches):

* The edit is the region between the common line prefix and the common line suffix.
* It must lie entirely **inside** the scene's ``construct()`` body, in both texts.
  An edit to ``setup()``, a helper, an import or a class attribute may change state
  that every block depends on, so nothing is reused.
* A block is reusable when the last source line that can have run before it
  (:meth:`ConstructMap.reach_end`) is above the edit.  Inside a loop that is the end of
  the outermost loop, because earlier iterations ran the whole body.  Calls that live
  in helpers, nested functions or lambdas are never reused.
* The new trace must describe the block identically (same timing, name and call site),
  and reuse stops at the first block that does not.
* Anything the source text cannot see (render size, fps, user modules the scene
  imports) is folded into an opaque ``signature``; if it changes nothing is reused.

No hashing is involved: plain comparisons are exact and faster than digesting.  The
module must stay free of Qt, manimlib and moderngl imports.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Collection, Iterator, Sequence

    from manim_studio.timeline_model import TimelineBlock

__all__ = ["ConstructMap", "ReusePlan", "RunSnapshot", "plan_reuse"]

_LOOPS = (ast.For, ast.AsyncFor, ast.While)
_NESTED_SCOPES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)


@dataclass(frozen=True)
class RunSnapshot:
    """Everything that identifies one executed run of a scene."""

    text: str  #: the source that actually ran (timeline patches applied)
    scene_name: str
    blocks: tuple[TimelineBlock, ...]
    call_lines: tuple[int | None, ...]  #: per block, the line of its call in ``text``
    sites: tuple[Any, ...]  #: per block call-site identity (compared with ``==``)
    signature: Any = None  #: render size/fps and user-module fingerprint; compared with ``==``


@dataclass(frozen=True)
class ReusePlan:
    """Indices of cached blocks that are still valid, and why the rest are not."""

    blocks: frozenset[int]
    reason: str

    @property
    def reused(self) -> int:
        return len(self.blocks)


NO_REUSE = ReusePlan(frozenset(), "nothing cached")


def _child_statements(node: ast.AST) -> Iterator[ast.stmt]:
    for child in ast.iter_child_nodes(node):
        if isinstance(child, ast.stmt):
            yield child
        elif isinstance(child, ast.ExceptHandler | ast.match_case):
            yield from child.body


class ConstructMap:
    """The line structure of one scene's ``construct()`` method."""

    def __init__(self, function: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        self._function = function
        self.header_line = function.lineno
        self.end_line = function.end_lineno or function.lineno

    @classmethod
    def parse(cls, source: str, scene_name: str) -> ConstructMap | None:
        try:
            tree = ast.parse(source)
        except (SyntaxError, ValueError):
            return None
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef) and node.name == scene_name:
                for member in node.body:
                    if (
                        isinstance(member, ast.FunctionDef | ast.AsyncFunctionDef)
                        and member.name == "construct"
                    ):
                        return cls(member)
        return None

    def reach_end(self, line: int) -> int | None:
        """Last line that may have executed before the call at ``line`` finished.

        ``None`` means unknown (the call is not straight-line ``construct`` code), so
        the caller must not reuse anything that depends on it.
        """
        statements: Sequence[ast.stmt] = self._function.body
        loop_end = 0
        found: ast.stmt | None = None
        while True:
            match = next(
                (s for s in statements if s.lineno <= line <= (s.end_lineno or s.lineno)), None
            )
            if match is None:
                break
            if isinstance(match, _NESTED_SCOPES):
                return None
            found = match
            if not loop_end and isinstance(match, _LOOPS):
                loop_end = match.end_lineno or match.lineno
            statements = list(_child_statements(match))
        if found is None:
            return None
        if any(
            isinstance(node, ast.Lambda) and node.lineno <= line <= (node.end_lineno or node.lineno)
            for node in ast.walk(found)
        ):
            return None
        return max(loop_end, found.end_lineno or found.lineno)


def _common_affixes(old: Sequence[str], new: Sequence[str]) -> tuple[int, int]:
    """Lengths of the shared line prefix and (non-overlapping) shared line suffix."""
    limit = min(len(old), len(new))
    prefix = 0
    while prefix < limit and old[prefix] == new[prefix]:
        prefix += 1
    suffix = 0
    while suffix < limit - prefix and old[-1 - suffix] == new[-1 - suffix]:
        suffix += 1
    return prefix, suffix


def _same_block(old: RunSnapshot, new: RunSnapshot, index: int) -> bool:
    if index >= len(new.blocks) or index >= len(old.blocks):
        return False
    a, b = old.blocks[index], new.blocks[index]
    return (
        (a.index, a.name, a.start, a.duration) == (b.index, b.name, b.start, b.duration)
        and old.sites[index] == new.sites[index]
        and old.call_lines[index] == new.call_lines[index]
    )


def plan_reuse(old: RunSnapshot | None, new: RunSnapshot, cached: Collection[int]) -> ReusePlan:
    """Blocks of ``old`` whose cached frames are still exact for ``new``.

    ``cached`` holds the indices of blocks the previous crawl completed.
    """
    if old is None or not cached:
        return NO_REUSE
    if old.signature != new.signature or old.scene_name != new.scene_name:
        return ReusePlan(frozenset(), "render settings or imported modules changed")
    old_lines, new_lines = old.text.splitlines(), new.text.splitlines()
    prefix, suffix = _common_affixes(old_lines, new_lines)

    changed = not (prefix == len(old_lines) == len(new_lines))
    old_map = ConstructMap.parse(old.text, old.scene_name)
    new_map = ConstructMap.parse(new.text, new.scene_name)
    if old_map is None or new_map is None:
        return ReusePlan(frozenset(), "construct() not found")
    first_changed = prefix + 1  # 1-based first line that differs (or was inserted)
    if changed:
        old_last = len(old_lines) - suffix
        new_last = len(new_lines) - suffix
        inside = (
            first_changed > old_map.header_line
            and first_changed > new_map.header_line
            and max(old_last, prefix) <= old_map.end_line
            and max(new_last, prefix) <= new_map.end_line
        )
        if not inside:
            return ReusePlan(frozenset(), "edit outside construct()")

    # Reuse stops at the first block whose description differs: later blocks may have
    # shifted in time or depend on state that block produced.
    limit = 0
    while limit < len(old.blocks) and _same_block(old, new, limit):
        limit += 1

    reusable: set[int] = set()
    for index in cached:
        if index >= limit or old.blocks[index].duration <= 0:
            continue
        line = old.call_lines[index]
        if line is None:
            continue
        reach = old_map.reach_end(line)
        if reach is None or (changed and reach >= first_changed):
            continue
        reusable.add(index)
    reason = "all blocks unchanged" if not changed else f"edit at line {first_changed}"
    return ReusePlan(frozenset(reusable), reason)
