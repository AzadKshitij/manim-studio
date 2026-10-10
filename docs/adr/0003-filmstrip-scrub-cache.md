# ADR 0003 — Smooth scrubbing via an in-memory filmstrip cache

- Status: accepted (2026-10-06)
- Context: Scrubbing is replay-based (ADR 0002), which is always fresh but costs
  one scene replay per pointer position (measured ~10–120 ms per pose). The spec's
  original smoothing idea — render a low-quality mp4 and scrub it with
  `QMediaPlayer` — was reconsidered and set aside as the primary mechanism:
  auto-rendering a video after every save adds a second renderer competing for the
  GPU, is minutes-slow for long scenes, can seek only to keyframes, and is stale
  for as long as the render takes (a second source of truth next to the live host).

## Decision

Add a **host-side filmstrip cache**:

1. `filmstrip.Filmstrip` is a bounded LRU of *already-encoded* frames keyed by
   integer frame index at the camera fps. Budget: 7200 frames / 256 MiB by
   default; oversized individual frames are refused rather than evicting the cache.
2. `scrub.FilmstripCrawler` fills the cache with **one construct pass**, block by
   block: replay to the block start (skip mode), then advance frames incrementally
   — O(1) per frame, exactly like manimgl's own playback loop minus timing sleeps
   and video encoding. Work is chunked (`step()` renders a few frames per call) so
   the host keeps servicing commands.
3. The idle loop steps the crawler only when no scrub request is pending, so
   interactive scrubbing always wins over background filling.
4. A scrub request is served **cache-first**: an exact index, else the nearest
   cached index within 3 frames, is sent directly through the frame pump with no
   GL work. Cache misses fall back to the replay pose (ADR 0002) and store the
   rendered frame for the next visit.
5. Freshness is structural (amended by ADR 0009: unchanged blocks are carried over): every successful load/reload replaces the filmstrip and
   drops the crawler, so a cached frame can never belong to an older revision.
   `exec`/live-shell mutations already only affect the display scene, exactly as
   before this change.
6. Progress is reported with a `filmstrip` event (`frames`, `done`, `capped`,
   `trace_id`), rendered as a small indicator in the preview panel.

## Consequences

- Scrub inside a crawled region is effectively free and smooth in both directions;
  only the first visit of a region pays the replay cost.
- Memory is bounded and tunable; the default caps are deliberately generous for
  short-to-medium scenes and stop crawling with `capped=true` beyond them.
- Frame indices use `round(t * fps)`; crawl frames follow manimgl's per-block
  quantization, so boundary lookups may be off by one frame. The nearest-within-3
  probe absorbs this (and is why cache hits do not require exact indices).
- The mp4 transport (`MediaTransport`) remains for reviewing the final render and
  for export; it is no longer the mechanism that makes scrubbing smooth.
- Follow-up opportunity (not required): stream frames at the editor viewport size
  via the `resize` command so the GUI never has to rescale large frames.
