# Manim Studio 1.1.0

## Added

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

## Changed

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

## Fixed

- A block with many animations no longer pushes the preview off screen.
- Reliable play/pause, loop and review-speed behaviour (loop stops when turned
  off, pause stops a running loop, speed changes real wall-clock pace).
- Later blocks stay reachable after a section is lengthened.
- Selected code stays readable with a themed selection band.

## Repository

- `LICENSE` (MIT), CI workflow (format, lint, types, tests on Ubuntu +
  Windows), tagged release workflow, Dependabot config, community files and
  issue/PR templates.

**Full changelog:** https://github.com/AzadKshitij/manim-studio/compare/v1.0.0...v1.1.0
