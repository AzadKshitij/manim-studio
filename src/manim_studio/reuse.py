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
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Collection, Iterator, Sequence

    from manim_studio.timeline_model import TimelineBlock

__all__ = [
    "ConstructMap",
    "ReusePlan",
    "RunSnapshot",
    "plan_reuse",
    "source_is_time_dependent",
]

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
    #: Nothing in the run can make a later frame depend on how long earlier blocks took
    #: (no updaters, no ``scene.time``, no user modules).  Defaults to the safe answer.
    time_independent: bool = False
    #: Finer, runtime evidence for scenes that *do* use updaters: per block, whether a
    #: time-based (``dt``) updater was alive at its start or end.  ``None`` = unknown.
    dynamic: tuple[bool, ...] | None = None
    #: The scene imports no user modules of its own (needed for the finer rule above).
    no_user_modules: bool = False


@dataclass(frozen=True)
class ReusePlan:
    """Indices of cached blocks that are still valid, and why the rest are not."""

    blocks: frozenset[int]
    reason: str
    #: ``(block index, frame delta)`` for reused blocks whose frames moved in time
    #: (a timing-only edit pushed them along the timeline); absent means no move.
    shifts: tuple[tuple[int, int], ...] = ()

    @property
    def reused(self) -> int:
        return len(self.blocks)

    def shift_of(self, index: int) -> int:
        """How many frames block ``index``'s cached frames moved (``0`` if they did not)."""
        return dict(self.shifts).get(index, 0)


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


#: Anything that lets a frame depend on elapsed time rather than on code order alone.
_TIME_DEPENDENT = re.compile(
    r"add_updater|always_redraw|\balways\b|f_always|clear_updaters|\.time\b|get_time|\bdt\b"
)


def source_is_time_dependent(text: str) -> bool:
    """True when the scene source mentions updaters or the scene clock.

    Animations are pure functions of their alpha, so without updaters a block looks the
    same however long the blocks before it lasted.  This is a conservative text check:
    a false positive only costs a re-render, a false negative would show a wrong frame.
    """
    return _TIME_DEPENDENT.search(text) is not None


def _is_self_call(node: ast.Call, method: str) -> bool:
    func = node.func
    return (
        isinstance(func, ast.Attribute)
        and func.attr == method
        and isinstance(func.value, ast.Name)
        and func.value.id == "self"
    )


def _is_number(node: ast.expr) -> bool:
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub | ast.UAdd):
        node = node.operand
    return (
        isinstance(node, ast.Constant)
        and isinstance(node.value, int | float)
        and not isinstance(node.value, bool)
    )


class _DurationStripper(ast.NodeTransformer):
    """Removes literal play/wait durations so two sources can be compared modulo timing."""

    def visit_Call(self, node: ast.Call) -> ast.AST:
        if _is_self_call(node, "play"):
            # ``run_time=`` on the play call and on the animations built inside it.
            for inner in ast.walk(node):
                if isinstance(inner, ast.Call):
                    inner.keywords = [k for k in inner.keywords if k.arg != "run_time"]
        elif _is_self_call(node, "wait"):
            node.keywords = [k for k in node.keywords if k.arg != "duration"]
            if node.args and _is_number(node.args[0]):
                node.args = node.args[1:]
        self.generic_visit(node)
        return node


def _reads_clock(tree: ast.AST) -> bool:
    """Does the scene look at absolute time (``self.time``, ``get_time``, ``import time``)?"""
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr in ("time", "get_time"):
            return True
        if isinstance(node, ast.Import) and any(a.name.split(".")[0] == "time" for a in node.names):
            return True
        if isinstance(node, ast.ImportFrom) and (node.module or "").split(".")[0] == "time":
            return True
    return False


def _timing_signature(text: str) -> tuple[str, bool] | None:
    """``(ast dump without literal durations, reads_clock)``; ``None`` if it won't parse."""
    try:
        tree = ast.parse(text)
    except (SyntaxError, ValueError):
        return None
    return ast.dump(_DurationStripper().visit(tree)), _reads_clock(tree)


def _differs_only_in_timing(old_text: str, new_text: str) -> bool:
    """True when the two sources are identical once literal durations are removed."""
    old_sig, new_sig = _timing_signature(old_text), _timing_signature(new_text)
    return old_sig is not None and new_sig is not None and old_sig[0] == new_sig[0]


def _clock_free(old: RunSnapshot, new: RunSnapshot) -> bool:
    """Neither source reads the clock (checked on the AST, so comments do not count)."""
    signatures = (_timing_signature(old.text), _timing_signature(new.text))
    return all(sig is not None and not sig[1] for sig in signatures)


def _plan_timing_reuse(
    old: RunSnapshot, new: RunSnapshot, cached: Collection[int], fps: int
) -> ReusePlan | None:
    """Reuse for an edit that only changed block durations (shifting later blocks).

    Applies when both runs are time-independent and block ``i`` is the same call in the
    same place with the same name; a block whose own duration changed is re-rendered,
    every other cached block keeps its frames, moved by however far it was pushed.
    ``None`` means the runs differ in more than timing.
    """
    strict = old.time_independent and new.time_independent
    if not strict and not (
        # Scenes with updaters: judged on what actually ran.  Only ``dt`` updaters make
        # state depend on a block's duration, so ``always_redraw`` scenes still qualify.
        old.dynamic is not None
        and new.dynamic is not None
        and old.no_user_modules
        and new.no_user_modules
        and _clock_free(old, new)
    ):
        return None
    if len(old.blocks) != len(new.blocks):
        return None
    for index, (a, b) in enumerate(zip(old.blocks, new.blocks, strict=True)):
        if (a.name, old.sites[index], old.call_lines[index]) != (
            b.name,
            new.sites[index],
            new.call_lines[index],
        ):
            return None
    changed = [
        index
        for index, (a, b) in enumerate(zip(old.blocks, new.blocks, strict=True))
        if abs(a.duration - b.duration) > 1e-9
    ]
    first = changed[0] if changed else len(old.blocks)
    # A retimed block with a ``dt`` updater alive leaves a different state behind it, so
    # blocks after it cannot be reused (blocks before it are untouched either way).
    shifts_state = not strict and any(
        (old.dynamic or ())[index] or (new.dynamic or ())[index] for index in changed
    )
    reusable: set[int] = set()
    shifts: list[tuple[int, int]] = []
    for index in cached:
        if index >= len(old.blocks):
            continue
        if shifts_state and index > first:
            continue
        a, b = old.blocks[index], new.blocks[index]
        if a.duration <= 0 or abs(a.duration - b.duration) > 1e-9:
            continue
        old_start = round(a.start * fps)
        new_start = round(b.start * fps)
        old_len = max(round(a.end * fps), old_start + 1) - old_start
        new_len = max(round(b.end * fps), new_start + 1) - new_start
        if old_len != new_len:
            continue  # rounding gave the block a different number of frames
        reusable.add(index)
        if new_start != old_start:
            shifts.append((index, new_start - old_start))
    reason = (
        "timing-only edit"
        if not shifts_state
        else "timing-only edit before a time-based updater: later blocks re-rendered"
    )
    return ReusePlan(frozenset(reusable), reason, tuple(sorted(shifts)))


def plan_reuse(
    old: RunSnapshot | None,
    new: RunSnapshot,
    cached: Collection[int],
    *,
    timing_only: bool = False,
    fps: int = 30,
) -> ReusePlan:
    """Blocks of ``old`` whose cached frames are still exact for ``new``.

    ``cached`` holds the indices of blocks the previous crawl completed.  With
    ``timing_only`` (the caller knows the edit only changed durations, for example
    timeline retimes) later blocks are reused too, moved to their new frame indices.
    """
    if old is None or not cached:
        return NO_REUSE
    if old.signature != new.signature or old.scene_name != new.scene_name:
        return ReusePlan(frozenset(), "render settings or imported modules changed")
    # Typing a new duration into the source is the same kind of edit as a timeline retime.
    timing_only = timing_only or (
        old.text != new.text and _differs_only_in_timing(old.text, new.text)
    )
    if timing_only:
        plan = _plan_timing_reuse(old, new, cached, fps)
        if plan is not None:
            return plan
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
