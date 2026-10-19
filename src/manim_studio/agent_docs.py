"""Text that ``manim-studio agent-init`` writes into a project: the agent skill and the managed
block for AGENTS.md (ADR 0013). Pure text, no imports beyond the standard library.

The skill is also committed under ``.opencode/skills/manim-scene-authoring/SKILL.md``; a test keeps
that copy identical to :data:`SKILL_BODY`.
"""

from __future__ import annotations

__all__ = [
    "AGENTS_BEGIN",
    "AGENTS_END",
    "MCP_SERVER_NAME",
    "SKILL_BODY",
    "SKILL_DESCRIPTION",
    "SKILL_NAME",
    "agents_block",
]

SKILL_NAME = "manim-scene-authoring"
SKILL_DESCRIPTION = (
    "Use when asked to create, edit, check, render or debug a ManimGL scene with Manim Studio, "
    "such as 'make a scene where a circle becomes a square'. Covers the MCP tools and CLI "
    "equivalents, the ManimGL API, hex colours and how to read a contact sheet."
)

MCP_SERVER_NAME = "manim-studio"
AGENTS_BEGIN = "<!-- manim-studio:begin -->"
AGENTS_END = "<!-- manim-studio:end -->"

SKILL_BODY = """\
# Authoring ManimGL scenes with Manim Studio

Manim Studio is a ManimGL editor. It has no AI of its own: you drive it through the `manim-studio`
MCP server (preferred) or the CLI. The editor may be open or closed; both see the same files.

## The loop

| Step | MCP tool | CLI equivalent |
|------|----------|----------------|
| Start from a template | `new_scene` | `manim-studio new FILE --template circle_to_square` |
| Write or edit source | `write_scene` | edit the file |
| Validate (no GL) | `check` | `manim-studio check FILE --json` |
| Verify timing | `trace` | `manim-studio trace FILE --json` |
| See the whole animation | `contact_sheet` | `manim-studio contact-sheet FILE --json` |
| Inspect one moment | `frame` | `manim-studio frame FILE --at 1.5 -o frame.png --json` |
| Look up an API | `api_index`, `describe_mobject` | `manim-studio api-index --json` |
| Render | `export` | `manim-studio export FILE -o out.mp4 --quality low` |

Run `check` after every write: it reports syntax errors and the line they are on. Then `trace`
shows whether the scene runs and what each block lasts. Then `contact_sheet` shows what it looks
like. Only then render.

### Reading a contact sheet

Each tile is labelled `t=<seconds> #<block index> <block name>`, for example `t=1.00s #4 Transform`.
The block index matches `trace`, so you can jump from a tile to its code. Look for:

- an empty tile where something should be visible (the object was never added, or it is off frame);
- a shape that never moves between tiles (the animation did not run, or it has `run_time` near 0);
- two objects overlapping where they should not;
- an object that leaves the frame and never returns.

Call `frame` at the time of any tile that looks wrong, for full resolution.

## ManimGL, not Manim Community

- Creation is `ShowCreation`, not `Create`. Imports: `from manimlib import *`.
- Scene base classes: `Scene`, `InteractiveScene`, `ThreeDScene`.
- There is no `Star`. Use `RegularPolygon(n=5)`.
- The camera is `self.frame`, for example `self.frame.animate.reorient(theta, phi)`.
- Common animations: `ShowCreation`, `Uncreate`, `Write`, `FadeIn`, `FadeOut`, `Transform`,
  `ReplacementTransform`, `Rotate`, `Indicate`, `FadeTransformPieces`.
- `mobject.animate.<method>(...)` builds an animation; `run_time`, `rate_func` and `lag_ratio`
  all work there.
- Check any name you are unsure of with `api_index` or `describe_mobject`.

## Colours: always hex strings

Write colours as hex strings such as `"#58C4DD"`. Never use manim colour constant names, even
though they exist. `describe_mobject` with `include_colours=true` lists the constants with their
hex values if you need the palette.

Sharp edge: `Circle`, `Dot` and `Ellipse` pass a default stroke colour explicitly, so `color=` only
sets the fill and the visible stroke keeps the default. Set the stroke explicitly:

```python
circle = Circle(stroke_color="#58C4DD")   # correct
dot = Dot(fill_color="#FC6255")           # Dot is filled, so use fill_color
circle = Circle(color="#58C4DD")          # wrong: the stroke keeps its default colour
```

`check` warns about this pattern on the line where it occurs.

## Structure that traces well

```python
from manimlib import *


class Intro(Scene):
    def construct(self) -> None:
        circle = Circle(stroke_color="#58C4DD")
        square = Square(stroke_color="#FFFF00")
        self.play(ShowCreation(circle), run_time=1.5)
        self.wait(0.5)
        self.play(Transform(circle, square), run_time=1.5)
        self.wait(1.0)
```

- Give every `play` an explicit `run_time=`. The timeline uses the requested times.
- Loops and branches are fine. The trace is taken at run time, so each loop iteration is its own
  block, and `trace` reports them that way.
- `save_state()`, `undo()` and `redo()` work, and `trace` counts them.
- Avoid absolute paths. ManimGL resolves assets from its configured folders.

## Studio specifics

- Timeline edits saved in `<scene>.studio.json` are honoured by `frame`, `contact_sheet` and
  `export`. Pass `use_sidecar=false` (MCP) or `--no-sidecar` (CLI) to ignore them.
- Do not hand-edit `<scene>.studio.json`. The editor writes it.
- With the editor open, saving the `.py` reloads the preview automatically.
- `self.embed()` does nothing in the preview. Do not rely on it.
- `export` defaults to `high` in the CLI and to `low` over MCP. Use `low` to check a result quickly.

## Debugging

1. `check`: syntax errors and missing scene classes, with line numbers.
2. `trace`: runtime errors, with the scene line in the traceback.
3. Common causes: a Manim Community name such as `Create`; an undefined variable at construct time;
   a `Tex` call when LaTeX is not installed (run `manim-studio latex status`).
4. If the output is wrong but nothing fails, look at the contact sheet, then one `frame`.
"""


def agents_block() -> str:
    """The managed section for AGENTS.md, between two markers so it can be replaced in place."""
    return "\n".join(
        [
            AGENTS_BEGIN,
            "## Manim Studio (ManimGL scenes)",
            "",
            "This project uses Manim Studio to write and render ManimGL scenes. It has",
            "no AI of its own; an agent drives it through tools:",
            "",
            f"- MCP server `{MCP_SERVER_NAME}`: start it with `manim-studio mcp --root .`.",
            "- Or the CLI: `manim-studio check`, `trace`, `frame`, `contact-sheet`, `export`.",
            "",
            f"Read `.claude/skills/{SKILL_NAME}/SKILL.md` (or the equivalent for your tool) before",
            "writing a scene. The essentials:",
            "",
            "- Use ManimGL names (`ShowCreation`, `from manimlib import *`), not Manim Community.",
            '- Write colours as hex strings such as "#58C4DD".',
            "- Run `check`, then `trace`, then `contact_sheet` before exporting.",
            AGENTS_END,
        ]
    )
