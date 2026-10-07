# Changelog

All notable changes to Manim Studio are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Changed

- Renamed the product from "Manim Editor" to **Manim Studio**: the import
  package is now `manim_studio`, the CLI is `manim-studio`, and the Windows
  org/bundle identity is `ManimStudio`.
- Renamed the LaTeX override environment variable `MANIM_EDITOR_LATEX_DIR` to
  `MANIM_STUDIO_LATEX_DIR`.

### Added

- `LICENSE` (MIT), a GitHub Actions CI workflow (format, lint, types, offscreen
  tests on Ubuntu + Windows), a tagged release workflow that builds the frozen
  bundle/portable zip/installer, and a Dependabot config.
- `.gitattributes` line-ending policy and an updated `.gitignore`.
- Community files: `CONTRIBUTING.md`, `CODE_OF_CONDUCT.md`, `SECURITY.md`, and
  GitHub issue/PR templates.

## [1.0.0] - 2026-10-07

First public release.

### Added

- **Syntax highlighting** — Pygments-powered Python highlighting with colour
  themes in the code editor.
- **Autocompletion + calltips** — Jedi-backed completions: in-scope names
  complete instantly; member completions and call signatures appear as you type.
- **App icon** — proper window/taskbar icon plus installer icon; a custom `.ico`
  dropped next to the app is picked up automatically.
- **Memory status** — live filmstrip cache / RAM readout in the status bar plus
  an App stats dialog; the preview host reports filmstrip bytes and RSS via
  STATS v2.
- **Frozen packaging** — one-folder bundle (`ManimStudio.exe` GUI +
  `manim-studio.exe` CLI), portable zip and a per-user Inno Setup installer.

### Fixed

- Completion offsets stay on the caret (LF newlines preserved; character — not
  byte — offsets).
- Comment and string words no longer pollute instant completions.

## [0.1.0] - 2026-10-06

### Added

- Initial shareable build: Scintilla code editor, embedded realtime preview
  (frame streaming) plus external GL window, runtime-traced timeline with
  replay-based scrubbing and a filmstrip cache, live IPython shell, video/still
  export via `manimgl -w`, and the headless CLI (`check`, `trace`, `export`,
  `new`, `preview`).

[Unreleased]: https://github.com/AzadKshitij/manim-studio/compare/v1.0.0...HEAD
[1.0.0]: https://github.com/AzadKshitij/manim-studio/releases/tag/v1.0.0
[0.1.0]: https://github.com/AzadKshitij/manim-studio/releases/tag/v0.1.0
