"""Feature showcase scene — the canonical editor test scene.

This scene deliberately exercises **every** editor and scene-API feature so that
``tests/test_scene_runner.py`` and the preview-host integration tests can assert on
a rich, deterministic trace:

- static mobjects: Circle, Square, Star, Dot, VGroup, Text
- ``add`` / ``remove`` / ``save_state`` / ``undo`` / ``redo``
- ``play`` with one, two and three animations; ``wait``
- runtime ``for`` loop and ``if`` branch (proves runtime tracing of control flow)
- ``mobject.animate`` builder syntax
- updaters and ``always_redraw``
- camera frame movement
- checkpoint comment (``# checkpoint``) for ``checkpoint_paste`` workflows
- optional LaTeX (skipped silently when no TeX distribution is installed)

Total duration is intentionally under two seconds so the whole suite stays fast.
"""

from manimlib import (
    BLUE,
    DEG,
    DOWN,
    GREEN,
    ORANGE,
    PURPLE,
    RED,
    RIGHT,
    UP,
    YELLOW,
    Circle,
    Dot,
    FadeIn,
    FadeOut,
    Indicate,
    InteractiveScene,
    RegularPolygon,
    Rotate,
    ShowCreation,
    Square,
    Text,
    Transform,
    VGroup,
    always_redraw,
)


class FeatureShowcase(InteractiveScene):
    """Every feature in one deterministic scene (see module docstring)."""

    def construct(self) -> None:
        # --- static mobjects, text, and add() --------------------------------
        title = Text("Manim Studio", font_size=64).to_edge(UP)
        circle = Circle(radius=1.1, stroke_color=BLUE)
        square = Square(side_length=1.8, color=YELLOW)
        self.add(title, circle)

        # --- creation animations ---------------------------------------------
        self.play(ShowCreation(circle), run_time=0.3)
        self.wait(0.2)

        # --- runtime loop (one timeline block per iteration) ------------------
        for _ in range(2):
            self.play(Rotate(circle, 90 * DEG, run_time=0.15))

        # --- runtime conditional ----------------------------------------------
        if abs(circle.get_center()[0]) < 1e-6:
            self.play(Transform(circle, square), run_time=0.25)

        # --- multiple animations in a single play ------------------------------
        self.play(FadeIn(square, shift=UP * 0.5), Indicate(title), run_time=0.25)

        # --- mobject.animate builder syntax ------------------------------------
        self.play(circle.animate.shift(RIGHT * 0.5 + DOWN * 0.5), run_time=0.2)

        # --- remove + save_state/undo/redo -------------------------------------
        self.remove(square)
        self.save_state()
        star = RegularPolygon(n=5, color=PURPLE, radius=0.8)
        self.play(ShowCreation(star), run_time=0.25)
        self.undo()
        self.redo()

        # --- updaters and always_redraw ----------------------------------------
        dot = Dot(fill_color=RED, radius=0.08)
        dot.add_updater(lambda mob, dt: mob.rotate(120 * DEG * dt))
        self.add(dot)
        clock = always_redraw(
            lambda: Dot(fill_color=GREEN, radius=0.04).move_to(RIGHT * 2 + UP * 2)
        )
        self.add(clock)
        self.wait(0.15)
        self.remove(clock)

        # --- groups -------------------------------------------------------------
        group = VGroup(circle, star).arrange(RIGHT, buff=0.6)
        self.play(group.animate.to_edge(DOWN), run_time=0.2)

        # --- camera movement ------------------------------------------------------
        self.play(self.frame.animate.reorient(15, 20), run_time=0.2)

        # --- optional LaTeX --------------------------------------------------------
        try:
            from manimlib import Tex

            formula = Tex(r"e^{i\pi} + 1 = 0", font_size=48)
            self.play(FadeIn(formula), run_time=0.2)
            self.play(FadeOut(formula), run_time=0.15)
        except Exception:  # pragma: no cover - depends on a TeX installation
            pass

        # checkpoint: outro
        self.play(FadeOut(title), run_time=0.2)
        self.wait(0.1)
        # keep at least one warm-colored mobject referenced so the trace is stable
        orange_dot = Dot(color=ORANGE).move_to(3 * RIGHT)
        self.add(orange_dot)
