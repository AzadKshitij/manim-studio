# Manim Studio 1.1.0 — Release Notes

First versioned update after the initial shareable 0.1.0 build. All changes below
landed after the 0.1.0 bundle (2026-10-06) and are included in the frozen
`ManimStudio-1.1.0-portable.zip` and `ManimStudio-Setup-1.1.0.exe`.

## Highlights

- **Syntax highlighting** — Pygments-powered Python highlighting with colour
  themes in the code editor (`0f66c6b`).
- **Autocompletion + calltips** — Jedi-backed completions: in-scope names
  complete instantly, member completions and call signatures as you type
  (`3b35687`, `769ea3f`).
- **App icon** — proper window/taskbar icon plus installer icon; drop a custom
  `.ico` next to the app and it gets picked up (`ab98c99`, `0d1fe85`).
- **Memory status** — live filmstrip cache / RAM readout in the status bar plus
  an App stats dialog; preview host reports filmstrip bytes and RSS via
  STATS v2 (`6730d37`, `165e7bb`, `11ae859`).
- **Frozen packaging** — one-folder bundle (`ManimStudio.exe` GUI +
  `manim-studio.exe` CLI), portable zip and per-user Inno Setup installer
  (ADR 0005, `d3c385d`).

## Fixes

- Completion offsets stay on the caret (LF newlines preserved, character — not
  byte — offsets) (`fc50f42`, `769ea3f`).
- Comment and string words no longer pollute instant completions (`b28fd47`).
- New runtime deps declared: `pygments>=2.19`, `jedi>=0.19`.

## What's changed (commits since 0.1.0)

```text
11ae859 feat(stats): live cache/RAM status bar and App stats dialog
165e7bb feat(protocol): report filmstrip bytes and host RSS via STATS (v2)
6730d37 feat(stats): add pure RAM/cache formatting and RSS helpers
b28fd47 fix(editor): keep comment and string words out of instant completions
fc50f42 fix(editor): keep LF newlines so Jedi completion offsets stay on the caret
0d1fe85 feat(editor): pick up a dropped-in .ico for the app icon
769ea3f fix(editor): complete in-scope names instantly and use character offsets
ab98c99 feat(editor): show app icon in window and taskbar
3b35687 feat(editor): Jedi completion, member completion and calltips
0f66c6b feat(editor): Pygments-based syntax highlighting
d3c385d feat(packaging): frozen build support (ADR 0005)
```

## Install

- **Portable:** unzip `ManimStudio-1.1.0-portable.zip` anywhere, run
  `ManimStudio.exe`. No install, no admin.
- **Installer:** run `ManimStudio-Setup-1.1.0.exe` (per-user, no admin prompt,
  Windows 10+ x64).
- First launch may trigger Windows SmartScreen (unsigned build):
  More info → Run anyway.

## Requirements

- Windows 10+ x64, OpenGL 3.3 context for preview.
- `ffmpeg` on `PATH` for video export only (not needed to edit/preview).
- Optional LaTeX for `Tex` mobjects — inside the app run
  `manim-studio latex install` (provisions per-user TinyTeX).

## Verify

Built with `scripts\build.ps1`: ruff format check (64 files), ruff check,
mypy (35 source files, no issues) and the full pytest suite green
(474 passed), plus frozen smoke tests (`--version`, `check`,
`templates`, `api-index`, manimgl dispatch, windowed host dispatch).
