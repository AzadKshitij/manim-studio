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
