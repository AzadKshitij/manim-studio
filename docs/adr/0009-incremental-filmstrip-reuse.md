# ADR 0009 — Incremental filmstrip: reuse unchanged blocks across reloads

- Status: accepted (2026-10-09); amends ADR 0003 item 5 ("every reload replaces the cache")
- Context: after every edit the host threw the whole filmstrip away and re-crawled the
  entire scene (ADR 0003), although frames of blocks the edit cannot influence are
  pixel-identical. On the showcase the re-crawl was ~90% of the time-to-ready after an edit.

## Decision

1. **Single-pass crawl** (`scrub.ScrubEngine.stream_frames`): `construct()` runs once on a
   greenlet (same OS thread, so the GL rules of AGENTS.md §2 hold) and is suspended at
   every frame of every uncached block, instead of one replay per block.
2. **Reuse across reloads** (`reuse.plan_reuse`, pure): a cached block is kept when
   everything that ran up to it is unchanged. The edit is the region between the common
   line prefix and suffix of the previous and the new *executed* source (timeline
   patches applied). Reuse requires that region to lie inside the scene's `construct()` in
   both texts, above no block's last-reachable line (the end of the outermost loop for
   blocks in loops), and that the new trace describes the block identically; reuse stops
   at the first block that does not match. Blocks whose call lives in a helper, nested
   function or lambda are never reused.
3. **Frames, never state**: only encoded frames are cached (ADR 0002 still rules out
   restoring mobject state). The filmstrip keeps the indices `[round(start*fps),
   round(end*fps))` of reused blocks; the boundary frame is re-rendered by the next block.
4. **Inputs the source text cannot show** invalidate everything: render size, fps and a
   `(path, mtime, size)` fingerprint of the user modules the scene imports
   (`scene_runner.user_module_signature`). `reload`/`load` accept `fresh: true` to discard
   the cache explicitly (assets such as images or SVGs are not tracked).
5. No hashing: plain comparisons are exact and cheaper than digesting, so no hash
   dependency is needed. (If a persistent on-disk frame cache is added later, use a
   fast non-cryptographic hash such as xxh3, not sha256.)

## Consequences

- Retiming or editing the last block re-renders only the tail; an edit early in
  `construct()` still re-crawls everything after it.
- **Timing-only edits shift instead of re-rendering.** When the scene file is unchanged
  and the only patches that differ between two runs are timeline `SetDuration` edits, a
  retime pushes later blocks along the timeline without changing a pixel of them, provided
  nothing can make a frame depend on elapsed time. `reuse.plan_reuse(timing_only=True)`
  then keeps every cached block except those whose own duration changed, and
  `Filmstrip.retain_shifted` re-keys their frames by the block's frame delta. Conditions:
  both runs are *time independent* (no `add_updater`, `always_redraw`, `scene.time`, `dt`
  in the executed text and no user modules: `reuse.source_is_time_dependent`, a
  conservative text check), the blocks are the same calls in the same places, and a
  block has the same number of frames before and after the shift (a fractional-frame
  shift can round a block to a different length; that block is re-rendered). On a
  24-block scene a retime keeps 23 blocks and completes the cache in ~0.3 s versus ~0.95 s
  for a rebuild; `test_a_timing_only_edit_keeps_later_blocks_cached_and_pixel_identical`
  compares a shifted frame with one from a from-scratch cache.
- **Source edits and scenes with updaters also qualify** (extends the rule above). Typing a
  new duration into the file is the same kind of edit as a timeline retime: when the old and
  new source are identical once literal `run_time=` / `wait()` durations are removed
  (`reuse._differs_only_in_timing`, an AST comparison that also ignores comments and
  formatting), the timing rule applies even though the file changed. And the text check
  above is only the *strict* path: a scene that uses updaters is judged on what actually ran.
  The tracer records per block whether a time-based updater (one taking `dt`, manimgl's own
  rule) was alive (`TraceSnapshot.block_dynamic`); `always_redraw`, value trackers and
  other non-`dt` updaters do not make a frame depend on elapsed time. Later blocks are
  reused unless a *retimed* block had a `dt` updater alive, in which case only the blocks
  before it are reused. Still excluded: scenes reading the clock (`self.time`, `get_time`,
  `import time`, found on the AST) and scenes importing user modules. Measured on
  `scenes/Welcome.py` (22 blocks, 32 s): retiming the first `wait` keeps 21/22 blocks
  (957 frames) and the cache completes in 0.35-0.37 s instead of 6.1-6.3 s.
- A reused block owns its frames up to, not including, the next block's first frame; the
  *last* playing block has no follower, so it also keeps its end frame (otherwise a scrub
  to the very end would land one frame early).
- Streamed blocks are stepped frame by frame like real playback, while uncached-prefix
  replays use skip mode, so updater-driven mobjects can differ by sub-pixel amounts
  depending on what was cached; `test_editing_the_last_block_reuses_everything_before_it`
  pins incremental frames to a fresh crawl (mean abs diff < 1/255).
- `log` gains `reuse` and `load_timings` (protocol v5).
