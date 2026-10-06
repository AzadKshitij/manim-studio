# ADR 0004 — On-demand LaTeX via in-app TinyTeX install

- Status: accepted
- Date: 2026-10-06

## Context

`Tex`/`Brace` scenes fail with manimgl's bare `LatexError: LaTeX compilation
failed` whenever the `latex` + `dvisvgm` programs are absent — the common case
on fresh Windows/macOS machines. `pip` cannot provide LaTeX, and shipping a
full TeX Live (~5 GB) with the app is unacceptable.

## Decision

- Do **not** bundle LaTeX with the app.
- Detect system LaTeX first; when missing, offer a one-click user-local
  portable-TinyTeX install from **Tools > LaTeX setup…** (also
  `manim-editor latex install`), with progress, cancel and remove.
- Fix manimgl 1.7.2's `-no-pdf` incompatibility with MiKTeX at runtime
  (`latex_compat.py`, mirroring upstream manim PR #2298; exports go through
  `manim_editor.manimgl_launcher`). This makes an existing system MiKTeX work
  without a download.
- Provide a real end-to-end **Diagnose** (`probe_compile`) that compiles
  manimgl's default template and reports the exact TeX error, so "binaries
  found" is never confused with "compilation works".
- Keep detection/install pure-stdlib (`latex.py`, `latex_install.py`); run
  downloads/compiles off the GUI thread; prepend the install `bin/` to `PATH`
  only for our own processes (`runtime_env` / `apply_runtime_path`).
- Turn `LatexError` into an actionable message everywhere via the existing
  `format_scene_error` funnel (no protocol change).

## Consequences

- App stays small; LaTeX (~100–200 MB) downloads once per user on demand.
- Requires internet at install time; TinyTeX release is pinned with SHA-256
  digests in `latex_install.py` (`TINYTEX_SOURCES`), so a moved upstream URL
  fails loudly instead of installing something unexpected. Adopting a new
  release = bump `TINYTEX_VERSION` + digests (the legacy
  `yihui.org/tinytex/TinyTeX-1.*` links already 404).
- If TinyTeX URLs change, only the installer module + this ADR need updates.
