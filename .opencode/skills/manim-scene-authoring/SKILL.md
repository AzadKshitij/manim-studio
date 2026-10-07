---
name: manim-scene-authoring
description: Use when asked to create, edit, validate or export a ManimGL scene with Manim Studio (e.g. "build me a CircleToSquare scene"). Covers the CLI workflow, manimgl API conventions, templates and trace expectations.
---

# Authoring scenes for Manim Studio

You can fully drive the editor headlessly. Use the CLI, then the GUI only when the
user wants to watch.

## Workflow

```powershell
# 1. scaffold from a template (blank | circle_to_square | text_intro | three_d)
uv run manim-studio new scenes/intro.py --template circle_to_square

# 2. static validation (syntax + scene discovery) — fast, no GL
uv run manim-studio check scenes/intro.py --json

# 3. runtime trace — proves the scene runs and shows the timeline
uv run manim-studio trace scenes/intro.py --json

# 4. a quick visual check without a window
uv run manim-studio still scenes/intro.py -o preview.png

# 5. render a distributable video
uv run manim-studio export scenes/intro.py -o intro.mp4 --quality high --fps 60

# 6. open the editor when the user wants to iterate interactively
uv run manim-studio gui scenes/intro.py
```

`trace --json` returns `blocks` (one per `play`/`wait`), `stats` and
`total_duration`. Use it to verify expectations: if the user asked for "three
rotations", the trace must contain three `Rotate` blocks.

## manimgl API conventions (this is NOT Manim CE)

- Creation animation is `ShowCreation`, not `Create`.
- Scene base classes: `Scene`, `InteractiveScene`, `ThreeDScene`.
- Standard imports at the top of every scene: `from manimlib import *`.
- Mobjects: `Circle`, `Square`, `Rectangle`, `Line`, `Arrow`, `Dot`, `Text`, `Tex`,
  `VGroup`, `Group`, `Polygon`, `RegularPolygon`, `Star` does not exist — use
  `RegularPolygon(n=5)`.
- Animations: `ShowCreation`, `Uncreate`, `Write`, `FadeIn`, `FadeOut`, `Transform`,
  `ReplacementTransform`, `Rotate`, `Indicate`, `FadeTransformPieces`.
- `mobject.animate.<method>(...)` builds a `_MethodAnimation`; all keyword animation
  arguments (`run_time`, `rate_func`, `lag_ratio`) work there.
- Camera: `self.frame.animate.reorient(theta, phi, height?)`.
- 3D: `ThreeDAxes`, `Sphere`, `Surface`, `Cube`.
- Updaters: `mob.add_updater(lambda m, dt: ...)`, `always_redraw(lambda: ...)`.

### Sharp edge: `Circle(color=...)` renders red

In manimgl, `Circle`, `Dot` and `Ellipse` default `stroke_color` to red and pass it
explicitly, so `Circle(color=YELLOW)` only sets the invisible fill — the visible
stroke stays red. Always colour those classes with `stroke_color=` (or `set_color()`):

```python
circle = Circle(stroke_color=BLUE)      # correct
dot = Dot(fill_color=RED)               # Dot is filled; use fill_color
circle = Circle(color=BLUE)             # WRONG: renders with a red stroke
```

`manim-studio check` detects this pattern and prints a warning on the offending line.
Other shapes (`Square`, `Rectangle`, `Line`, `Arrow`, `RegularPolygon`, `Text`) pass
`color=` through normally.

### `self.embed()` is ignored in the preview

The editor runs scenes in a background host process; a terminal IPython embed would
block it forever (it reads stdin, which the editor never writes to). `scene.embed()`
is therefore a logged no-op — use the editor's **Live shell** dock (Ctrl+`) instead,
which exposes the same scene API (`play`, `wait`, `add`, `remove`, `undo`, ...,
plus `checkpoint_paste`).

## Scene structure that traces well

```python
from manimlib import *


class Intro(Scene):
    def construct(self) -> None:
        circle = Circle(color=BLUE)
        square = Square(color=YELLOW)
        self.play(ShowCreation(circle), run_time=1.5)
        self.wait(0.5)
        self.play(Transform(circle, square), run_time=1.5)
        self.wait(1.0)
```

- Keep `run_time=` on every `play` — the timeline uses the requested run times.
- Use loops/branches freely: the trace is a **runtime** trace, so loop iterations
  become individual timeline blocks.
- `save_state()` / `undo()` / `redo()` work and are reported in trace stats.
- Put `# checkpoint: name` comments where `checkpoint_paste()` workflows should resume.
- Avoid absolute paths; assets are resolved by manimgl from its configured dirs.

## Editor templates (Insert menu / `templates --json`)

Snippets available for interactive insertion: circle, square, text, transform,
fade_in, fade_out, wait, group, updater, always_redraw, camera_move, loop,
condition, save_state. The same list (`manim-studio templates --json`) includes the
exact code, formatted for insertion inside `construct`.

## Debugging a failing scene

1. `uv run manim-studio check scenes/x.py` — syntax / missing scene class.
2. `uv run manim-studio trace scenes/x.py` — full traceback with the scene line number.
3. Common causes: manimgl-vs-CE names (`Create`), missing `run_time` defaults making
   tiny waits quantize to a full frame, `Tex` without a TeX installation (guard it or
   skip), input variables that are undefined at trace time.
4. In the GUI, errors appear as editor markers and in the Live shell dock; fix and
   press F5 (or just save — auto-reload is on).
