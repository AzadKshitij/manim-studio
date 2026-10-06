# LaTeX support (on-demand TinyTeX)

`Tex`, `Brace` and friends need external programs: `latex` + `dvisvgm`.
Manim Editor does **not** bundle them. It detects a system install and,
when missing, offers a one-click user-local download.

## User flow

1. Open a scene using `Tex`/`Brace` without LaTeX → the preview error says
   what is missing (or that the compile failed) and points at
   **Tools > LaTeX setup…**.
2. `Tools > LaTeX setup…` shows status (ready/missing, paths, location) and
   offers **Install** (~100–200 MB portable TinyTeX into your user folder,
   no admin), **Remove**, **Diagnose**, and progress/cancel.
3. **Diagnose** runs a real compile of manimgl's default template and reports
   the exact TeX error (first run can take a few minutes while MiKTeX builds
   fonts). After a fresh TinyTeX install it runs automatically.
4. The status bar shows `LaTeX: ok|missing` and refreshes after install or
   after a LaTeX failure. Installing LaTeX restarts the preview host so the
   new toolchain is visible immediately.

## Locations

| OS      | Install dir                                              |
| ------- | -------------------------------------------------------- |
| Windows | `%LOCALAPPDATA%/ManimEditor/latex`                       |
| macOS   | `~/Library/Application Support/ManimEditor/latex`        |
| Linux   | `~/.local/share/manim-editor/latex` (`$XDG_DATA_HOME`)   |

Override with `MANIM_EDITOR_LATEX_DIR` (used by tests and custom setups).

## Download source

Bundles are pinned to a TinyTeX monthly release (`TINYTEX_VERSION` in
`latex_install.py`, currently `v2026.10`) from the official
`rstudio/tinytex-releases` GitHub releases, with published SHA-256 digests
verified before extraction:

| OS            | Bundle (~size)                              |
| ------------- | ------------------------------------------- |
| Windows       | `TinyTeX-1-windows-*.exe` self-extractor (~73 MB) |
| macOS         | `TinyTeX-1-darwin-*.tar.xz` (~67 MB)        |
| Linux x86_64  | `TinyTeX-1-linux-x86_64-*.tar.xz` (~53 MB)  |
| Linux ARM64 / musl | matching `-arm64` / `-linuxmusl` variant |

The old `yihui.org/tinytex/TinyTeX-1.{zip,tgz,tar.gz}` links are dead
(404) — do not use them. To adopt a newer TinyTeX release, update
`TINYTEX_VERSION` + the five digests in `TINYTEX_SOURCES` (and only those).

## How it works

- `src/manim_editor/latex.py` (stdlib only): `latex_status()`,
  `probe_compile()`, `runtime_env()`, `apply_runtime_path()`,
  `is_latex_error()`.
- `src/manim_editor/latex_compat.py`: runtime fix for a manimgl 1.7.2 bug —
  it passes the TeX Live-only `-no-pdf` flag to `latex`, which MiKTeX rejects
  as an unknown option (upstream fixed this in manim PR #2298 by using the
  flag only for `xelatex`). The patch mirrors that fix without touching
  site-packages and is a no-op on TeX Live.
- `src/manim_editor/latex_install.py`: downloads the pinned portable TinyTeX
  release bundle for the current OS (version + SHA-256 in
  `TINYTEX_SOURCES`), verifies the checksum, unbundles it (Windows
  self-extracting exe via `exe -y`, no UI/admin; `.tar.xz` elsewhere),
  runs `tlmgr install` for the manimgl default-template package set
  (`MANIM_TEX_PACKAGES`), writes an `.installed` marker. Downloads retry
  transient failures and fail fast on HTTP errors with the URL included.
- `src/manim_editor/editor/latex_dialog.py`: dialog + `QThread` workers for
  install and diagnose. Network/extraction/tlmgr/compiles never run on the
  GUI thread.
- Runtime wiring: `cli.main`, `editor/app.run_editor`, `preview_host.run`,
  `scene_runner.run_construct`/`run_trace`/`render_still` call
  `apply_runtime_path()` + `patch_manimgl_latex_compat()`;
  `export.run_export` uses `runtime_env()` and runs
  `manim_editor.manimgl_launcher` (manimgl CLI + compat fix) so `manimgl -w`
  exports get both fixes.
- Errors: `format_scene_error()` appends the right hint to `LatexError`s —
  "missing" only when the binaries are actually absent, otherwise "installed
  but the compile failed; run Diagnose".
- CLI: `manim-editor latex status|install|uninstall|diagnose [--json] [--dir DIR]`;
  `manim-editor check --json` includes a `latex:` object.

## Troubleshooting

- **Diagnose hangs or times out**: your TeX distribution may be waiting for a
  package-installation prompt. Configure automatic package installation (e.g.
  MiKTeX Console → Settings → Always install missing packages on-the-fly), or
  install the app's TinyTeX, which is non-interactive.
- **MiKTeX**: supported through the compatibility fix above. If compilation
  still fails, Diagnose shows the TeX log errors.
- **Running as Administrator**: MiKTeX logs a security warning for elevated
  processes. Prefer a normal (non-elevated) launch; TinyTeX is unaffected.

## Headless / CI

```powershell
uv run manim-editor latex status
uv run manim-editor latex install
uv run manim-editor check scenes/my_scene.py --json
```

TinyTeX is LPPL-licensed and downloaded at install time, so nothing
LaTeX-related is committed or shipped with the app itself.
