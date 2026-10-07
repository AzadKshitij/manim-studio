# ADR 0006 — Exports bake the editor's retiming into the render

- Status: accepted (2026-10-08)
- Context: The timeline lets the user drag a block's right edge to retime that
  section. The live host honours the retiming for scrubbing and playback (ADR
  0002 and `TimingScaler`), but exports shell out to `manimgl -w` through
  `manim_studio.manimgl_launcher`, which re-runs the scene with its original
  `run_time`s. A user who retimed a section therefore got a preview that matched
  the timeline and an exported video that did not. Baking the retiming was
  tracked as deferred work ("T4").

## Decision

Bake the retiming into the export by passing it to the launcher:

1. `build_export_plan` accepts `timing_factors: Mapping[int, float]`
   (`{block_index: factor}`). It validates the factors, writes them to a hashed
   JSON file under the temp dir, and appends `--timing-file <path>` to the
   manimgl argv. `ExportPlan` carries the normalised factors as a tuple.
2. `manim_studio.manimgl_launcher.main` strips `--timing-file` from `sys.argv`
   **before importing anything that imports manimlib** — `manimlib.config`
   parses `sys.argv` at import time, so a leftover argument would make its
   argparse parser exit with code 2.
3. When factors are present the launcher wraps `Scene.run` so that every scene
   gets a `TimingScaler(scene, factors)` (speed 1.0: retiming only). Block
   indexes come from manimgl's own `num_plays`, the same counter the tracer and
   the host use, so the factors line up with the traced blocks.
4. The window supplies the factors: `MainWindow._timing_factors()` derives
   `duration / base_duration` per overridden block from the `TimingOverrides`
   stack, and every plan built from the window (export dialog, block still,
   preview video, `export_video_to`) forwards them.

## Consequences

- The exported video, the block still and the preview render all match the
  retimed timeline, closing the preview/export mismatch.
- Retiming stays a pure editor-side override: no scene source is rewritten and
  no state is stored in the scene, so exports remain reproducible and the
  original file is untouched.
- The mechanism reuses `TimingScaler`, so host playback and export share one
  implementation of "scaled run_time" semantics.
- Only per-block duration factors are baked; the review-speed multiplier is not
  (it is a preview-only convenience).
- A stray `--timing-file` written for a scene whose `num_plays` ordering changes
  between trace and export (nondeterministic control flow) would mis-scale; the
  scene's own determinism is the same assumption the tracer already relies on.
