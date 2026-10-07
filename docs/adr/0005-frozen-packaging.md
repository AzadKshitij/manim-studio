# ADR 0005 — Frozen packaging: one PyInstaller bundle, two executables

- Status: accepted (2026-10-06)
- Context: users and AI agents need a double-clickable Windows app plus the scriptable
  CLI without installing Python. The editor spawns helpers as `python -m <module>`
  (`preview_controller` → `manim_studio.preview_host`, `export` → manimgl launcher);
  a frozen exe cannot do that, and `manimlib` resolves data files (shaders,
  `default_config.yml`) and API sources relative to its on-disk package directory.

## Decision

1. **Re-invocation protocol** (`spawn.py`, single source of truth): frozen builds
   relaunch their own executable with an internal flag — `--preview-host …` or
   `--manimgl-export …` — which `frozen_entry` routes to the right module. Frozen
   detection is `getattr(sys, "frozen", False)` via `spawn.frozen()`, so dev checkouts
   keep using `python -m`.
2. **Two executables, one bundle** (`packaging/manim_studio.spec`, onedir):
   `ManimStudio.exe` (windowed GUI; bare path arg opens that file; no console flash
   for spawned children) and `manim-studio.exe` (console: full CLI for terminals and
   agents, and the worker exe frozen parents re-invoke). No onefile build — a
   onefile child would unpack hundreds of MB to `%TEMP%` on every preview restart.
3. **Spec collects what analysis cannot see**: manimlib data (`.yml`, `.glsl`) *and*
   its `.py` sources (the api-index AST scan reads them from `_MEIPASS/manimlib`
   via `find_manimlib_path` in frozen builds), moderngl-window shaders/fonts,
   `pyside6_scintilla` + `manimpango` natives, `pyglet`/`IPython`/`jedi` runtime
   imports, and every installed `*.dist-info` (manimlib calls
   `pkg_resources.get_distribution("manimgl")` at import time).
4. **Console-less stdio guard**: the windowed exe rebuilds std streams from dup'd
   fds when redirected (`ensure_standard_streams`), else uses the null device — a
   double-clicked GUI can never die on a stray `print`.
5. **Distribution** (`scripts/build.ps1`): quality gate → PyInstaller → smoke tests
   of the frozen exes (`--version`, `check`, `api-index`, manimgl `--help`
   dispatch, windowed dispatch) → portable zip (`ManimStudio-<ver>-portable.zip`)
   → Inno Setup installer (`ManimStudio-Setup-<ver>.exe`, per-user, no admin).

## Consequences

- `ffmpeg` (export) and LaTeX stay external requirements; `manim-studio latex
  install` still provisions TinyTeX per user.
- Everything frozen is covered by `test_spawn.py` / `test_frozen_entry.py` /
  `test_api_index.py::TestFrozenLookup`; the flag↔CLI-subcommand mapping and the
  `CLI_SUBCOMMANDS` mirror are test-guarded against drift.
- New CLI subcommand? Add it to the parser *and* `frozen_entry.CLI_SUBCOMMANDS`
  (the mirror test fails otherwise).
- First launch on a machine may trip SmartScreen (unsigned build); signing is a
  later step, not a code change.
