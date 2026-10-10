"""Bounded in-memory filmstrip cache for smooth, always-fresh scrubbing.

Frames are stored already encoded (the same zlib+header payload the frame channel
uses), keyed by integer frame index at the host's camera fps.  The cache is filled
by :class:`manim_studio.scrub.FilmstripCrawler` and served directly by the preview
host, which makes a scrub inside a cached region as cheap as a video seek — while
staying fresh, because any reload replaces the whole cache.

This module is pure stdlib so the eviction policy can be unit-tested anywhere.
"""

from __future__ import annotations

from collections import OrderedDict
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable

__all__ = ["DEFAULT_MAX_BYTES", "DEFAULT_MAX_FRAMES", "Filmstrip"]

#: Keep memory bounded: ~4 minutes at 30 fps, and never more than 256 MiB of
#: compressed frame payloads.
DEFAULT_MAX_FRAMES = 7200
DEFAULT_MAX_BYTES = 256 * 1024 * 1024


class Filmstrip:
    """LRU cache of encoded frames keyed by frame index."""

    def __init__(
        self,
        *,
        max_frames: int = DEFAULT_MAX_FRAMES,
        max_bytes: int = DEFAULT_MAX_BYTES,
    ) -> None:
        if max_frames < 1 or max_bytes < 1:
            raise ValueError("filmstrip budgets must be positive")
        self.max_frames = max_frames
        self.max_bytes = max_bytes
        self._frames: OrderedDict[int, bytes] = OrderedDict()
        self._bytes = 0

    # ------------------------------------------------------------------

    @property
    def frames(self) -> int:
        return len(self._frames)

    @property
    def byte_size(self) -> int:
        return self._bytes

    @property
    def is_empty(self) -> bool:
        return not self._frames

    def put(self, index: int, blob: bytes) -> bool:
        """Store one encoded frame; returns False when it cannot fit the budget."""
        if index < 0:
            raise ValueError(f"frame index must be >= 0, got {index}")
        if len(blob) > self.max_bytes:
            return False
        existing = self._frames.pop(index, None)
        if existing is not None:
            self._bytes -= len(existing)
        self._frames[index] = blob
        self._bytes += len(blob)
        self._evict()
        return True

    def get(self, index: int) -> bytes | None:
        """Exact lookup, refreshing the entry's LRU position."""
        blob = self._frames.get(index)
        if blob is None:
            return None
        self._frames.move_to_end(index)
        return blob

    def nearest(self, index: int, max_distance: int = 3) -> bytes | None:
        """Closest cached frame within ``max_distance`` (ties go to the earlier one)."""
        for distance in range(0, max_distance + 1):
            for candidate in (index - distance, index + distance):
                if candidate < 0:
                    continue
                blob = self.get(candidate)
                if blob is not None:
                    return blob
        return None

    def has_range(self, start: int, stop: int) -> bool:
        """True when every frame index in ``[start, stop)`` is cached (no LRU refresh)."""
        return all(index in self._frames for index in range(start, stop))

    def retain(self, ranges: Iterable[tuple[int, int]]) -> int:
        """Drop every frame outside the half-open ``(start, stop)`` ranges.

        Used on reload to keep the frames of blocks an edit did not touch.  Returns
        how many frames were kept.
        """
        spans = sorted(ranges)
        kept: OrderedDict[int, bytes] = OrderedDict()
        for index, blob in self._frames.items():
            if any(start <= index < stop for start, stop in spans):
                kept[index] = blob
        self._frames = kept
        self._bytes = sum(len(blob) for blob in kept.values())
        return len(kept)

    def retain_shifted(self, moves: Iterable[tuple[int, int, int]]) -> int:
        """Keep only the frames in ``(start, stop, delta)`` ranges, re-keyed by ``delta``.

        A retimed block pushes every later block along the timeline without changing a
        single pixel of it, so its cached frames move to ``index + delta`` instead of
        being rendered again.  Frames outside every range are dropped; the LRU order of
        the kept frames is preserved.  Ranges must not map onto each other (they are
        distinct blocks of one timeline).  Returns how many frames were kept.
        """
        spans = sorted(moves)
        kept: OrderedDict[int, bytes] = OrderedDict()
        for index, blob in self._frames.items():
            for start, stop, delta in spans:
                if start <= index < stop:
                    if index + delta >= 0:
                        kept[index + delta] = blob
                    break
        self._frames = kept
        self._bytes = sum(len(blob) for blob in kept.values())
        return len(kept)

    def clear(self) -> None:
        self._frames.clear()
        self._bytes = 0

    def _evict(self) -> None:
        while self._frames and (
            len(self._frames) > self.max_frames or self._bytes > self.max_bytes
        ):
            _index, blob = self._frames.popitem(last=False)
            self._bytes -= len(blob)
