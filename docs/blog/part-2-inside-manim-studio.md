---
title: "Inside Manim Studio: architecture, scrubbing, and an editable timeline"
published: false
description: "How Manim Studio works under the hood: a companion OpenGL process, a runtime-traced timeline, three designs for scrubbing, an editable timeline of anchored patches, and the gotchas that cost real hours."
tags: manim, python, architecture, opensource
cover_image: ""
series: "Building Manim Studio"
# Hashnode: rename `description` to `subtitle`, drop `series`.
---

> This is **Part 2** of a two-part series. [Part 1](part-1-how-i-built-manim-studio.md)
> covered why Manim Studio exists and how it grew from an empty project to v1.1.0. This
> part is the engineering: the architecture and every decision that mattered.

Manim Studio is a realtime desktop editor for [ManimGL](https://github.com/3b1b/manim)
scenes. Code editor on the left, live preview on the right, scrubbable timeline below. The
whole design answers one question: **how do you show a running Manim scene, at any point in
time, without re-rendering a video?**

Here's how — and, along the way, why the obvious version of several of these ideas doesn't
work.

## The bet that shaped everything: a companion OpenGL process

The naive design is a single process: Qt on one side, `manimlib` + OpenGL on the other,
both on the GUI thread. The original spec actually said exactly that — *"all GL on GUI
thread."*

I didn't do that, and it's the single most important decision in the codebase (ADR 0001).
Running pyglet/moderngl's event loop inside Qt's GUI thread means **two event loops sharing
one thread** — and a GPU or driver hiccup then takes the whole editor down with it. A bad
shader, a driver reset, a scene with an infinite loop: any of those kills your editor and
everything you hadn't saved.

So all OpenGL lives in **a companion process** (`manim_studio/preview_host.py`) with one GL
context on one thread. The editor spawns it, passes a loopback TCP port, and the host
connects back. The editor never touches GL — rendering lives entirely in the host — and it
talks to that host over a versioned JSON protocol.

The host can show the scene two ways:

- **External window** — the host shows a normal GL window next to Qt.
- **Embedded (default)** — the host keeps its window hidden, renders into the framebuffer,
  reads it back with `fbo.read()`, compresses the RGBA, and streams length-prefixed frames
  over a dedicated socket. The editor turns each frame into a `QImage` in a `QTimer` poll.

Backpressure is explicit: **at most one frame in flight, and frames are dropped, not queued,
when the socket is congested.** Preview *freshness* beats preview completeness — if the
transport can't keep up, you'd rather see the newest frame than a smooth replay of stale
ones.

> 🖼️ **[Screenshot placeholder — external GL window mode]**
> *Stage 1: the editor and the standalone GL preview side by side.*

The consequences are exactly what you'd want: a rendering crash isolates to the host and the
editor restarts it automatically; user code errors surface as editor markers instead of
taking down the app; and the IPC protocol becomes a first-class, versioned, unit-tested
interface (it's at **v5** now). Export deliberately doesn't use the host at all — it shells
out to `manimgl -w` for deterministic, non-interactive output.

## Trace, don't parse: why the timeline is a runtime trace

The timeline could have been built by parsing the scene's AST and reading every
`self.play(...)`. It isn't, and that's deliberate.

Static analysis is wrong the moment control flow isn't linear. A `for` loop that plays the
same animation five times, an `if` that branches on a random value, an animation inside a
helper — an AST sees *one* call site; the user sees *five* blocks on the timeline. So the
host **runs the scene** with a tracer that wraps `play()` and `wait()`, and emits a block
list:

```json
{ "name": "FadeIn", "start": 2.4, "duration": 1.5, "index": 3 }
```

Now the timeline matches what actually executes. Loops fan out into real blocks; branches
reflect the branch that ran. The same runtime counter (`num_plays`) later becomes the key
that ties a timeline block back to the Python call site it came from — which is what makes
editable patches possible.

## Scrubbing, three designs deep

This is where the project got interesting, because the **obvious solution doesn't work.**

**Attempt 1 — snapshots.** Save the scene state at every block boundary (`save_state()`),
then drive `anim.interpolate(alpha)` from the restored snapshot. On the face of it this is
the video-player approach — store keyframes, interpolate between them — so surely it works
for a timeline.

It breaks the moment a scene uses `always_redraw` or updaters. A `SceneState` cannot
capture an animation mid-flight, and restored mobject copies can detach from the closures
that keep redrawing them. You get a frozen or wrong picture — silently, which is the worst
kind of wrong.

**Attempt 2 — replay** (ADR 0002). Instead of rewinding a snapshot, *replay the scene*.
Keep a dedicated scrub scene, and on each scrub:

1. Reset it (mobjects, time, counters, undo stacks, RNG seed).
2. Re-run `setup()` + `construct()` in **skip-animations** mode.
3. Blocks *before* the target run in the fast path; the block *containing* the target is
   intercepted and each animation is interpolated to exactly the alpha the normal playback
   loop would compute.

Stripped to its core:

```python
def pose(self, time: float) -> TimelineBlock | None:
    """Replay the scene so it displays the state at `time` seconds."""
    target = min(max(time, 0.0), self.total_duration)
    self._reset(target)          # mobjects, time, counters, RNG
    self._scene.setup()
    self._scene.construct()      # skip mode; the target block is intercepted
    return self.block_at(target) # one frame is rendered from this pose
```

Because it runs the *normal* animation machinery, `always_redraw` and updaters behave
exactly as they do when playback pauses on a frame. Loops and branches are replayed at
runtime instead of trusted to a snapshot. The cost is one replay per scrub step — measured
at roughly **5–50 ms** for the test scenes — and the host coalesces scrub requests so a
pointer-rate drag never queues work: latest wins.

**Attempt 3 — a filmstrip cache** (ADR 0003). Replaying 5–50 ms per pointer move is fine
for a nudge, not for a drag you want to feel like video. So the host keeps an in-memory LRU
of **already-encoded frames**, keyed by integer frame index. A background crawler fills it,
block by block, rendering each frame in O(1) — the same loop manimgl uses for playback,
minus the timing sleeps and the video encoding. A scrub request is served **cache-first**:
an exact index, or the nearest cached frame within 3 frames. Cache misses fall back to a
replay pose *and store the rendered frame*.

The design pushed further in v1.1:

- **Single-pass crawl** — instead of replaying `construct()` once per block, suspend it on a
  greenlet (same OS thread, so the single-context rule holds) and step it frame by frame
  across every uncached block. One pass, not one replay per block.
- **Reuse across reloads** (ADR 0009) — after an edit, frames of blocks the edit provably
  cannot affect are *pixel-identical*, so keep them. The edit region is the span between the
  common line prefix and suffix of the old and new executed source; reuse requires that span
  to sit inside `construct()` and above no later block. On the showcase, this is the
  difference between a full re-crawl and re-rendering just the tail.
- **Timing-only edits shift, they don't re-render.** If the only change is a retimed
  `SetDuration`, and nothing makes a frame depend on elapsed time, later blocks move along
  the timeline without changing a pixel — the cache is *re-keyed*, not rebuilt.

The freshness guarantee is structural: cached frames can only ever belong to the current
revision, because blocks are reused only when everything that ran up to them is unchanged.

## "Make it fast" as a measured discipline

"It feels slow" is not a bug report you can close. So I built a benchmark harness
(`tests/test_perf.py`) that records numbers to JSON and diffs before/after on the same
machine, and I treated every optimization as a hypothesis with a measurement.

Codec first. The original wire format compressed frames with zlib. A comparison table made
the call obvious — `zstd-1` compresses **~6× faster than zlib-1**, decompresses ~2.5×
faster, and at a **~3× better ratio**. (lz4 encoded marginally faster but shipped ~2.8× more
bytes and decoded slower.) So frames went to zstd, with zlib/raw fallback when a payload
won't shrink.

Then the pipeline, step by step:

- Encode **on the pump thread**, not the GL thread — the GL thread only pays a lock and a
  buffer swap. Handoff went from ~34 ms to **0.003 ms/frame**.
- **Dedup identical frames** — during a `wait` or a static hold manimgl redraws the same
  pixels every tick, so exact repeats are dropped before encoding.
- Scale streamed frames with **fast** transformation, then settle **smooth** 150 ms after
  the stream pauses — cheap motion, crisp stills.
- Fill the filmstrip **from the playhead outward**, not from the start.

The headline results from the "rendering" branch, measured on the same machine:

| Metric | Before | After | Change |
| --- | --- | --- | --- |
| Edit → new frame shown | 0.126–0.131 s | **0.069–0.075 s** | **−44%** |
| Retime last block → cache complete | 1.077 s | **0.284 s** | **−74%** |
| Edit mid-scene block → cache complete | 1.094 s | **0.477 s** | **−56%** |
| Full crawl, first load | 0.857 s (≈120 fps) | **0.550 s (≈200 fps)** | **−36%** |
| Real-time playback | 28.5 eff. fps | 28.5 eff. fps | no regression |

Crawl throughput overall went from **22.96 → 39.32 fps** on the baseline scene, and frame
encoding from **18.3 ms → 2.43 ms**.

The most dramatic single number is on the starter scene, `Welcome.py` (22 blocks, ~32 s,
`always_redraw` everywhere). Retiming the *first* `wait` used to re-crawl the entire scene —
**6.14–6.28 s**. With reuse-aware timing, it re-renders one block and keeps the other 21:
**0.35–0.37 s**, a **94%** cut, with frames verified pixel-identical (mean abs diff < 1/255)
against a from-scratch crawl.

One honesty note I'm fond of: an early version of the real-time metric reported a "~245 ms
hitch." It was wrong — the gaps were the scene's static `wait` blocks, where the host
deliberately sends *no* frame because nothing changed. The metric now counts a gap as a
stall only for frames beyond the host's own deduped count. Measure, then trust the measure
only after you've explained it.

> 🖼️ **[Screenshot placeholder — export dialog]**
> *Export: quality, fps, resolution, and range, all through headless `manimgl -w`.*
> Suggested file: `docs/assets/screenshots/export.png`.

## The timeline becomes an editor

v0.1.0's timeline *visualized* the scene. v1.1.0's timeline **edits** it — and getting that
right was a design problem, not a UI problem (ADR 0007).

The first attempt stored retiming as a duration override keyed by **block index**. That has
two fatal flaws: the override is discarded on every reload (so any code edit throws away
your retiming), and an index is a *position* — insert a `play()` above a retimed block and
the override lands on the wrong animation.

So edits became **anchored patches**. The anchor is a **call-site fingerprint**, not an
index:

```python
@dataclass(frozen=True, slots=True)
class Anchor:
    """A call site, optionally narrowed to one loop iteration (0-based)."""
    site_id: str
    occurrence: int | None = None
```

`site_id` is a hash of the call *with its duration removed* — plus its enclosing function,
call kind, and an ordinal that separates identical calls. Retiming a call therefore never
changes its identity; changing its *animations* does. `occurrence` narrows a loop body to
one iteration.

Everything else follows from that:

- **The `.py` file is untouched.** Patches live in `<scene>.studio.json` next to the scene,
  with their own undo/redo stack.
- **Edits are applied by splicing text on exact AST spans**, not `ast.unparse` — so comments
  and formatting survive, and "Apply to source" is a minimal diff.
- **Everything runs the same patched text**: preview, scrub, filmstrip, playback and export.
  No second code path that retimes at runtime, so they cannot disagree.
- **Edits that can't be applied are orphaned, never moved.** If a call's fingerprint no
  longer exists, the edit is reported and kept — never applied to a different animation.
- **"Apply to source" is the exit.** It writes the baked text as one undoable edit, saves,
  and keeps only the edits that couldn't be baked.

This is what killed the earlier export-parity problem (ADR 0006). Retiming used to be an
editor-side override that exports never saw — so your preview matched the timeline and your
exported video didn't. Once the export runs the same patched source, the mismatch is gone by
construction, and the CLI gains the same parity for free.

> 🖼️ **[Screenshot placeholder — themes]**
> *Chalkboard and loadable JSON themes.* Suggested file:
> `docs/assets/screenshots/themes.jpg`.

## The long tail

The features that make it *feel* finished were each their own small design problem:

- **LaTeX on demand** (ADR 0004). `Tex` scenes fail with a cryptic error on any machine
  without LaTeX, and shipping TeX Live (~5 GB) is a non-starter. So: don't bundle it.
  Detect a system install; if it's missing, offer a one-click per-user TinyTeX download
  (`manim-studio latex install`). Detection and install stay pure-stdlib, downloads run off
  the GUI thread, and a real end-to-end *diagnose* compiles a test document so "binaries
  found" is never confused with "compilation works."
- **Frozen packaging** (ADR 0005). The editor spawns helpers as `python -m <module>`, which
  a frozen executable can't do. The fix is a **re-invocation protocol**: the frozen app
  relaunches *itself* with an internal `--preview-host` / `--manimgl-export` flag that
  routes to the right module. One onedir bundle, **two executables** — `ManimStudio.exe`
  (windowed GUI) and `manim-studio.exe` (console CLI). No onefile build: a onefile child
  would unpack hundreds of MB to `%TEMP%` on every preview restart.
- **Themes as data** (ADR 0008). Colours were hard-coded across four files and disagreed
  with each other. Now a theme is **base colours + metrics** in a JSON file; hover, pressed
  and selection shades are *derived*, never stored. `editor/styling.py` is the only place
  that turns a theme into a Qt palette and style sheet. Six dark and three light built-ins,
  every one passing contrast checks in tests, plus user themes in
  `~/.manim-studio/themes`.

## Gotchas that cost real hours

Every project has a handful of bugs you remember. Mine:

- **zstd contexts are not thread-safe.** The host encodes on the pump thread *and* the
  crawl thread; a shared `ZstdCompressor` segfaulted the host with `0xC0000005`. Fix: cache
  one compressor **per thread**.
- **`manimlib.config` parses `sys.argv` at import time.** Any extra argument I appended for
  export made its argparse parser exit with code 2. Fix: strip the flag *before* importing
  anything that imports `manimlib`.
- **Scintilla offsets are UTF-8 byte offsets; Jedi wants character offsets.** Mixing them
  put completions on the wrong line. Then Windows CRLF pushed the Jedi offset past the
  caret. Fix: force LF and convert offsets, handling both.
- **A plain `QLabel` asks its layout for the full width of its text.** A block with a
  2,000-character animation list pushed the window's minimum width to ~13,700 px — past the
  screen, with the preview panel off-screen entirely. Fix: every label whose text comes from
  the scene or the trace is an `ElidedLabel`.
- **manimgl's `Circle(color=...)` colour trap.** `color` is ignored; you need
  `fill_color`/`stroke_color`. The editor's `check` command warns about it by name.

None of these are in any tutorial. They're the tax you pay for building on top of a
rendering engine instead of next to it.

## What I'd tell you if you're building something similar

1. **Isolate the angry thing.** The GPU, the user's Python, and the third-party renderer
   all get to crash — in a process that isn't your editor. The companion-process bet paid
   for itself many times over.
2. **Make the interface first-class.** The editor↔host protocol is versioned, documented,
   and unit-tested. Every protocol bump was a small, safe change instead of a nightmare.
3. **Write down the decisions.** Nine ADRs mean I never re-litigate "why not snapshots?" —
   and superseded ones (0006 → 0007) show the thinking evolving, which is the honest record.
4. **Measure, or you're guessing.** The perf branch found real wins only because there was a
   baseline to beat.
5. **Bet on a runtime trace over static analysis** whenever behavior — not source — is the
   ground truth.

## What's next

The patch engine has room to grow: reordering blocks and inserting animations from a
library are the natural next steps, and both build on the same anchors. Packaging is
Windows-first right now, with macOS and Linux on the roadmap. And the whole thing is
scriptable end to end, so an agent can drive it as easily as a human can.

Five releases of the protocol later, 124 commits, ~17.7k lines of source against ~13.8k
lines of tests. The bet — *a live preview you edit instead of a render you wait for* —
held up.

If that sounds useful:

- **Source:** [github.com/AzadKshitij/manim-studio](https://github.com/AzadKshitij/manim-studio)
- **Release:** [v1.1.0](https://github.com/AzadKshitij/manim-studio/releases/tag/v1.1.0)
- **Part 1 (the story):** [How I built a realtime editor for ManimGL](part-1-how-i-built-manim-studio.md)

Thanks for reading. If you make Manim animations — or you're building a desktop app on top
of a GPU renderer — I'd love to hear what's slow for you.