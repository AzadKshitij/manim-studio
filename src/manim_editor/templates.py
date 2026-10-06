"""Scene and snippet templates.

Two kinds:

* **scene templates** render complete, runnable files (``{scene_name}`` placeholder);
* **snippet templates** are inserted at the cursor inside an existing ``construct``
  and are written with the conventional 4-space indentation already applied.

Everything here is plain data + string rendering so it is cheap to unit test, and
the test suite verifies every template compiles and every scene template traces.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence

__all__ = [
    "SCENE_TEMPLATES",
    "SNIPPET_TEMPLATES",
    "Template",
    "TemplateError",
    "find_template",
    "render_scene_template",
    "snippet_titles",
]

_HEADER = "from manimlib import *\n"


class TemplateError(ValueError):
    """Raised for unknown templates or invalid scene names."""


@dataclass(frozen=True, slots=True)
class Template:
    """One scene file template or one insertable snippet."""

    key: str
    title: str
    description: str
    code: str

    @property
    def is_scene(self) -> bool:
        return "{scene_name}" in self.code

    def render(self, scene_name: str = "MyScene") -> str:
        """Render the template, validating the scene name when used."""
        if self.is_scene:
            if not scene_name.isidentifier():
                raise TemplateError(f"invalid scene name {scene_name!r}")
            return self.code.replace("{scene_name}", scene_name)
        return self.code


SCENE_TEMPLATES: tuple[Template, ...] = (
    Template(
        key="blank",
        title="Blank scene",
        description="Minimal scene that writes a title and waits.",
        code=_HEADER
        + """

class {scene_name}(Scene):
    def construct(self) -> None:
        title = Text("Hello, Manim!")
        self.play(Write(title))
        self.wait(1.0)
""",
    ),
    Template(
        key="circle_to_square",
        title="Circle → Square",
        description="Create a circle, then transform it into a square.",
        code=_HEADER
        + """

class {scene_name}(Scene):
    def construct(self) -> None:
        # manimgl note: Circle strokes default to red and `color=` only sets the
        # invisible fill, so the visible colour needs stroke_color.
        circle = Circle(stroke_color=BLUE)
        square = Square(color=YELLOW)

        self.play(ShowCreation(circle), run_time=1.5)
        self.wait(0.5)
        self.play(Transform(circle, square), run_time=1.5)
        self.wait(1.0)
""",
    ),
    Template(
        key="text_intro",
        title="Text intro",
        description="Fade a title in, underline it with a line, fade out.",
        code=_HEADER
        + """

class {scene_name}(Scene):
    def construct(self) -> None:
        title = Text("Manim Editor", font_size=72)
        underline = Line(LEFT * 2, RIGHT * 2, color=BLUE)
        underline.next_to(title, DOWN)

        self.play(FadeIn(title, shift=UP * 0.5), ShowCreation(underline))
        self.wait(1.0)
        self.play(FadeOut(title), FadeOut(underline))
""",
    ),
    Template(
        key="three_d",
        title="3D rotation",
        description="A sphere on axes with a camera orbit.",
        code=_HEADER
        + """

class {scene_name}(ThreeDScene):
    def construct(self) -> None:
        axes = ThreeDAxes()
        sphere = Sphere(radius=1.0, color=BLUE)

        self.add(axes)
        self.play(ShowCreation(sphere), run_time=1.5)
        self.play(self.frame.animate.reorient(30, 60), run_time=2.0)
        self.wait(1.0)
""",
    ),
)

SNIPPET_TEMPLATES: tuple[Template, ...] = (
    Template(
        key="circle",
        title="Show a circle",
        description="Add a circle to the scene.",
        code="circle = Circle(stroke_color=BLUE)\nself.add(circle)\n",
    ),
    Template(
        key="square",
        title="Create a square",
        description="Animate a square into existence.",
        code="square = Square(color=YELLOW)\nself.play(ShowCreation(square))\n",
    ),
    Template(
        key="text",
        title="Write text",
        description="Create a Text mobject and write it.",
        code='label = Text("Hello")\nself.play(Write(label))\n',
    ),
    Template(
        key="transform",
        title="Transform",
        description="Morph one mobject into another.",
        code="self.play(Transform(first, second))\n",
    ),
    Template(
        key="fade_in",
        title="Fade in",
        description="Fade a mobject in with a small shift.",
        code="self.play(FadeIn(mob, shift=UP * 0.5))\n",
    ),
    Template(
        key="fade_out",
        title="Fade out",
        description="Fade a mobject out.",
        code="self.play(FadeOut(mob))\n",
    ),
    Template(
        key="wait",
        title="Wait",
        description="Hold the current frame for one second.",
        code="self.wait(1.0)\n",
    ),
    Template(
        key="group",
        title="Group and arrange",
        description="Group mobjects, arrange them, then move the group.",
        code=(
            "group = VGroup(mob1, mob2).arrange(RIGHT, buff=0.5)\n"
            "self.play(group.animate.to_edge(DOWN))\n"
        ),
    ),
    Template(
        key="updater",
        title="Updater",
        description="Rotate a mobject continuously with an updater.",
        code="dot.add_updater(lambda mob, dt: mob.rotate(120 * DEG * dt))\nself.add(dot)\n",
    ),
    Template(
        key="always_redraw",
        title="always_redraw",
        description="Redraw a mobject every frame from other state.",
        code=(
            "tracker = always_redraw(lambda: Dot().move_to(circle.get_center()))\n"
            "self.add(tracker)\n"
        ),
    ),
    Template(
        key="camera_move",
        title="Camera move",
        description="Orbit the camera frame.",
        code="self.play(self.frame.animate.reorient(30, 45), run_time=1.5)\n",
    ),
    Template(
        key="loop",
        title="Loop animations",
        description="Repeat an animation three times (traced per iteration).",
        code="for _ in range(3):\n    self.play(Rotate(circle, 90 * DEG, run_time=0.5))\n",
    ),
    Template(
        key="condition",
        title="Conditional animation",
        description="Branch on runtime state.",
        code="if condition:\n    self.play(FadeIn(mob))\nelse:\n    self.wait(0.5)\n",
    ),
    Template(
        key="save_state",
        title="save_state / undo",
        description="Snapshot the scene and restore it.",
        code="self.save_state()\n# ... make changes ...\nself.undo()\n",
    ),
)


def find_template(key: str, *, kind: str | None = None) -> Template:
    """Look up a template by key across scenes and snippets."""
    for template in (*SCENE_TEMPLATES, *SNIPPET_TEMPLATES):
        if template.key != key:
            continue
        if kind == "scene" and not template.is_scene:
            continue
        if kind == "snippet" and template.is_scene:
            continue
        return template
    raise TemplateError(f"unknown template {key!r}")


def render_scene_template(key: str, scene_name: str) -> str:
    """Render a complete scene file from a scene template."""
    template = find_template(key, kind="scene")
    return template.render(scene_name)


def snippet_titles() -> Sequence[tuple[str, str]]:
    """``(key, title)`` pairs for building the Insert menu."""
    return [(template.key, template.title) for template in SNIPPET_TEMPLATES]
