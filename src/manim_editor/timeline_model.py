"""Pure timeline data model.

The timeline is built from a *runtime trace* of ``Scene.play()`` / ``Scene.wait()``
calls.  ``log.json`` is the spec-compliant serialization: a JSON list of objects
``{"name": str, "start": float, "duration": float, "index": int}``.

This module must stay free of Qt, manimlib and moderngl imports so that the CLI and
the test suite can exercise it anywhere.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence

__all__ = [
    "TimelineBlock",
    "TimelineError",
    "TimelineLog",
    "TimelineView",
    "dump_log",
    "format_duration",
    "load_log",
    "parse_log",
    "save_log",
    "snap_index",
]


class TimelineError(ValueError):
    """Raised when timeline data is malformed or inconsistent."""


@dataclass(frozen=True, slots=True)
class TimelineBlock:
    """One ``play()`` or ``wait()`` call: a rectangle on the timeline."""

    index: int
    name: str
    start: float
    duration: float

    def __post_init__(self) -> None:
        if self.index < 0:
            raise TimelineError(f"block index must be >= 0, got {self.index}")
        if not isinstance(self.name, str) or not self.name.strip():
            raise TimelineError("block name must be a non-empty string")
        if not math.isfinite(self.start) or self.start < 0:
            raise TimelineError(f"block start must be finite and >= 0, got {self.start!r}")
        if not math.isfinite(self.duration) or self.duration < 0:
            raise TimelineError(f"block duration must be finite and >= 0, got {self.duration!r}")

    @property
    def end(self) -> float:
        """Scene time at which this block finishes."""
        return self.start + self.duration

    def to_dict(self) -> dict[str, Any]:
        """Serialize to the spec ``log.json`` shape."""
        return {
            "name": self.name,
            "start": self.start,
            "duration": self.duration,
            "index": self.index,
        }

    @classmethod
    def from_dict(cls, data: Any) -> TimelineBlock:
        """Strictly parse one block; raise :class:`TimelineError` on bad input."""
        if not isinstance(data, dict):
            raise TimelineError(f"block must be an object, got {type(data).__name__}")
        missing = {"name", "start", "duration", "index"} - data.keys()
        if missing:
            raise TimelineError(f"block is missing keys: {sorted(missing)}")
        name = data["name"]
        if not isinstance(name, str):
            raise TimelineError(f"block name must be a string, got {type(name).__name__}")
        try:
            start = float(data["start"])
            duration = float(data["duration"])
            index = int(data["index"])
        except (TypeError, ValueError) as exc:
            raise TimelineError(f"block has non-numeric fields: {data!r}") from exc
        return cls(index=index, name=name, start=start, duration=duration)


@dataclass(frozen=True, slots=True)
class TimelineLog:
    """An ordered, validated collection of trace blocks."""

    blocks: tuple[TimelineBlock, ...]

    def __post_init__(self) -> None:
        seen: set[int] = set()
        last_index = -1
        for block in self.blocks:
            if block.index in seen:
                raise TimelineError(f"duplicate block index {block.index}")
            if block.index <= last_index:
                raise TimelineError("blocks must be ordered by ascending index")
            seen.add(block.index)
            last_index = block.index

    @property
    def is_empty(self) -> bool:
        return not self.blocks

    @property
    def num_blocks(self) -> int:
        return len(self.blocks)

    @property
    def total_duration(self) -> float:
        """End time of the last block, or 0.0 for an empty trace."""
        if not self.blocks:
            return 0.0
        return max(block.end for block in self.blocks)

    def block_by_index(self, index: int) -> TimelineBlock | None:
        """Return the block with ``index`` or ``None``."""
        for block in self.blocks:
            if block.index == index:
                return block
        return None

    def block_at(self, time: float) -> TimelineBlock | None:
        """Return the block that contains ``time`` (half-open intervals).

        Times outside the trace clamp to the first/last block; ``time`` exactly
        equal to the total duration belongs to the last block.
        """
        if not self.blocks:
            return None
        if time <= self.blocks[0].start:
            return self.blocks[0]
        for block in self.blocks:
            if block.start <= time < block.end:
                return block
        return self.blocks[-1]

    def index_at(self, time: float) -> int | None:
        """Convenience wrapper returning the containing block index."""
        block = self.block_at(time)
        return None if block is None else block.index

    def to_dicts(self) -> list[dict[str, Any]]:
        return [block.to_dict() for block in self.blocks]

    @classmethod
    def from_iterable(cls, blocks: Iterable[TimelineBlock]) -> TimelineLog:
        return cls(tuple(blocks))


def dump_log(blocks: Sequence[TimelineBlock]) -> list[dict[str, Any]]:
    """Serialize blocks into the spec-compliant bare-list shape."""
    return [block.to_dict() for block in blocks]


def parse_log(data: Any) -> TimelineLog:
    """Parse a trace payload.

    Accepts either the bare list form of ``log.json`` or a versioned object
    ``{"version": 1, "blocks": [...]}`` for forward compatibility.
    """
    if isinstance(data, list):
        blocks_data = data
    elif isinstance(data, dict) and "blocks" in data:
        blocks_data = data["blocks"]
    else:
        raise TimelineError("trace payload must be a list or an object with 'blocks'")
    if not isinstance(blocks_data, list):
        raise TimelineError("'blocks' must be a list")
    return TimelineLog.from_iterable(TimelineBlock.from_dict(item) for item in blocks_data)


def load_log(path: str | Path) -> TimelineLog:
    """Read and validate a ``log.json`` file."""
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise
    except json.JSONDecodeError as exc:
        raise TimelineError(f"invalid JSON in {path}: {exc}") from exc
    return parse_log(raw)


def save_log(path: str | Path, log: TimelineLog | Sequence[TimelineBlock]) -> None:
    """Write a spec-compliant ``log.json`` (pretty-printed, stable order)."""
    blocks = log.blocks if isinstance(log, TimelineLog) else tuple(log)
    text = json.dumps(dump_log(blocks), indent=2) + "\n"
    Path(path).write_text(text, encoding="utf-8")


def snap_index(log: TimelineLog, time: float) -> int | None:
    """Block index containing ``time``; ``None`` when the log is empty."""
    return log.index_at(time)


def format_duration(seconds: float) -> str:
    """Human-friendly ``m:ss.d`` (or ``ss.d`` under a minute) label."""
    if not math.isfinite(seconds) or seconds < 0:
        seconds = 0.0
    minutes, rest = divmod(seconds, 60.0)
    if minutes >= 1:
        return f"{int(minutes)}:{rest:04.1f}"
    return f"{rest:.1f}s"


@dataclass(frozen=True, slots=True)
class TimelineView:
    """Zoom/pan mapping between scene time and widget pixels.

    ``start`` is the leftmost visible time, ``span`` the visible duration and
    ``width`` the widget width in pixels.  All construction clamps values so the
    view can never invert or escape the trace bounds.
    """

    total: float
    width: float
    start: float = 0.0
    span: float | None = None

    def __post_init__(self) -> None:
        total = max(float(self.total), 1e-6)
        width = max(float(self.width), 1.0)
        max_span = max(total, 1e-6)
        span = max_span if self.span is None else min(max(float(self.span), 1e-6), max_span)
        start = min(max(float(self.start), 0.0), max_span - span)
        object.__setattr__(self, "total", total)
        object.__setattr__(self, "width", width)
        object.__setattr__(self, "span", span)
        object.__setattr__(self, "start", max(start, 0.0))

    @property
    def end(self) -> float:
        return self.start + (self.span or 0.0)

    def time_to_x(self, time: float) -> float:
        """Map a scene time to a widget x coordinate (may be off-screen)."""
        return (time - self.start) / (self.span or 1e-6) * self.width

    def x_to_time(self, x: float) -> float:
        """Map a widget x coordinate to a scene time, clamped to the trace."""
        time = self.start + x / self.width * (self.span or 1e-6)
        return min(max(time, 0.0), self.total)

    def zoom_at(self, x: float, factor: float, *, min_span: float = 0.05) -> TimelineView:
        """Zoom around an x coordinate by ``factor`` (>1 zooms in)."""
        if factor <= 0:
            raise TimelineError("zoom factor must be positive")
        anchor = self.x_to_time(x)
        new_span = min(max((self.span or 1e-6) / factor, min_span), max(self.total, 1e-6))
        anchor_ratio = (anchor - self.start) / (self.span or 1e-6)
        new_start = anchor - anchor_ratio * new_span
        return replace(self, start=new_start, span=new_span)

    def pan_px(self, dx_pixels: float) -> TimelineView:
        """Pan by a pixel delta (positive scrolls content left)."""
        dt = dx_pixels / self.width * (self.span or 1e-6)
        return replace(self, start=self.start + dt)

    def fit(self) -> TimelineView:
        """Show the whole trace."""
        return replace(self, start=0.0, span=max(self.total, 1e-6))

    @property
    def is_zoomed(self) -> bool:
        return (self.span or 0.0) < self.total - 1e-9
