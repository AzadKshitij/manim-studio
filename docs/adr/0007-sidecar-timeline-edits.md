# ADR 0007 — Timeline edits are anchored patches in a sidecar, applied as virtual source

- Status: accepted (2026-10-09); supersedes ADR 0006
- Context: The timeline was a visualization with one throwaway edit. Dragging a
  block's edge stored a per-block-*index* duration override in memory
  (`TimingOverrides`), sent to the host as `set_timing`. Consequences:
  - the override was discarded on every reload, so any code edit threw the
    retiming away;
  - indices are positions, so inserting a `play()` above a retimed block would
    have moved the override onto the wrong block even if it had survived;
  - nothing but "insert hold" could change the scene from the timeline, and the
    CLI export never saw the retiming at all.

  The goal is a timeline that is used for *editing*: retime, delete and (later)
  reorder and insert, with edits that stay attached to the code they were made
  on.

## Decision

1. **Edits are patches anchored on a call site, not an index** (`patches.py`).
   `site_index.py` statically indexes every `self.play()` / `self.wait()` and
   gives each a fingerprint: enclosing function, call kind, a hash of the call
   *with its duration removed*, and an ordinal that separates identical calls.
   Retiming a call (by hand or on the timeline) therefore never changes its
   identity; changing its animations does. A patch is `SetDuration` or `Delete`
   on an `Anchor(site_id, occurrence?)`; `occurrence` narrows a loop body to one
   iteration.
2. **The scene file is not modified.** Patches live in `<scene>.studio.json`
   (`sidecar.py`) with their own undo/redo (`PatchStack`). They are applied by
   *splicing text on exact AST spans* (not `ast.unparse`), so comments and
   formatting survive and "Apply to source" is a minimal diff.
3. **Everything runs the same patched text.** The host executes the patched
   source as the scene file (`scene_runner.load_scene_module(source=...)` with
   `co_filename` kept as the real path), so preview, scrub, filmstrip, playback
   and export cannot disagree. Exports get `--patch-file`; the plan reads the
   sidecar by default, which also closes the CLI parity gap.
4. **Edits that cannot be applied are orphaned, never moved.** If a call's
   fingerprint no longer exists the edit is reported (`log.patches.orphaned`),
   counted in the status bar, kept in the sidecar and never applied to another
   call. The user can discard orphans or fix the code.
5. **Loops fan out visibly.** Editing a loop body edits the call site, so every
   iteration changes (the block shows a `xN` badge). A per-iteration edit is
   compiled to a small runtime helper, `__studio__.dur(site, {iteration: s},
   default)` (`patch_runtime.StudioRuntime`), whose counters are reset on every
   `construct()`; such edits cannot be baked into plain source and stay as
   timeline edits when the user applies the rest.
6. **"Apply to source" is the exit.** `Timeline > Apply timeline edits to source`
   writes the baked text into the buffer as a single undoable edit, saves, and
   keeps only the edits that could not be baked.
7. **Protocol v4**: `load`/`reload` carry `patches`, `set_patches` re-runs the
   scene with a new set, `log` reports per-block `sites`, the applied/orphaned
   `patches` report and a `cause` (`"patches"` reloads keep the user's zoom,
   playhead and selection). The host also accepts `--patch-file` because its
   first load comes from argv. `set_timing`/`timing`, `TimingOverrides` and the
   export `timing_factors`/`--timing-file` are removed.

## Consequences

- Retimes survive unrelated code edits and follow their call when code is added
  above it; this is covered by GL end-to-end tests against a real host.
- A patched run needs a full host reload (a ~0.1–1 s re-trace) instead of the old
  proportional rescale. The timeline shows the stretch immediately from a local
  projection and reconciles when the host's `log` arrives.
- What you see in the editor buffer differs from what runs while edits are
  pending. The status-bar chip, the corner flags on edited blocks and
  `Timeline > Show timeline edits as diff…` make that explicit.
- Per-animation lanes, reordering and inserting animations from a library build
  on the same anchors (`Move`/`Insert` patches); they are not part of this ADR.
- `scrub.TimingScaler` remains for the review-speed multiplier only.
