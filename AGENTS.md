# AGENTS.md — Manim Studio development guide

This repository builds **Manim Studio**: a production-grade desktop editor for
[ManimGL](https://github.com/3b1b/manim) scenes. Left: code editor. Right: realtime
preview. Bottom: timeline scrub. Target platforms: Windows, macOS, Ubuntu 24+.

This file is the contract for **AI agents and humans** working in this repo. Rules here
are mandatory. If a design decision must change, write an ADR in `docs/adr/` and update
this file — do not silently diverge.

## 1. Commands

```powershell
uv sync --all-groups                 # create/refresh the environment (Python 3.13)
uv run ruff format src tests         # formatting is mandatory before commit
uv run ruff check src tests          # lint
uv run mypy                          # static typing gate for src/
uv run pytest                        # full suite; Qt runs offscreen, GL auto-skips
uv run pytest -m "not gl and not slow"   # fast inner loop
uv run pytest -m gl                  # OpenGL integration tests (real GPU needed)
uv run pytest -m slow                # real ffmpeg export test
uv run manim-studio --help           # app CLI
uv run manim-studio gui <file.py>    # open the editor
```

Never commit with failing `ruff check`, `ruff format --check`, `mypy`, or `pytest`.
A change that cannot be unit-tested must ship with an integration test or an ADR
explaining why not.

## 2. Architecture map

| Module | Responsibility | May import |
| --- | --- | --- |
| `manim_studio/timeline_model.py` | Pure data model for trace blocks, px/time math, JSON (de)serialization | stdlib only |
| `manim_studio/site_index.py` | Static index of `self.play()`/`self.wait()` call sites with stable fingerprints | stdlib only |
| `manim_studio/patches.py` | Anchored timeline edits (`SetDuration`, `Delete`) and the text-splice engine that applies them | stdlib, `site_index` |
| `manim_studio/patch_runtime.py` | `__studio__` helper for per-iteration (loop) edits | stdlib only |
| `manim_studio/sidecar.py` | `<scene>.studio.json` IO, patch temp files and the `PatchStack` undo history | stdlib, `patches` |
| `manim_studio/tracer.py` | Runtime tracer wrapping `play()`/`wait()` and scene API calls; tags blocks with their call site | stdlib, `timeline_model` |
| `manim_studio/python_highlight.py` | Pygments-powered Python token spans for the code editor | stdlib, `pygments` |
| `manim_studio/python_completion.py` | Jedi-backed completion candidates and call signatures | stdlib, `jedi` |
| `manim_studio/ipc.py` | JSON-line protocol + binary frame framing | stdlib, `frame_codec` |
| `manim_studio/frame_codec.py` | Frame payload codec (zstd default, zlib/raw fallback) | stdlib (+ `zstandard` imported lazily) |
| `manim_studio/filmstrip.py` | Bounded LRU cache of encoded scrub frames | stdlib only |
| `manim_studio/scrub.py` | Replay posing (`ScrubEngine`) + single-pass filmstrip crawler (construct() suspended on a greenlet, same GL thread) | `manimlib`, `greenlet` lazily |
| `manim_studio/theme.py` | Colour themes: model, built-ins, JSON theme files, derived colours (ADR 0008) | stdlib only |
| `manim_studio/templates.py` | Scene + prefab templates | stdlib only |
| `manim_studio/api_index.py` | Offline AST scan of installed `manimlib` for autocomplete | stdlib only |
| `manim_studio/scene_runner.py` | Discover/trace/validate scenes, headless execution | `manimlib` lazily |
| `manim_studio/export.py` | Video export via `manimgl -w` subprocess; bakes the sidecar's edits in | `subprocess`, `sidecar` |
| `manim_studio/preview_host.py` | Owns the **only** OpenGL context; renders and streams frames | `manimlib`, moderngl |
| `manim_studio/latex.py` | LaTeX detection, runtime env and end-to-end probe | stdlib only |
| `manim_studio/latex_install.py` | On-demand TinyTeX download/install | stdlib, `latex` |
| `manim_studio/latex_compat.py` | Runtime fix for manimgl 1.7.2 `-no-pdf`/MiKTeX bug | `manimlib` lazily |
| `manim_studio/manimgl_launcher.py` | Export entry point (manimgl CLI + LaTeX fix + `--patch-file`) | `manimlib` lazily |
| `manim_studio/editor/styling.py` | `Theme` -> `QPalette` + Qt style sheet; applied app-wide | PySide6, `theme` |
| `manim_studio/editor/*` | Qt shell, Scintilla, timeline widget, panels | PySide6 |
| `manim_studio/cli.py` | Agent-facing CLI (`trace`, `export`, `check`, `new`, `preview`) | everything above |

Status: Stage 1 (external GL window) and Stage 2 (embedded frame streaming) are
implemented; timeline v1 (media transport scrub) and v2 (zoom/pan/click-jump/drag)
are implemented; timeline v3 (live scrub) is intentionally deferred per the spec.
Export, stills and the live shell are implemented. See `docs/protocol.md` for the
wire protocol and `docs/adr/` for architectural decisions.

Hard dependency rules:

1. Pure modules (`timeline_model`, `site_index`, `patches`, `patch_runtime`, `sidecar`,
   `tracer`, `ipc`, `filmstrip`, `templates`, `api_index`, `theme`) must never import Qt,
   `manimlib`, or `moderngl`. They are the fully-tested core.
2. `editor/*` must not import `manimlib`/`moderngl` at module import time. The editor
   talks to the preview host via IPC only.
3. GL lives in `preview_host` and nothing else. **One context, one thread.** The Qt GUI
   thread never touches OpenGL.
4. Never call `Scene.play()` from a file-watcher callback. Watcher events are always
   turned into Qt signals and queued.

## 3. Preview architecture (Stage 1 + Stage 2)

`preview_host` is a companion process; the editor spawns it and talks JSON-lines over a
loopback TCP socket (editor listens, host connects back; port passed on argv).

- **Stage 1 (external window):** host opens a visible GL window. Editor never embeds.
- **Stage 2 (embedded):** host hides its GL window, renders into the default framebuffer,
  reads it back with `fbo.read()`, compresses RGBA (zstd, zlib or raw), and streams length-prefixed
  frames over a dedicated frame connection. The editor expands each frame into a
  `QImage` on the GUI thread from a `QTimer`-driven poll.
- Frame backpressure: at most one in-flight frame; if the socket is congested the host
  drops the frame and keeps the GL loop healthy. The UI must never block on rendering.
- Reload reuses the existing GL context and window; never recreate the context on edit.

Protocol details live in `docs/protocol.md`. Any change to the protocol bumps
`PROTOCOL_VERSION` in `ipc.py` and must update both sides + tests.

## 4. Timeline architecture

- Timeline data comes from a **runtime trace** (not static AST), so loops/branches are
  correct: `log.json` is a list of `{name, start, duration, index}` blocks.
- v1: transport slider bound to a low-quality mp4 (`QMediaPlayer`) for video review.
- v2: interactive strip — wheel pan, Ctrl+wheel zoom, click/drag to position the
  playhead, zoom-to-block, hover info, arrow-key frame stepping.
- v3: **implemented** as replay-based scrubbing (`scrub.py`, ADR 0002): the target
  time is posed by replaying the scene in skip mode and interpolating the containing
  block's animations to the exact alpha, so `always_redraw`/updaters keep working.
  The host coalesces scrub requests (latest wins) so drags never queue replays.
- Smoothness: an in-memory **filmstrip cache** (`filmstrip.py`, ADR 0003) is filled
  by a block-granular crawler that renders each frame at O(1) per frame; scrub
  requests are served cache-first and fall back to a replay pose on a miss. The
  cache is replaced on every load/reload, so cached frames are never stale.

## 4b. Timeline edits (ADR 0007)

The timeline is an editing surface, not just a view.

- Edits are **patches anchored on a call site** (a fingerprint of the call with its
  duration removed), never on a block index, stored in `<scene>.studio.json` next to
  the scene. The `.py` is untouched until `Timeline > Apply timeline edits to source`.
- The host, scrub, filmstrip, playback and export all run the **same patched source
  text** (`apply_patches` + `load_scene_module(source=...)`); never add a second path
  that retimes at runtime.
- An edit whose call no longer exists is **orphaned** (reported, kept, never applied
  elsewhere). Do not add fuzzy re-anchoring that could move an edit onto another call.
- Per-iteration edits go through `__studio__.dur`; they cannot be baked into plain
  source (`apply_patches(runtime=False)` reports them as orphaned/unbakeable).
- Editor code talks to the host through `PatchController` -> `PreviewController
  .set_patches`; the host answers with a `log` event (`cause="patches"`) that carries
  each block's `sites` and the applied/orphaned report.

## 5. Code style

- Python 3.13, full type annotations on every public function; `ruff format` output is
  the canonical formatting; line length 100.
- Qt: PySide6 signals/slots only. Long work happens in worker threads/processes; the GUI
  thread only does UI. No `time.sleep` on the GUI thread. Prefer `QTimer` over threads
  for polling.
- Threading: communicate with signals (`Signal(object)`) or `QMetaObject.invokeMethod`;
  never mutate widgets from worker threads.
- Logging: `logging.getLogger(__name__)`; no `print` in library code (`cli.py` may print
  to stdout as its contract; the preview host uses logging/stderr only).
- Errors: never bare `except:`. User-visible failures become a status line + log entry;
  the editor must stay alive when a scene crashes. Tracebacks from user scenes are
  parsed to `line`/`message` and shown as editor markers.
- Follow ManimGL naming conventions (`mobject`, `run_time`, `construct`, ...) in
  scene-facing code and templates.

## 6. Testing

- Everything in the pure core has unit tests; Qt widgets have offscreen smoke tests
  (`QT_QPA_PLATFORM=offscreen`, `pytest-qt`); GL behavior has `@pytest.mark.gl`
  integration tests that spawn the real preview host; `@pytest.mark.slow` covers the
  real ffmpeg export path.
- `tests/test_editor_integration.py` is the full-stack contract: a real `MainWindow`
  drives a real host through trace → frames → seek → live shell → play/stop.
- `tests/scenes/feature_showcase.py` is the canonical scene that exercises every editor
  and scene-API feature: play/wait, add/remove, save_state/undo, updaters,
  always_redraw, loops/branches, groups, text, camera moves. It must trace cleanly.
  Add a feature? Extend the showcase and its assertions in the same commit.
- Tests must be deterministic: no fixed sleeps, no absolute wall-clock dependencies,
  no reliance on window managers. Use `FakeClock`/monotonic injection for timers.
- GL/LaTeX-dependent tests skip (not fail) when the environment cannot support them.

## 7. Git / review protocol

1. Small, single-purpose commits; message style: `feat(scope): ...`, `fix(scope): ...`,
   `test(scope): ...`, `docs: ...`, `chore: ...`.
2. Before every commit, run the full gate:
   `uv run ruff format src tests && uv run ruff check src tests && uv run mypy && uv run pytest`.
3. Self-review the diff (`git diff --staged`) for: correctness, dead code, missing
   tests, leftover debug prints, unrelated edits. Fix findings before committing.
4. Never commit generated media (mp4/png renders), `.venv`, or caches.

## 8. AI-agent workflows

The editor is designed to be driven headlessly by agents:

```powershell
uv run manim-studio check scenes/foo.py            # discover scenes + syntax check
uv run manim-studio trace scenes/foo.py --json     # runtime blocks, stats, timing
uv run manim-studio export scenes/foo.py -o out.mp4 --quality low   # honours foo.studio.json
uv run manim-studio export scenes/foo.py --no-sidecar               # render the source as written
uv run manim-studio new scenes/foo.py --template circle_to_square
uv run manim-studio preview scenes/foo.py           # standalone GL preview window
```

The CLI emits machine-readable JSON on stdout when `--json` is passed. Exit codes:
`0` success, `1` user/scene error, `2` usage error. `manim-studio check` is the fastest
way to validate generated scenes before opening the editor.

To author a scene, load the `manim-scene-authoring` skill (or read
`.opencode/skills/manim-scene-authoring/SKILL.md`); it documents the CLI loop,
manimgl naming conventions and templates. For codebase work, load
`manim-studio-dev` (`.opencode/skills/manim-studio-dev/SKILL.md`).
