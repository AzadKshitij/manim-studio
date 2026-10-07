"""Timeline timing overrides with undo/redo (pure, Qt-free).

A user lengthens or shortens a section by dragging a block's right edge; the
override is a per-block duration that the host applies when it replays the
scene.  Keeping the edit history here — rather than in the widget — makes
undo/redo and "what does the timeline look like now" unit-testable without Qt.
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING

from manim_studio.timeline_model import TimelineError, TimelineLog

if TYPE_CHECKING:
    from collections.abc import Mapping

__all__ = ["TimingOverrides"]


def _validate_duration(duration: float) -> float:
    value = float(duration)
    if not math.isfinite(value) or value < 0:
        raise TimelineError(f"duration must be finite and >= 0, got {duration!r}")
    return value


class TimingOverrides:
    """Ordered per-block duration overrides with undo/redo.

    Only blocks the user actually changed are stored; everything else follows
    ``base``.  :meth:`log` re-derives the whole timeline, shifting later blocks
    so a lengthened section pushes everything after it.
    """

    def __init__(self, base: TimelineLog) -> None:
        self._base = base
        self._current: dict[int, float] = {}
        self._undo: list[dict[int, float]] = []
        self._redo: list[dict[int, float]] = []

    @property
    def base(self) -> TimelineLog:
        return self._base

    @property
    def is_empty(self) -> bool:
        return not self._current

    @property
    def can_undo(self) -> bool:
        return bool(self._undo)

    @property
    def can_redo(self) -> bool:
        return bool(self._redo)

    @property
    def durations(self) -> Mapping[int, float]:
        """A copy of the current ``{block index: duration}`` overrides."""
        return dict(self._current)

    def set_duration(self, index: int, duration: float) -> bool:
        """Override ``index``'s duration; returns False when nothing changed."""
        value = _validate_duration(duration)
        target = self._base.block_by_index(index)
        if target is None:
            raise TimelineError(f"no block with index {index}")
        if self._current.get(index, target.duration) == value:
            return False
        self._push()
        self._current[index] = value
        return True

    def reset_block(self, index: int) -> bool:
        """Drop the override for ``index``; returns False when there was none."""
        if index not in self._current:
            return False
        self._push()
        del self._current[index]
        return True

    def reset(self) -> bool:
        """Drop every override; returns False when there were none."""
        if not self._current:
            return False
        self._push()
        self._current = {}
        return True

    def undo(self) -> bool:
        if not self._undo:
            return False
        self._redo.append(dict(self._current))
        self._current = self._undo.pop()
        return True

    def redo(self) -> bool:
        if not self._redo:
            return False
        self._undo.append(dict(self._current))
        self._current = self._redo.pop()
        return True

    def log(self) -> TimelineLog:
        """``base`` with every override applied in ascending index order."""
        log = self._base
        for index in sorted(self._current):
            log = log.scaled_block_duration(index, self._current[index])
        return log

    def to_payload(self) -> list[dict[str, float]]:
        """Wire form: ``[{"index": i, "duration": d}, ...]`` sorted by index."""
        return [
            {"index": index, "duration": self._current[index]} for index in sorted(self._current)
        ]

    def _push(self) -> None:
        self._undo.append(dict(self._current))
        self._redo.clear()
