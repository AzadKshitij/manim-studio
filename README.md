# Manim Editor

A production-grade, realtime desktop editor for [ManimGL](https://github.com/3b1b/manim)
scenes — Windows, macOS and Ubuntu 24+.

- **Left:** code editor (Scintilla) with `manimlib` autocomplete, error markers and
  full undo/redo.
- **Right:** realtime preview — embedded frame streaming (default) or an external
  GL window — plus a live IPython shell wired to the running scene.
- **Bottom:** real timeline built from a runtime trace of `play()`/`wait()` — drag
  to scrub the live scene smoothly, wheel to pan, Ctrl+wheel to zoom, arrows to
  step one frame, plus a rendered-video scrub slider. A background filmstrip cache
  makes scrubbing feel like a video while staying fresh after every edit.
- **One click:** export MP4 (up to 4K/60) or a still via headless `manimgl -w`.

Everything is also drivable without the GUI, so AI agents can create, validate,
trace and export scenes with the CLI.

## Requirements

- Python 3.13 and [`uv`](https://docs.astral.sh/uv/)
- A working OpenGL 3.3 context (preview, traces, stills)
- `ffmpeg` on `PATH` (video export only)
- Optional: a LaTeX installation for `Tex` mobjects

## Quick start

```powershell
uv sync --all-groups
uv run manim-editor new scenes/intro.py --template circle_to_square
uv run manim-editor gui scenes/intro.py
```

In the editor: edit + save to auto-reload the preview (F5 forces a reload), F6
plays from the playhead, F7 stops, Ctrl+R renders a scrubbable preview video,
Ctrl+E exports the final video, Ctrl+` opens the live shell.

## CLI (agent-friendly)

```text
manim-editor gui   [FILE]            open the desktop editor
manim-editor check FILE              syntax + scene discovery (fast, no GL)
manim-editor trace FILE [--json]     runtime play/wait trace (blocks, stats)
manim-editor still FILE -o OUT.png   render the final frame
manim-editor export FILE -o OUT.mp4  render a video (--quality preview|low|medium|high|4k)
manim-editor new FILE --template K   scaffold a scene (--list to see templates)
manim-editor preview FILE            standalone GL preview host
manim-editor templates --json        scene + insertable snippet templates
manim-editor api-index --json        offline scan of the installed manimlib API
```

Exit codes: `0` success, `1` user/scene error, `2` usage error. JSON goes to stdout;
scene output is captured so it can never corrupt structured output.

## Architecture in one paragraph

A Qt shell (`manim_editor.editor`) never imports `manimlib`. A companion process
(`manim_editor.preview_host`) owns the only OpenGL context, traces scenes at runtime
and streams frames to the editor over a loopback protocol (`docs/protocol.md`).
Export shells out to `manimgl -w` for deterministic, non-interactive rendering.
The pure core (`timeline_model`, `tracer`, `ipc`, `templates`, `api_index`) has no
GUI/GL dependencies and is exhaustively unit tested.

## Development

```powershell
uv run ruff format src tests && uv run ruff check src tests
uv run mypy
uv run pytest -m "not gl and not slow"   # fast loop
uv run pytest                              # everything, incl. GL + ffmpeg export
```

PowerShell helpers in `scripts/` wrap the same commands:

| script | purpose |
| --- | --- |
| `scripts\run.ps1 [FILE] …` | open the GUI from source (args pass through to `manim-editor gui`) |
| `scripts\lint.ps1 [-Fix]` | format check + ruff + mypy (`-Fix` applies safe autofixes) |
| `scripts\test.ps1 [-m …]` | pytest (args pass through) |
| `scripts\check.ps1 [-Fast]` | full pre-commit gate: lint + pytest (`-Fast` skips GL/slow tests) |
| `scripts\clean.ps1 [-Venv]` | remove `dist/`, `build/`, caches, `__pycache__` (`-Venv` also drops `.venv`) |
| `scripts\build.ps1 …` | release pipeline: gate → PyInstaller bundle → smoke tests → portable zip → installer (see below) |

## Packaging (Windows installer + portable)

`scripts\build.ps1` produces everything under `dist/` (see `docs/adr/0005-frozen-packaging.md`):

- `dist\ManimEditor\` — the application folder: `ManimEditor.exe` (double-click
  GUI) + `manim-editor.exe` (console CLI for terminals and AI agents).
- `dist\ManimEditor-<version>-portable.zip` — unzip-and-run anywhere.
- `dist\ManimEditor-Setup-<version>.exe` — Inno Setup installer (per-user, no
  admin prompt); skipped with `-SkipInstaller` or when Inno Setup 6 is missing.

The frozen app still needs `ffmpeg` on `PATH` for video export and optionally
LaTeX (`manim-editor latex install` provisions TinyTeX per user).

See `AGENTS.md` for the full contract, `docs/protocol.md` for the wire protocol,
`docs/adr/` for decisions, and the skills in `.opencode/skills/` for scene-authoring
and codebase workflows. `tests/scenes/feature_showcase.py` is the canonical scene
that exercises every editor, timeline and scene-API feature.
