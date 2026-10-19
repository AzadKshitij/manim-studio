# 0010. Per-scene render settings and the managed source block

Status: accepted

## Context

Every scene rendered at one host-wide size (`--size`, default 1280x720) with manimgl's
default background, and the preview always ran at 30 fps. Users need a scene to choose its
own pixel size (including portrait, for phone-shaped video), frame rate, background and a
colour palette, and the preview, export and the plain `manimgl` command must all agree.

Checked against the installed manimgl 1.7.2 (`scene/scene.py`, `extract_scene.py`,
`config.py`, `camera/camera.py`):

- The CLI passes `camera_config=dict()` into `Scene.__init__`, so a class-level
  `default_camera_config` is honoured and wins over the global `camera:` config.
- Passing `camera_config` explicitly wins over the class attribute, so an out-of-process
  caller can override a scene without touching its source.
- With a window, `Scene.__init__` forces `camera_config["fps"] = 30`. The preview's frame
  rate is therefore always 30; only exports can use another value.
- `refresh_uniforms` builds `frame_rescale_factors` from the module-level 16:9 constants
  (`FRAME_WIDTH`, `FRAME_HEIGHT`), and the vertical scale also follows the frame height.
  Any other pixel aspect is stretched sideways. A spike rendered a 1080x1920 scene with a
  2-unit square: it came out 154 px wide and 488 px tall. Correcting only
  `frame_rescale_factors[0]` to `2 / (FRAME_HEIGHT * aspect)` makes it 488x488, and the
  correction is a no-op for 16:9.

## Decision

- **Storage.** Settings live in `<scene>.studio.json`, version 2, under `scenes`, keyed by
  scene class name. The timeline patches sit beside them, and saving either part keeps the
  other. Version 1 files still load. Pure logic is in `scene_settings.py`.
- **Source.** Every settings change writes a managed block into that scene's class, between
  marker comments. The block is an `__init__(self, **kwargs)` override that sets
  `camera_config` (resolution, fps, background, opacity, frame shape) and then calls
  `super().__init__`. A `camera_config` the caller passes in still wins, so the sidecar that
  the editor and export use keeps precedence. For non-16:9 sizes only, the same `__init__`
  also corrects manimgl's horizontal scale. The block optionally includes `STUDIO_PALETTE`
  (the swatch colours). It is spliced by `ast` location. Code outside the markers is never
  touched, another class's block is left alone, and a class that already defines `__init__`
  keeps its code (the settings are still saved, and the status line says why the code was not
  updated).
- **Where the change lands.** If the file has no unsaved edits, the block is written and the
  file saved, as one undo step. If it has unsaved edits, the block changes in the editor only,
  so nothing typed is overwritten on disk. "Apply settings to source" stays as an explicit
  re-sync, and "Reset scene settings" removes the scene's block along with the stored settings.
- **Runtime.** The preview host takes `--settings-file` and passes the settings into
  `make_scene`, which applies the aspect correction to the camera it creates. The export
  launcher takes `--settings-file` and wraps `Scene.__init__`, so the studio's settings are the
  last merge: they win over the class block. Explicit resolution or fps from a caller are
  applied on top of the stored settings.
- **Precedence.** Studio runs (editor, `manim-studio` CLI) use the sidecar. A plain `manimgl`
  run uses the block. Every change keeps the two in step; a block edited by hand can drift
  until the next change or an explicit re-sync, and Studio then uses the sidecar.
- **Palettes** are a background plus swatches, not a remapping of the `BLUE`/`RED` constants.
  Built-in palettes are checked for WCAG non-text contrast of at least 3:1.
- **Layouts** are two presets, landscape (the existing layout) and portrait (code over the
  timeline on the left, preview full height on the right). Switching reparents the same panel
  widgets into new splitters. The sizes last used for each preset are remembered separately.

## Consequences

- A change of size, fps or background restarts the preview host. The host fixes its window and
  camera at start, and `Command.RESIZE` has no handler, so no protocol change was needed and
  `PROTOCOL_VERSION` is unchanged.
- The preview runs at manimgl's 30 fps, and the dialog says so. Exports use the stored fps.
- Settings are per scene class, so one file can hold several scenes with different sizes.
- Scene settings write the `.py` when the user changes them, which is a deliberate departure
  from the first version of this decision, where only "Apply" wrote the code. The request was
  that the code follows the settings. Timeline edits keep the rule of `docs/adr/0007`: they
  stay in the sidecar until applied.
- The aspect correction is a monkeypatch of one camera instance's `refresh_uniforms`, not a
  change to manimlib. It can be removed if a later manimgl derives the frame rescale from the
  pixel shape.
