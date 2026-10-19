"""Contact-sheet planning: which times to sample, which block each sample sits in, and where
each tile goes on the sheet (pure geometry, no Qt, no manimlib, no pixels).

``frames.py`` composes the actual image from the layout computed here.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from manim_studio.timeline_model import TimelineBlock

__all__ = [
    "Cell",
    "Sampling",
    "SheetError",
    "SheetLayout",
    "block_at_time",
    "format_label",
    "normalize_times",
    "parse_times",
    "plan_sheet",
    "sample_times",
    "truncate_label",
]

# Times are reported to microseconds; two samples closer than this are the same sample.
_TIME_DECIMALS = 6
# Slack when comparing a sample against the end of the range (float noise only).
_END_EPS = 1e-9
_MIN_CELL_WIDTH = 16


class SheetError(ValueError):
    """Bad arguments for a contact sheet (non-positive spacing, empty times, ...)."""


@dataclass(frozen=True, slots=True)
class Sampling:
    """Sample times for a sheet, and how they were chosen."""

    times: tuple[float, ...]
    step: float  # spacing actually used between samples (>= the requested ``every``)
    thinned: bool  # True when ``max_frames`` forced a larger step


def _finite(value: float, name: str) -> None:
    if not math.isfinite(value):
        raise SheetError(f"{name} must be a finite number, got {value!r}")


def sample_times(
    total: float,
    every: float,
    *,
    start: float = 0.0,
    end: float | None = None,
    max_frames: int = 48,
) -> Sampling:
    """Evenly spaced sample times in ``[start, end]``, always ending on ``end``.

    When the even spacing would give more than ``max_frames`` samples, the samples are spread
    evenly over the whole range instead (``thinned=True``) so the first and last state are kept.
    """
    _finite(total, "total")
    _finite(every, "every")
    if total < 0:
        raise SheetError(f"total must be >= 0, got {total!r}")
    if every <= 0:
        raise SheetError(f"every must be > 0, got {every!r}")
    if max_frames < 2:
        raise SheetError(f"max_frames must be >= 2, got {max_frames!r}")

    range_end = total if end is None else end
    _finite(start, "start")
    _finite(range_end, "end")
    lo = min(max(start, 0.0), total)
    hi = min(max(range_end, 0.0), total)
    if lo > hi:
        raise SheetError(f"start ({start}) must not be after end ({range_end})")

    if hi - lo <= _END_EPS:
        return Sampling((round(lo, _TIME_DECIMALS),), every, False)

    # Collect one sample past the cap: enough to know the sheet must be thinned.
    raw: list[float] = []
    index = 0
    while len(raw) <= max_frames:
        value = round(lo + index * every, _TIME_DECIMALS)
        if value >= hi - _END_EPS:
            break
        raw.append(value)
        index += 1
    end_value = round(hi, _TIME_DECIMALS)
    if not raw or raw[-1] != end_value:
        raw.append(end_value)

    if len(raw) > max_frames:
        spacing = (hi - lo) / (max_frames - 1)
        spread = [round(lo + i * spacing, _TIME_DECIMALS) for i in range(max_frames - 1)]
        # dict.fromkeys keeps order and drops any value that rounding made equal.
        times = tuple(dict.fromkeys([*spread, end_value]))
        return Sampling(times, spacing, True)
    return Sampling(tuple(raw), every, False)


def normalize_times(values: Iterable[float], total: float) -> tuple[float, ...]:
    """Clamp ``values`` into ``[0, total]``, then sort and drop duplicates (to microseconds)."""
    rounded: set[float] = set()
    for value in values:
        _finite(value, "time")
        rounded.add(round(min(max(value, 0.0), total), _TIME_DECIMALS))
    if not rounded:
        raise SheetError("times must not be empty")
    return tuple(sorted(rounded))


def parse_times(text: str, total: float) -> tuple[float, ...]:
    """Parse ``"0, 1.5 ,3"`` into sorted, de-duplicated times clamped to ``[0, total]``."""
    if not text.strip():
        raise SheetError("times must not be empty")
    values: list[float] = []
    for part in text.split(","):
        token = part.strip()
        try:
            values.append(float(token))
        except ValueError:
            raise SheetError(f"not a number in times: {token!r}") from None
    return normalize_times(values, total)


def block_at_time(blocks: Sequence[TimelineBlock], time: float) -> TimelineBlock | None:
    """The block containing ``time``: a boundary belongs to the next block, and times outside
    the trace clamp to the first or last block. Mirrors ``ScrubEngine.block_at``."""
    if not blocks:
        return None
    for block in blocks:
        if block.start <= time < block.end:
            return block
    return blocks[0] if time < blocks[0].start else blocks[-1]


def truncate_label(text: str, max_chars: int) -> str:
    """Shorten ``text`` to ``max_chars``, ending in ASCII ``...`` (the PIL default font lacks an
    ellipsis glyph)."""
    if max_chars < 0:
        raise SheetError(f"max_chars must be >= 0, got {max_chars!r}")
    if len(text) <= max_chars:
        return text
    if max_chars < 4:
        return text[:max_chars]
    return text[: max_chars - 3] + "..."


def format_label(time: float, block: TimelineBlock | None) -> str:
    """The caption under a tile: the time, then the block it belongs to."""
    if block is None:
        return f"t={time:.2f}s"
    return f"t={time:.2f}s #{block.index} {block.name}"


@dataclass(frozen=True, slots=True)
class Cell:
    """One tile: where its image goes, and its caption strip just below it."""

    index: int
    time: float
    block_index: int | None
    block_name: str
    label: str
    x: int  # top-left of the IMAGE area, in sheet pixels
    y: int
    width: int  # image area size
    height: int
    label_y: int  # top of the label strip under the image


@dataclass(frozen=True, slots=True)
class SheetLayout:
    """Grid geometry for a whole sheet, in pixels."""

    columns: int
    rows: int
    cell_width: int
    cell_height: int
    label_height: int
    gap: int
    margin: int
    width: int
    height: int
    cells: tuple[Cell, ...]


def plan_sheet(
    times: Sequence[float],
    blocks: Sequence[TimelineBlock],
    frame_size: tuple[int, int],
    *,
    columns: int | None = None,
    max_width: int = 1600,
    label_height: int = 22,
    gap: int = 8,
    margin: int = 8,
) -> SheetLayout:
    """Lay out one tile per time in a grid no wider than ``max_width`` (when possible)."""
    if not times:
        raise SheetError("a contact sheet needs at least one time")
    frame_w, frame_h = frame_size
    if frame_w <= 0 or frame_h <= 0:
        raise SheetError(f"frame size must be positive, got {frame_size!r}")
    if label_height < 0 or gap < 0 or margin < 0:
        raise SheetError("label_height, gap and margin must be >= 0")

    count = len(times)
    if columns is None:
        columns = min(count, max(1, math.ceil(math.sqrt(count))))
    else:
        columns = min(max(columns, 1), count)
    rows = (count + columns - 1) // columns

    fitted = (max_width - 2 * margin - (columns - 1) * gap) // columns
    # Never scale a frame up; never shrink a tile below the readable minimum unless the frame
    # itself is smaller than that.
    cell_width = min(frame_w, max(_MIN_CELL_WIDTH, fitted))
    cell_height = max(1, round(cell_width * frame_h / frame_w))

    width = 2 * margin + columns * cell_width + (columns - 1) * gap
    height = 2 * margin + rows * (cell_height + label_height) + (rows - 1) * gap
    label_budget = max(8, cell_width // 8)

    cells: list[Cell] = []
    for index, time in enumerate(times):
        col = index % columns
        row = index // columns
        x = margin + col * (cell_width + gap)
        y = margin + row * (cell_height + label_height + gap)
        block = block_at_time(blocks, time)
        cells.append(
            Cell(
                index=index,
                time=time,
                block_index=block.index if block is not None else None,
                block_name=block.name if block is not None else "",
                label=truncate_label(format_label(time, block), label_budget),
                x=x,
                y=y,
                width=cell_width,
                height=cell_height,
                label_y=y + cell_height,
            )
        )
    return SheetLayout(
        columns=columns,
        rows=rows,
        cell_width=cell_width,
        cell_height=cell_height,
        label_height=label_height,
        gap=gap,
        margin=margin,
        width=width,
        height=height,
        cells=tuple(cells),
    )
