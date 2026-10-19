# Chunk 1 — `contact_sheet.py`: time sampling, block lookup, grid layout (pure)

Rules: stdlib only (+ `manim_studio.timeline_model`). No PIL, no Qt, no manimlib.
Files: create `src/manim_studio/contact_sheet.py`, `tests/test_contact_sheet.py`.
Also edit `AGENTS.md`: add the module row to the architecture map and add `contact_sheet` to the
list of pure modules in "Hard dependency rules" item 1.

## 1.1 Public API (exact names; `__all__` must list them)

```python
class SheetError(ValueError): ...            # bad arguments (every <= 0, empty times, ...)

@dataclass(frozen=True, slots=True)
class Sampling:
    times: tuple[float, ...]   # ascending, unique (1e-6), each in [start, end]
    step: float                # spacing actually used between samples (>= requested every)
    thinned: bool              # True when max_frames forced a larger step

def sample_times(total: float, every: float, *, start: float = 0.0, end: float | None = None,
                 max_frames: int = 48) -> Sampling
def parse_times(text: str, total: float) -> tuple[float, ...]
def block_at_time(blocks: Sequence[TimelineBlock], time: float) -> TimelineBlock | None
def truncate_label(text: str, max_chars: int) -> str
def format_label(time: float, block: TimelineBlock | None) -> str

@dataclass(frozen=True, slots=True)
class Cell:
    index: int; time: float; block_index: int | None; block_name: str
    label: str
    x: int; y: int          # top-left of the IMAGE area of the cell, in sheet pixels
    width: int; height: int  # image area size
    label_y: int             # top of the label strip under the image

@dataclass(frozen=True, slots=True)
class SheetLayout:
    columns: int; rows: int
    cell_width: int; cell_height: int
    label_height: int; gap: int; margin: int
    width: int; height: int   # whole sheet
    cells: tuple[Cell, ...]

def plan_sheet(times: Sequence[float], blocks: Sequence[TimelineBlock],
               frame_size: tuple[int, int], *, columns: int | None = None,
               max_width: int = 1600, label_height: int = 22, gap: int = 8,
               margin: int = 8) -> SheetLayout
```

## 1.2 Exact behaviour

`sample_times`:
* `total` = scene duration in seconds (>= 0). `end` defaults to `total`; clamp `start`,`end` into
  `[0, total]`; if `start > end` raise `SheetError`. `every <= 0` or non-finite → `SheetError`.
* Raw samples: `round(start + i * every, 6)` for `i = 0, 1, 2, ...` while value `< end - 1e-9`;
  then append `end` (the final state) unless the last sample is within `1e-6` of it.
* If `total == 0` (or `start == end`): return `Sampling((round(start, 6),), every, False)`.
* If `len(raw) > max_frames` (`max_frames >= 2`, else `SheetError`): choose
  `step = every * ceil((len(raw) - 1) / (max_frames - 1))`… concretely: recompute with
  `every2 = (end - start) / (max_frames - 1)`, samples `round(start + i * every2, 6)` for
  `i in range(max_frames)` (so first = start, last = end), `thinned=True`, `step=every2`.
* Examples that MUST hold: `sample_times(2.0, 0.5).times == (0.0, 0.5, 1.0, 1.5, 2.0)`;
  `sample_times(1.2, 0.5).times == (0.0, 0.5, 1.0, 1.2)`;
  `sample_times(0.0, 0.5).times == (0.0,)`;
  `sample_times(10.0, 0.1, max_frames=5)` → 5 samples, first 0.0, last 10.0, `thinned`;
  `sample_times(4.0, 1.0, start=1.0, end=3.0).times == (1.0, 2.0, 3.0)`.

`parse_times("0, 1.5 ,3", total)`: split on `,`, strip, float, clamp each into `[0, total]`,
sort, de-duplicate (1e-6). Empty text or a non-number → `SheetError`.

`block_at_time(blocks, time)`: first block with `block.start <= time < block.end`; a time exactly
on a boundary belongs to the NEXT block; `time >= last.end` → last block; `time < first.start`
→ first block; empty `blocks` → `None`. (Mirrors `ScrubEngine.block_at`.)

`truncate_label(text, n)`: `text` if `len(text) <= n`; else `text[: max(n - 3, 1)] + "..."`
(ASCII dots on purpose: PIL's default bitmap font has no ellipsis glyph). `n < 4` → `text[:n]`.

`format_label(time, block)`: `f"t={time:.2f}s"` when block is `None`, else
`f"t={time:.2f}s #{block.index} {block.name}"`.

`plan_sheet`:
* `times` empty → `SheetError`. `frame_size = (fw, fh)` both > 0 else `SheetError`.
* `columns` default: `min(n, max(1, math.ceil(math.sqrt(n))))` (n=1→1, 2→2, 4→2, 5→3, 9→3,
  20→5). If given: clamp to `[1, n]`.
* `rows = ceil(n / columns)`.
* `cell_width = (max_width - 2*margin - (columns-1)*gap) // columns`, but never larger than `fw`
  (no up-scaling) and at least 16. `cell_height = max(1, round(cell_width * fh / fw))`.
* `width = 2*margin + columns*cell_width + (columns-1)*gap`;
  `height = 2*margin + rows*(cell_height + label_height) + (rows-1)*gap`.
* Cell `i`: `col = i % columns`, `row = i // columns`;
  `x = margin + col*(cell_width+gap)`; `y = margin + row*(cell_height+label_height+gap)`;
  `label_y = y + cell_height`; `block = block_at_time(blocks, times[i])`;
  label = `truncate_label(format_label(...), max(8, cell_width // 8))`.
  `block_index = block.index if block else None`, `block_name = block.name if block else ""`.

## 1.3 Tests (`tests/test_contact_sheet.py`, class per function) — write them FIRST

* sample_times: the five examples above; `every=0` and `every=-1` and `float("nan")` raise;
  `start>end` raises; `max_frames=1` raises; times strictly ascending; no duplicates when
  `end` is a multiple of `every` (`sample_times(1.0, 0.5).times == (0.0, 0.5, 1.0)`).
* parse_times: clamping (`"-1,99"` with total 2 → `(0.0, 2.0)`), sorting, de-dup, errors.
* block_at_time: three blocks (0-1, 1-2, 2-3): t=0→#0, t=1.0→#1 (boundary → next), t=2.999→#2,
  t=3→#2, t=-1→#0, `[]`→None. Build blocks with `TimelineBlock(index=i, name="Wait", start=..,
  duration=1.0)`.
* truncate_label / format_label: exact strings.
* plan_sheet: n=1 (1 col, 1 row); n=5 (3 cols, 2 rows); every cell inside `[0,width]x[0,height]`;
  no two image rects (x,y,w,h) overlap; label strip below each image; `cell_width <= fw`;
  explicit `columns=99` clamps to n; formula check for `width`/`height`; empty times raises;
  all label strings `<= max(8, cell_width // 8)` long.

## 1.4 Done when

`uv run pytest tests/test_contact_sheet.py` green; ruff/mypy clean; AGENTS.md updated;
commit `feat(frames): contact-sheet time sampling and layout`.
