"""Runtime helper behind per-iteration timeline edits (pure, stdlib only).

When a timeline edit targets *one iteration* of a loop body, the patched source
cannot just contain a different literal.  :func:`manim_studio.patches.apply_patches`
instead emits ``__studio__.dur(site_id, {iteration: seconds}, default)`` and the
module that runs the patched source exposes a :class:`StudioRuntime` under that name.

The runtime counts how often each site executed.  Anything that re-runs ``construct``
(the scrub replay, the filmstrip crawler, a reload) must call :meth:`reset` first so
iteration numbers start from zero again.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Mapping

__all__ = ["StudioRuntime"]


class StudioRuntime:
    """Per-site execution counter that resolves per-iteration duration overrides."""

    def __init__(self) -> None:
        self._counts: dict[str, int] = {}

    def reset(self) -> None:
        """Forget all counts; call before every re-run of ``construct``."""
        self._counts.clear()

    def count(self, site_id: str) -> int:
        """How many times ``site_id`` has resolved a duration since the last reset."""
        return self._counts.get(site_id, 0)

    def dur(self, site_id: str, overrides: Mapping[int, float], default: float) -> float:
        """The duration for this execution of ``site_id``: override or ``default``."""
        occurrence = self._counts.get(site_id, 0)
        self._counts[site_id] = occurrence + 1
        return overrides.get(occurrence, default)
