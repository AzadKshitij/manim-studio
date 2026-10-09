# Performance benchmarks

These are the numbers the rendering/timeline optimizations are judged against.
Nothing here asserts absolute timings — a workstation and a laptop CPU differ by
2× and that is not a regression. The point is to compare **the same machine
before and after** a change, so every run is recorded to JSON and diffed.

## Running

The benchmark is a pair of pytest tests behind the existing markers:

```powershell
# micro: frame codec + parser only, no GPU, ~3 s
uv run pytest tests/test_perf.py -m slow -s

# end-to-end: spawns a real 720p preview host (needs a GL context), ~15 s
uv run pytest tests/test_perf.py -m gl -s

# both, recorded into one file
$env:MANIM_STUDIO_PERF_REPORT = "docs/perf/after.json"
uv run pytest tests/test_perf.py -m "slow or gl" -s
```

`-s` prints the human-readable summary; `MANIM_STUDIO_PERF_REPORT` additionally
merges the run into a JSON file (later runs update matching keys, so micro and
end-to-end can be recorded separately).

`scripts/benchmark.ps1` wraps the recorded run.

## What is measured

**Micro** (`render_like_frame`, 1280×720 RGBA):

| metric | meaning |
| --- | --- |
| `micro.<codec>.encode_ms` | payload compression time for one frame |
| `micro.<codec>.decode_ms` | payload decompression time for one frame |
| `micro.<codec>.ratio` | uncompressed / compressed payload size |
| `parser.round_trip_ms` | `encode_frame` → `FrameParser.feed` in 64 KiB bursts |
| `editor.wrap_ms` / `editor.wrap_copy_ms` | wrapping a 720p RGBA buffer in a `QImage`, with and without the defensive copy |
| `editor.from_image_ms` / `editor.scale_{smooth,fast}_ms` | `QPixmap` conversion and both downscale modes to a 960×540 viewport |

**End-to-end** (real host, `tests/scenes/feature_showcase.py`, 1280×720):

| metric | meaning |
| --- | --- |
| `trace_seconds` | host start → runtime trace ready |
| `crawl_frames` / `crawl_seconds` / `crawl_fps` | filmstrip crawl throughput (idle loop) |
| `play_seconds` / `playback_fps` | wall time and frame rate for a full playback |
| `play_realtime_ratio` | `play_seconds / scene duration`; `> 1.0` means the GL thread fell behind |
| `host_timings.*` | per-phase avg/max ms reported by the host (`render`, `readback`, `encode`, `send`, `pose`, `crawl_*`) |

## Recorded baseline — 2026-10-07

Machine: Windows 11, AMD64 (8 CPUs), Python 3.13.12. Recorded in
`baseline.json`. This is **before** the pipeline optimizations.

| metric | baseline |
| --- | --- |
| micro zlib-1 encode / decode | 33.3 ms / 10.3 ms (8.99×) |
| parser round trip | 10.8 ms |
| crawl | 107 frames / 4.66 s = 22.96 fps |
| playback | 91 frames / 3.64 s = 25.02 fps, realtime ratio 1.233 |
| host `render` / `readback` / `encode` | 10.8 / 5.8 / 18.3 ms avg |
| host `crawl_capture` / `crawl_encode` | 8.3 / 12.5 ms avg |

## Codec comparison — 2026-10-07

`codec-comparison.json`, same 720p render-like frame
(`uv run --with zstandard --with lz4 python -m pytest tests/test_perf.py -m slow -s`):

| codec | encode ms | decode ms | ratio |
| --- | --- | --- | --- |
| lz4-block | 2.39 | 3.42 | 9.62× |
| **zstd-1** | **5.73** | **5.02** | **26.64×** |
| zstd-3 | 7.65 | 5.09 | 26.29× |
| zlib-1 (current) | 33.81 | 12.53 | 8.99× |
| zlib-6 | 61.01 | 7.05 | 29.42× |

zstd-1 compresses ~6× faster than zlib-1, decompresses ~2.5× faster, and at a
~3× better ratio. lz4 encodes marginally faster but ships ~2.8× more bytes and
decodes slower than zstd, so **zstd-1 is the chosen wire codec**, with a raw
(uncompressed) fallback when a payload would not shrink.

## Change log

### P1.1 — encode on the pump thread (2026-10-07)

`FramePump.offer` now queues **raw** pixels; the pump thread encodes and sends
them, so the GL thread only pays a lock + buffer swap. Recorded in
`after-p1.1.json`:

| metric | baseline | after P1.1 |
| --- | --- | --- |
| pump handoff (caller thread) | 34.3 ms (= inline encode) | **0.0029 ms/frame** |
| crawl | 22.96 fps | 26.96 fps |
| playback | 25.02 fps, ratio 1.233 | 27.67 fps, ratio 1.115 |

The handoff number is deterministic and is the real proof: the GL thread no
longer blocks on compression. The end-to-end numbers carry several-percent
run-to-run noise (this box is shared), so treat them as directional.

### P1.1b — zstd wire codec (2026-10-07)

`ipc` frames now carry a scheme byte and go through `frame_codec` (zstd-1,
zlib/raw fallback); `PROTOCOL_VERSION` is 3. Recorded in `after-p1.1b.json`:

| metric | baseline | P1.1 | P1.1b |
| --- | --- | --- | --- |
| crawl | 22.96 fps | 26.96 fps | **39.32 fps** |
| host `crawl_encode` | 12.5 ms | 13.0 ms | **1.34 ms** |
| host `encode` (playback) | 18.3 ms | 14.7 ms | **2.43 ms** |
| parser round trip | 10.8 ms | 11.4 ms | 7.8 ms |
| playback | 25.0 fps / 1.233 | 27.7 fps / 1.115 | 27.5 fps / 1.121 |

Crawl throughput is up 71% on the baseline and frame encoding is an order of
magnitude cheaper. Note: zstd compression contexts are not thread-safe and the
host encodes on both the pump and the idle/crawl thread, so `frame_codec`
caches one compressor **per thread**.

### P1.4 — drop the editor's defensive QImage copy (2026-10-07)

`PreviewController.decode_latest_frame` wrapped each decompressed frame in
`QImage(..., ).copy()`. PySide6 already keeps a reference to the source buffer
for the image's lifetime, so the copy was pure overhead on the GUI thread.
Verified by reading pixels back after a forced collection and 300 MiB of heap
churn. Recorded in `after-p1.4.json`:

| metric | before | after P1.4 |
| --- | --- | --- |
| editor wrap + copy (720p) | 1.19 ms | **0.003 ms** (wrap only) |

The editor still pays ~2.1 ms for `QPixmap.fromImage` and ~5.0 ms for the smooth
downscale per frame; those are the remaining GUI-thread costs that P1.3 targets.

### P1.2 — dedup identical frames in the pump (2026-10-07)

`FramePump` now compares each raw frame with the last one it actually sent and
drops exact repeats without re-encoding or sending them. During a `wait` or a
static hold manimgl redraws the same pixels every tick, so this removes real
work; the comparison runs on the pump thread, never the GL thread, and costs
~0.7 ms only when a frame did change. Recorded in `after-p1.2.json`:

| metric | after P1.1b | after P1.2 |
| --- | --- | --- |
| frames offered during playback | 93 | 93 |
| frames encoded / sent | 93 | **80** |
| frames deduped | — | **13** (~14%) |

This trims ~14% of the host's per-playback encode+send work (~29 ms) and the
matching wire traffic. It does **not** change `render` (still ~23 ms/frame), so
the realtime ratio is unchanged (~1.12). Note that `playback_fps` counts frames
*delivered*, so it falls by design once duplicates stop being transmitted —
compare `play_realtime_ratio`, not `playback_fps`, across this change.

### P1.3 — adaptive preview scaling (2026-10-07)

`PreviewPanel` now scales streamed frames with `FastTransformation` and, 150 ms
after the stream pauses, re-renders the last frame with
`SmoothTransformation`, so motion is cheap and stills stay crisp. Frames that
arrive while the viewport is hidden (minimised, other page) are stored and scaled
on show instead of on arrival. Recorded in `after-p1.3.json`:

| metric | always smooth | adaptive |
| --- | --- | --- |
| per streamed frame (720p → 960×540) | 5.2–7.0 ms | 4.4–6.1 ms |
| saving | — | **0.7–1.4 ms/frame** (~15–25%) |

Per-frame numbers move with machine load (this box is shared), so the saving is
quoted as a range across runs; within any single run fast is always cheaper, and
the one settle-time smooth scale per burst is negligible. `QPixmap.fromImage`
(~2.2 ms) is unchanged and remains the floor for the GUI-thread cost.

### P1.5 — playhead-first filmstrip crawl (2026-10-07)

`FilmstripCrawler` now fills the cache outward from the playhead instead of from
the start, and the host re-aims it (`prioritize`) when a seek or scrub moves the
playhead mid-crawl. Reordering is free: posing any block replays the scene from
the beginning anyway, so the total work is unchanged and only the order differs.
`time_to_playhead` records the wall time until the playhead's block is cached.
A/B on `feature_showcase` (initial frame = end):

| metric | sequential (before) | playhead-first |
| --- | --- | --- |
| `time_to_playhead` | 3.33 s | **0.25 s** |
| crawl throughput | 33.2 fps | 26.6 fps (run-to-run noise; same work) |

The playhead's region is ready ~13× sooner; the full crawl still takes the same
time and `crawl_fps` is unchanged within noise.

### R1 — single-pass filmstrip crawl (2026-10-09, branch `rendering`)

`FilmstripCrawler` no longer replays `construct()` once per block. After the
playhead's block (posed directly, so `time_to_playhead` is unchanged) it streams the
scene once: `ScrubEngine.stream_frames` suspends `construct()` on a greenlet (same GL
thread) at every frame of every uncached block. Equivalence is covered by
`test_streamed_frames_match_replay_poses` (cached frames vs replay poses).
A/B on `feature_showcase` at 720p, old and new code run back to back on the same
machine (`tests/test_perf.py -m gl`, `docs/perf/after-single-pass.json`):

| metric | per-block replay (before) | single pass |
| --- | --- | --- |
| full crawl (`crawl_seconds`) | 1.06 s | **0.62 s** |
| crawl throughput | 100.6 fps | **172.9 fps** |
| `time_to_playhead` | 0.10 s | 0.11 s (unchanged) |
| reload -> filmstrip rebuilt (3 edit shapes) | 1.30–1.46 s | **0.86–0.93 s** |

Load itself (`load_timings.total`, ~0.12–0.14 s) is unchanged; this change targets the
crawl only. The showcase is a small, light scene (24 blocks), so the saving is the
floor: replay cost grows with the Python/mobject work before each block, and the old
crawler paid it once per block.

### R0 — real-time fps metric (30 fps target) (2026-10-09, branch `rendering`)

`tests/test_perf.py -m gl` now reports a `realtime` block (`docs/perf/baseline-realtime.json`)
for real-time playback of the showcase at 720p, judged against 30 fps:

- `effective_fps` = 30 × scene seconds / wall seconds — the rate the scene itself plays
  at. This is the headline: the host skips frames identical to the previous one during
  static waits, so *delivered* `fps` understates what the viewer sees.
- `start_latency_ms` (PLAY → first frame), `p5_fps`, frame-interval p50/p95/max.
- `sustains_realtime`: effective fps ≥ 95% of target **and** no frame gap ≥ 2 frame times.

Baseline on `feature_showcase` (three runs, host at 720p): **28.6 effective fps** (95% of
30; play ratio 1.07, i.e. ~7% slower than scripted), start latency ~68 ms, steady interval
p50 ≈ 33.7 ms, 0 unexplained missing frames -> `sustains_realtime` true.

Correction: an earlier revision of this metric reported 27.6 fps and a "~245 ms hitch".
The two gaps (~240 ms and ~170 ms) are the scene's static `wait` blocks, where the host
deliberately sends no frame because nothing changed (`frames_deduped`). The metric now
counts a gap as a stall only for frames beyond the host's own deduped count
(`unexplained_missing`), and measures wall time from the first frame (start latency is
reported separately). The remaining ~7% is per-block overhead (pre_play/begin_animations
plus the construct code between blocks), not a stall.

### R2 — reuse unchanged blocks across reloads (2026-10-09, branch `rendering`)

On reload the filmstrip now keeps the frames of blocks the edit cannot influence
(`reuse.plan_reuse`, ADR 0009) instead of re-crawling the whole scene. Measured as
**reload command → filmstrip complete**, `feature_showcase` 720p, old and new code
alternated back to back on the same (noisy) machine, two rounds each
(`tests/test_perf.py -m gl -k Reload`, `docs/perf/after-reuse.json`):

| edit | blocks reused | before (single-pass, no reuse) | after |
| --- | --- | --- | --- |
| retime the last block | 12 / 15 | 1.41–1.52 s | **0.76 s** (-50%) |
| edit a middle block | 5 / 15 | 1.41–1.51 s | **1.13 s** (-22%) |
| edit a non-play line mid-scene | 6 / 15 | 1.47–1.66 s | **1.10 s** (-27%) |

The saving is proportional to how much of the scene sits *before* the edit. The load
itself (trace + initial scene, ~0.25–0.33 s here) is unchanged, and the not-yet-reused
tail is still crawled by replaying `construct()` once in skip mode through the cached
prefix, which is the next target. Absolute times are higher than R1's because the
machine was busier; only the old/new pairs within one round are comparable.

Pitfall caught by measuring: the first version of the user-module fingerprint resolved
a path for every loaded module and added ~0.8 s to every load. It now rejects modules
by string prefix before touching the filesystem (~4 ms), reported as
`load_timings.signature`.

### R3 — one `construct()` per load (2026-10-09, branch `rendering`)

When the preview opens on the end state (the default), the traced scene *is* the initial
scene, so the host keeps it instead of running `construct()` a second time. A/B on
`feature_showcase` 720p, old and new code alternated, three rounds
(`load_timings.total` from the `log` event; reload command -> `log` event is within 5 ms of it):

| edit | before | after |
| --- | --- | --- |
| retime last block | 0.117–0.136 s | **0.067–0.074 s** |
| edit middle block | 0.122–0.127 s | **0.066–0.069 s** (-44%) |
| edit non-play line | 0.114–0.137 s | **0.066–0.076 s** |

This is the time before the new frame appears after an edit, so it is the latency the
user feels (the filmstrip crawl in R1/R2 happens in the background). `initial_frame="start"`
still runs the second, truncated `construct()`.

### Summary — `rendering` branch vs. the commit it started from (2026-10-09)

Same benchmark (`tests/test_perf.py -m gl`, 720p `feature_showcase`), old code (`4e39d1f`,
extracted to a scratch directory) and new code alternated over 3 rounds on an otherwise idle
machine; medians. Raw reports: `before-rendering-branch.json`, `after-rendering-branch.json`.

| metric | before | after | change |
| --- | --- | --- | --- |
| edit -> new frame shown (reload -> `log`) | 0.126–0.131 s | **0.069–0.075 s** | **-44%** |
| retime last block -> filmstrip complete | 1.077 s | **0.284 s** | **-74%** |
| edit mid-scene block -> filmstrip complete | 1.094 s | **0.477 s** | **-56%** |
| edit mid-scene non-play line -> filmstrip complete | 1.186 s | **0.476 s** | **-60%** |
| full crawl (first load) | 0.857 s (≈ 120 fps) | **0.550 s (≈ 200 fps)** | **-36%** |
| real-time playback | 28.5 effective fps | 28.5 effective fps | unchanged (no regression) |

Real-time playback is ~95% of the 30 fps target with no unexplained stalls; the remaining
~7% is per-block overhead and was not changed by this work.

### R5 — retimes keep later blocks in scenes with updaters, and for edits typed in the source (2026-10-09)

A timeline retime already kept later blocks (`557c0cf`), but only for `SetDuration` patches
and only when the source had no updater at all (a text search for `always_redraw`, `dt`,
`.time`, ...). `scenes/Welcome.py` uses `always_redraw`, and editing a `wait()` typed
directly into the file was not recognised as a timing edit, so both re-rendered every block.
Now the decision uses what actually ran (the tracer flags blocks that had a `dt` updater
alive) and an AST comparison that ignores literal durations (ADR 0009). `Welcome.py`
(22 blocks, 32.4 s, 972 frames at 320x180), their commit and this change alternated back to
back, two rounds, reload -> filmstrip complete:

| edit | blocks reused: before -> after | crawl before | crawl after |
| --- | --- | --- | --- |
| first `wait` 0.5 -> 0.8 | 1 -> **21** of 22 | 6.14-6.28 s | **0.35-0.37 s** (-94%) |
| first play `run_time` 2.767 -> 3.0 | 0 -> **21** of 22 | 6.44-6.70 s | **0.50 s** (-92%) |
| last `wait` 2 -> 2.5 (no change expected) | 21 -> 21 | 0.59-0.60 s | 0.47 s |

Frames moved this way are compared with a from-scratch crawl in
`test_retiming_the_first_wait_of_welcome_keeps_the_later_blocks` (mean abs diff < 1/255).
A retimed block with a `dt` updater alive still invalidates everything after it
(`test_retiming_a_block_with_a_dt_updater_reuses_nothing_after_it`).
