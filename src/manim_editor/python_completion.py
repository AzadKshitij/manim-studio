"""Jedi-backed Python completion and signature lookup for the code editor.

Jedi performs static analysis only: it parses the buffer and the installed
packages but never imports or executes the user's scene, so calling it is safe
and side-effect free.  The calls can be slow on first use, so the editor runs
them on a worker thread (see ``editor/completion_worker.py``) and this module
stays free of Qt, ``manimlib`` and Scintilla so it can be unit-tested directly.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import jedi

__all__ = ["Completion", "Signature", "complete", "offset_to_line_col", "signatures"]

log = logging.getLogger(__name__)

_MAX_COMPLETIONS = 200


@dataclass(frozen=True, slots=True)
class Completion:
    """One completion candidate from Jedi."""

    name: str
    insert: str
    kind: str  # Jedi's type: "class" | "function" | "instance" | "statement" | ...


@dataclass(frozen=True, slots=True)
class Signature:
    """A call signature plus the index of the parameter being edited."""

    label: str
    active_parameter: int


def offset_to_line_col(source: str, offset: int) -> tuple[int, int]:
    """Convert a character ``offset`` to Jedi's 1-based line / 0-based column."""
    offset = max(0, min(offset, len(source)))
    line = source.count("\n", 0, offset) + 1
    line_start = source.rfind("\n", 0, offset) + 1
    return line, offset - line_start


def _script(source: str, path: str | None) -> jedi.Script:
    return jedi.Script(source, path=path or None)


def complete(source: str, offset: int, path: str | None = None) -> tuple[Completion, ...]:
    """Return completion candidates for the caret at ``offset``.

    Returns an empty tuple when Jedi has nothing (or cannot parse the buffer);
    a broken scene being edited must never raise into the UI.
    """
    if not source:
        return ()
    line, column = offset_to_line_col(source, offset)
    try:
        results = _script(source, path).complete(line=line, column=column)
    except Exception:  # pragma: no cover - defensive: Jedi raises many types
        log.debug("jedi completion failed", exc_info=True)
        return ()
    completions: list[Completion] = []
    seen: set[str] = set()
    for item in results:
        # Jedi's ``complete`` is only the suffix left to type (``pend`` for
        # ``values.ap``); Scintilla replaces the typed prefix itself, so the
        # full ``name`` is what must go in the list.
        insert = item.name or item.complete
        if not insert or insert in seen:
            continue
        seen.add(insert)
        completions.append(
            Completion(name=item.name or insert, insert=insert, kind=item.type or "")
        )
        if len(completions) >= _MAX_COMPLETIONS:
            break
    return tuple(completions)


def signatures(source: str, offset: int, path: str | None = None) -> tuple[Signature, ...]:
    """Return call signatures for a call whose parenthesis precedes ``offset``."""
    if not source:
        return ()
    line, column = offset_to_line_col(source, offset)
    try:
        results = _script(source, path).get_signatures(line=line, column=column)
    except Exception:  # pragma: no cover - defensive
        log.debug("jedi signature lookup failed", exc_info=True)
        return ()
    found: list[Signature] = []
    for item in results:
        active = getattr(item, "index", None)
        render = getattr(item, "to_string", None)
        label = render() if callable(render) else str(item)
        found.append(
            Signature(
                label=_one_line(str(label)),
                active_parameter=active if isinstance(active, int) else -1,
            )
        )
    return tuple(found)


def _one_line(text: str) -> str:
    """Collapse a possibly multi-line signature to a single calltip line."""
    return " ".join(text.split())
