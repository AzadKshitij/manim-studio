# Changelog

All notable changes to Manim Studio are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- **Mobject inspector** — a gear beside each line that configures a mobject
  (`circle = Circle(...)`, `circle.shift(UP)`) opens an inspector panel with every
  parameter ManimGL exposes for that class: radius, fill, stroke, position, font and font
  size, gradients (with "+" to add colours), points as number boxes and `t2c` colour
  maps row by row. Field types come from each parameter's annotation, so unset colours,
  numbers and text get the right widget. "Add call" appends any chained setter, such as
  `.scale(...)`. Apply writes the change into the source as one undo step, saves and
  refreshes the preview. The property list is discovered from the installed manimlib and
  cached per version (ADR 0012).
- Colour literals keep their colour-blind timeline identity (ADR 0011): recolouring a
  call keeps its timeline edits. The colour swatches of ADR 0011 are replaced by the
  gears above.

- **Agent surface** (ADR 0013). `manim-studio frame FILE --at SECONDS` renders a scene at any
  time to a PNG. `manim-studio contact-sheet FILE` tiles frames into one image, each labelled
  with its time and block name. Both honour the timeline edits saved in `<scene>.studio.json`.
- **MCP server** — `manim-studio mcp --root DIR` serves ten tools over stdio: `check`, `trace`,
  `frame`, `contact_sheet`, `export`, `list_templates`, `api_index`, `describe_mobject`,
  `new_scene` and `write_scene`. Every path must stay inside the root. Images come back inline.
  It works whether or not the editor is open. There is no AI inside the app; the agent is
  yours.
- **`agent-init`** writes agent instructions into a project: a Claude Code skill, a Cursor
  rule, a managed block in `AGENTS.md`, and MCP config for Claude Code, Cursor and opencode.
  Files you have edited are kept unless you pass `--force`.

## [1.1.0] - 2026-10-10

### Added

- **Editable timeline** — timeline edits are anchored patches stored in
  `<scene>.studio.json`; per-animation lanes, an editable block inspector,
  orphan tray, icon toolbar, block context menu, exact-duration entry,
  snapping (frames, 0.1 s, edges), zoom buttons/fit-selection, scrollbar,
  insert-wait-at-playhead, loop range and playback speed. Exports bake the
  edits in (`--no-sidecar` to skip).
- Block highlight that follows the editor caret; Play from playhead.
- **Themes** — Chalkboard theme and loadable JSON theme files; new editor
  header/toolbar, rounded popup menus and a regenerated app icon.
- "Reload preview, discard frame cache" action; LaTeX caching.
- Welcome starter scene.

### Changed

- Renamed the product from "Manim Editor" to **Manim Studio**: the import
  package is now `manim_studio`, the CLI is `manim-studio`, and the Windows
  org/bundle identity is `ManimStudio`.
- Renamed the LaTeX override environment variable `MANIM_EDITOR_LATEX_DIR` to
  `MANIM_STUDIO_LATEX_DIR`.
- Rendering performance: single-pass filmstrip crawl, filmstrip reuse across
  reloads and retimes, traced scene reused as the initial scene, preview
  frames scaled fast then settled smooth.
- Preview protocol bumped to v4 (timeline patches).
- Removed the render-preview scrub bar; block actions moved to the toolbar.

### Fixed

- A block with many animations no longer pushes the preview off screen.
- Reliable play/pause, loop and review-speed behaviour (loop stops when turned
  off, pause stops a running loop, speed changes real wall-clock pace).
- Later blocks stay reachable after a section is lengthened.
- Selected code stays readable with a themed selection band.

### Repository

- `LICENSE` (MIT), CI workflow (format, lint, types, tests on Ubuntu +
  Windows), tagged release workflow, Dependabot config, community files and
  issue/PR templates.

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

[Unreleased]: https://github.com/AzadKshitij/manim-studio/compare/v1.1.0...HEAD
[1.1.0]: https://github.com/AzadKshitij/manim-studio/compare/v1.0.0...v1.1.0
[1.0.0]: https://github.com/AzadKshitij/manim-studio/releases/tag/v1.0.0
[0.1.0]: https://github.com/AzadKshitij/manim-studio/releases/tag/v0.1.0
