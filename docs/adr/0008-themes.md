# ADR 0008 — Themes are data: one JSON file drives palette, style sheet, editor and timeline

- Status: accepted (2026-10-09)
- Context: Colours were hard-coded in `app.py` (palette), `code_editor.py`
  (Scintilla + Pygments style names), `preview_panel.py` and `timeline_widget.py`
  (QColor literals). Changing the look meant editing four files, and the greys
  did not agree with each other.

## Decision

1. A theme is **base colours + metrics** (`theme.py`, pure stdlib). Hover, pressed,
   selection and soft status fills are derived in `derive()`, never stored.
2. The file format is JSON (`"format": "manim-studio-theme"`), identical to what the
   design-system page (`docs/design/studio-design-system.html`) exports. Missing or
   invalid values fall back to Chalkboard, so partial files load.
3. `editor/styling.py` is the only place that turns a theme into `QPalette` and a
   Qt style sheet. Widgets that paint themselves (`TimelineWidget`) or style labels
   (`PreviewPanel`, `BlockInspector`) read `current_theme()` and expose
   `apply_theme()`; `MainWindow.set_theme()` fans a switch out to all of them.
4. Built-ins: Chalkboard (default), Paper (light), Night Violet. User themes are
   `*.json` files in `~/.manim-studio/themes` (`MANIM_STUDIO_THEMES` overrides).
   View > Theme lists both and remembers the choice in `QSettings`.
5. Combo boxes, spin boxes and check boxes stay Fusion-drawn: styling their borders
   in QSS removes the native arrows and indicators.

## Consequences

- Timeline lane colours come from the theme's six lanes (`hash(name) % 6`), so the
  same animation name keeps its colour within a theme.
- Syntax colours come from the theme, not Pygments styles; `python_highlight` still
  decides the token kinds.
- Theme metrics are pixels; the Scintilla font size is converted to points.
