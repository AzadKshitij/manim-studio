# ADR 0002 — Timeline scrubbing is replay-based, not snapshot-based

- Status: accepted (2026-10-06)
- Context: The original specification deferred live scrubbing ("v3") because the
  obvious implementation — `save_state()` at every block boundary and drive
  `anim.interpolate(alpha)` from a restored snapshot — breaks with
  `always_redraw` and updaters: a `SceneState` does not capture an animation
  mid-flight, and restored mobject copies can detach from the closures that keep
  redrawing them.

## Decision

Scrubbing is performed by **replaying** the scene, not by rewinding a snapshot:

1. On load the host traces the scene as usual and keeps the block list.
2. The first scrub lazily creates a dedicated scrub scene (its own FBO set,
   separate from the scene used for playback/seek) and wraps its `play`/`wait`.
3. `ScrubEngine.pose(t)` rewinds that scene (mobjects, time, counters, undo
   stacks, RNG seed) and re-runs `setup()` + `construct()` in skip-animations
   mode. Blocks before the target execute in the fast path; the block containing
   `t` is *intercepted*:
   - a play: animations are built with `prepare_animation`, begun, and each is
     interpolated to `alpha = (t - block.start) / animation.run_time` — exactly
     the alpha the normal playback loop would compute — then `EndScene` stops the
     replay, leaving the scene posed mid-animation;
   - a wait: updaters advance by `t - scene.time`.
4. The host renders one frame from that pose and streams it.

Because the normal animation/update machinery runs, `always_redraw` and updaters
behave as they do when playback pauses on a frame, and loops/branches are
replayed at runtime rather than trusted to a snapshot.

Transport: a new `scrub {time}` control command.  The host coalesces requests
(latest wins) so pointer-rate drags never queue replays; each pose is fast
because replays run with `skip_animations=True` and no rendering except the
single final frame.

## Consequences

- Scrubbing works for arbitrary scenes, including updaters and `always_redraw`;
  the deferred v3 concern is addressed without state snapshots.
- Cost per scrub step is one replay of the scene's Python (measured ~5–50 ms for
  test scenes); the host drops stale requests instead of buffering them.
- A scene with expensive per-frame Python (huge loops, heavy Tex construction)
  can still be slow to scrub; the pointer simply outruns the preview, which is
  the accepted trade-off.
- Playback and seeking keep using the original display scene; scrubbing never
  disturbs them.
