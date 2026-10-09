"""Anchored timeline edits and the text-splice engine that applies them (pure).

A *patch* is an edit made on the timeline: "this call lasts 2.5 s", "drop this call".
Patches never refer to block indices or line numbers.  They point at a call **site**
by the fingerprint from :mod:`manim_studio.site_index`, so they survive unrelated code
edits and are reported as *orphaned* (never silently misapplied) when their site can
no longer be found.

:func:`apply_patches` rewrites the source text by splicing exact AST spans, so comments
and formatting are preserved and "Apply to source" yields a minimal diff.  The same
text is what the preview, scrub and export all execute, so they cannot disagree.

Per-iteration edits (``occurrence`` set) cannot be written as plain code; they are
compiled into a call to the ``__studio__.dur`` runtime helper (see
:mod:`manim_studio.patch_runtime`) and are reported as ``unbakeable`` when the caller
wants source text that runs without the helper.

This module must stay free of Qt, manimlib and moderngl imports.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from manim_studio.site_index import CallSite, SiteIndex, index_source, split_lines

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence

__all__ = [
    "RUNTIME_NAME",
    "Anchor",
    "Delete",
    "Orphan",
    "Patch",
    "PatchError",
    "PatchResult",
    "SetDuration",
    "apply_patches",
    "format_seconds",
    "parse_patch",
    "with_patch",
]

#: Name the executing module must expose for per-iteration overrides.
RUNTIME_NAME = "__studio__"


class PatchError(ValueError):
    """Raised when a patch payload is malformed."""


@dataclass(frozen=True, slots=True)
class Anchor:
    """A call site, optionally narrowed to one loop iteration (0-based)."""

    site_id: str
    occurrence: int | None = None

    def __post_init__(self) -> None:
        if not self.site_id:
            raise PatchError("anchor site_id must not be empty")
        if self.occurrence is not None and self.occurrence < 0:
            raise PatchError(f"occurrence must be >= 0, got {self.occurrence}")

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {"site": self.site_id}
        if self.occurrence is not None:
            data["occurrence"] = self.occurrence
        return data

    @classmethod
    def from_dict(cls, data: Any) -> Anchor:
        if not isinstance(data, dict) or not isinstance(data.get("site"), str):
            raise PatchError(f"anchor needs a string 'site', got {data!r}")
        occurrence = data.get("occurrence")
        if occurrence is not None and (
            not isinstance(occurrence, int) or isinstance(occurrence, bool)
        ):
            raise PatchError(f"occurrence must be an integer, got {occurrence!r}")
        return cls(data["site"], occurrence)


@dataclass(frozen=True, slots=True)
class SetDuration:
    """Make a call last ``seconds``: ``run_time=`` for play, the duration for wait."""

    anchor: Anchor
    seconds: float

    def __post_init__(self) -> None:
        if not math.isfinite(self.seconds) or self.seconds < 0:
            raise PatchError(f"duration must be finite and >= 0, got {self.seconds!r}")

    op = "set_duration"

    def to_dict(self) -> dict[str, Any]:
        return {"op": self.op, **self.anchor.to_dict(), "seconds": self.seconds}


@dataclass(frozen=True, slots=True)
class Delete:
    """Remove a call (replaced by ``pass`` so line numbers do not move)."""

    anchor: Anchor

    op = "delete"

    def to_dict(self) -> dict[str, Any]:
        return {"op": self.op, **self.anchor.to_dict()}


Patch = SetDuration | Delete


def parse_patch(data: Any) -> Patch:
    """Strictly parse one serialized patch."""
    if not isinstance(data, dict):
        raise PatchError(f"patch must be an object, got {type(data).__name__}")
    anchor = Anchor.from_dict(data)
    op = data.get("op")
    if op == SetDuration.op:
        seconds = data.get("seconds")
        if not isinstance(seconds, (int, float)) or isinstance(seconds, bool):
            raise PatchError(f"set_duration needs numeric 'seconds', got {seconds!r}")
        return SetDuration(anchor, float(seconds))
    if op == Delete.op:
        return Delete(anchor)
    raise PatchError(f"unknown patch op {op!r}")


def with_patch(patches: Sequence[Patch], new: Patch) -> tuple[Patch, ...]:
    """Return ``patches`` with ``new`` added, replacing what it supersedes.

    A later duration for the same anchor replaces the earlier one.  A delete of a
    whole site replaces every duration edit of that site (and any earlier delete).
    """
    kept: list[Patch] = []
    for existing in patches:
        if isinstance(new, SetDuration) and isinstance(existing, SetDuration):
            if existing.anchor == new.anchor:
                continue
        elif isinstance(new, Delete) and existing.anchor.site_id == new.anchor.site_id:
            continue
        kept.append(existing)
    kept.append(new)
    return tuple(kept)


def format_seconds(seconds: float) -> str:
    """Format a duration as a Python literal (``0.5``, ``1.0``, ``0.25``)."""
    text = f"{max(0.0, float(seconds)):.3f}".rstrip("0")
    if text.endswith("."):
        text += "0"
    return text


@dataclass(frozen=True, slots=True)
class Orphan:
    """A patch that could not be applied, with a human-readable reason."""

    patch: Patch
    reason: str


@dataclass(frozen=True, slots=True)
class PatchResult:
    """Outcome of :func:`apply_patches`."""

    text: str
    applied: tuple[Patch, ...]
    orphaned: tuple[Orphan, ...]
    index: SiteIndex  #: index of the *original* source the patches were resolved against

    @property
    def changed(self) -> bool:
        return bool(self.applied)


@dataclass(slots=True)
class _SiteEdits:
    site: CallSite
    whole: SetDuration | None = None
    iterations: dict[int, SetDuration] | None = None
    delete: Delete | None = None


@dataclass(frozen=True, slots=True)
class _Splice:
    start: int
    end: int
    text: str


def apply_patches(
    source: str,
    patches: Iterable[Patch],
    *,
    runtime: bool = True,
    index: SiteIndex | None = None,
) -> PatchResult:
    """Apply ``patches`` to ``source`` and report what could not be applied.

    ``runtime=False`` asks for source that runs without the ``__studio__`` helper:
    per-iteration edits are then reported as orphaned ("unbakeable") instead of
    being compiled into helper calls.  Raises :class:`SyntaxError` if ``source``
    does not parse.
    """
    index = index if index is not None else index_source(source)
    offsets = _line_offsets(source)
    applied: list[Patch] = []
    orphaned: list[Orphan] = []
    edits: dict[str, _SiteEdits] = {}

    for patch in patches:
        site = index.by_id(patch.anchor.site_id)
        if site is None:
            orphaned.append(Orphan(patch, "the call this edit was made on no longer exists"))
            continue
        reason = _unsupported(patch, site, runtime)
        if reason is not None:
            orphaned.append(Orphan(patch, reason))
            continue
        entry = edits.setdefault(site.site_id, _SiteEdits(site))
        if isinstance(patch, Delete):
            entry.delete = patch
        elif patch.anchor.occurrence is None:
            entry.whole = patch
        else:
            if entry.iterations is None:
                entry.iterations = {}
            entry.iterations[patch.anchor.occurrence] = patch

    splices: list[_Splice] = []
    for entry in edits.values():
        splices.extend(_site_splices(source, offsets, entry, applied, orphaned))

    return PatchResult(
        text=_splice(source, splices),
        applied=tuple(applied),
        orphaned=tuple(orphaned),
        index=index,
    )


def _unsupported(patch: Patch, site: CallSite, runtime: bool) -> str | None:
    occurrence = patch.anchor.occurrence
    if isinstance(patch, Delete):
        if occurrence is not None:
            return "deleting a single loop iteration is not supported yet"
        if not site.deletable or site.statement is None:
            return "this call is not a statement on its own line, so it cannot be deleted"
        return None
    if site.duration.kind == "absent" and site.unsafe_kwargs:
        return "this call passes **kwargs, so a duration cannot be added safely"
    if occurrence is not None and not runtime:
        return "per-iteration edits cannot be written to source; edit all iterations instead"
    return None


def _site_splices(
    source: str,
    offsets: list[int],
    entry: _SiteEdits,
    applied: list[Patch],
    orphaned: list[Orphan],
) -> list[_Splice]:
    site = entry.site
    if entry.delete is not None:
        statement = site.statement
        assert statement is not None  # guaranteed by _unsupported
        start = _offset(offsets, statement.line, statement.col)
        end = _offset(offsets, statement.end_line, statement.end_col)
        padding = "\n" * (statement.end_line - statement.line)
        applied.append(entry.delete)  # supersedes any duration edit of the same site
        return [_Splice(start, end, "pass" + padding)]

    iterations = entry.iterations or {}
    if iterations:
        default = _iteration_default(source, offsets, entry)
        if default is None:
            for patch in iterations.values():
                orphaned.append(
                    Orphan(patch, "set a duration for all iterations first (none is written yet)")
                )
            return []
        overrides = ", ".join(
            f"{occurrence}: {format_seconds(patch.seconds)}"
            for occurrence, patch in sorted(iterations.items())
        )
        value = f"{RUNTIME_NAME}.dur({site.site_id!r}, {{{overrides}}}, {default})"
        applied.extend(iterations.values())
        if entry.whole is not None:
            applied.append(entry.whole)
    elif entry.whole is not None:
        value = format_seconds(entry.whole.seconds)
        applied.append(entry.whole)
    else:  # pragma: no cover - entries always carry at least one patch
        return []
    return [_duration_splice(offsets, site, value)]


def _iteration_default(source: str, offsets: list[int], entry: _SiteEdits) -> str | None:
    """Source text of the value a non-overridden iteration should keep using."""
    if entry.whole is not None:
        return format_seconds(entry.whole.seconds)
    span = entry.site.duration.span
    if span is None:
        return None
    start = _offset(offsets, span.line, span.col)
    end = _offset(offsets, span.end_line, span.end_col)
    return source[start:end]


def _duration_splice(offsets: list[int], site: CallSite, value: str) -> _Splice:
    span = site.duration.span
    if span is not None:
        return _Splice(
            _offset(offsets, span.line, span.col),
            _offset(offsets, span.end_line, span.end_col),
            value,
        )
    at = _offset(offsets, *site.args_end)
    if site.kind == "play":
        text = f", run_time={value}" if site.has_args else f"run_time={value}"
    else:
        text = f", duration={value}" if site.has_other_args else value
    return _Splice(at, at, text)


def _line_offsets(source: str) -> list[int]:
    offsets = [0]
    for line in split_lines(source):
        offsets.append(offsets[-1] + len(line))
    return offsets


def _offset(offsets: list[int], line: int, col: int) -> int:
    return offsets[line - 1] + col


def _splice(source: str, splices: list[_Splice]) -> str:
    ordered = sorted(splices, key=lambda item: (item.start, item.end), reverse=True)
    previous_start = len(source) + 1
    for item in ordered:
        if item.end > previous_start:
            raise PatchError("patch edits overlap; this is a bug in the patch engine")
        source = source[: item.start] + item.text + source[item.end :]
        previous_start = item.start
    return source
