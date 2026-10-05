# Manim Editor

A production-grade, realtime desktop editor for [ManimGL](https://github.com/3b1b/manim)
scenes — Windows, macOS and Ubuntu 24+.

- **Left:** code editor (Scintilla) with `manimlib` autocomplete, error markers.
- **Right:** realtime preview — external GL window (Stage 1) or embedded framebuffer
  streaming (Stage 2).
- **Bottom:** timeline scrub built from a runtime trace of `play()`/`wait()`.
- **One click:** export a distributable video via headless `manimgl -w`.

## Quick start

```powershell
uv sync --all-groups
uv run manim-editor new scenes/intro.py --template circle_to_square
uv run manim-editor gui scenes/intro.py
```

No GPU? The editor still runs; preview and export need a working OpenGL context and
`manimgl` runtime.

## Agent / CLI usage

```powershell
uv run manim-editor check scenes/intro.py          # syntax + scene discovery
uv run manim-editor trace scenes/intro.py --json   # runtime timeline trace
uv run manim-editor export scenes/intro.py -o out.mp4 --quality low
uv run manim-editor preview scenes/intro.py        # standalone preview host
```

See `AGENTS.md` for development rules, `docs/` for architecture and protocol notes.
