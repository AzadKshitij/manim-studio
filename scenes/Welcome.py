"""Welcome to Manim Studio — the starter scene.

Open it with ``manim-studio gui scenes/Welcome.py``, then change a number or a colour,
save, and watch the preview reload. Drag the timeline to scrub. No LaTeX required:
everything here uses ``Text`` and plain shapes.

Tour: title -> orbiting dots -> circle morphs into a square -> a travelling sine
wave -> a Lissajous curve drawn by an updater -> a colour-wave grid -> sign-off.
"""

from itertools import cycle

import numpy as np
from manimlib import (
    BLUE,
    DOWN,
    GREEN,
    ORANGE,
    PINK,
    PURPLE,
    RED,
    TAU,
    TEAL,
    UP,
    YELLOW,
    DEG,
    RIGHT,
    LEFT,
    TOP,
    BOTTOM,
    Circle,
    Dot,
    FadeIn,
    FadeOut,
    InteractiveScene,
    LaggedStart,
    ParametricCurve,
    Rotate,
    ShowCreation,
    Square,
    Text,
    Transform,
    ValueTracker,
    VGroup,
    VMobject,
    Write,
    always_redraw,
    interpolate_color,
    linear,
)

PALETTE = [BLUE, TEAL, GREEN, YELLOW, ORANGE, RED, PINK, PURPLE]




class Welcome(InteractiveScene):

    # --- manim-studio:scene-settings (managed; edit via Scene > Scene Settings) ---
    def __init__(self, **kwargs) -> None:
        # Scene settings from Manim Studio (managed block; change them in the Scene menu).
        kwargs["camera_config"] = {
            "resolution": (854, 480),
            "fps": 30,
            "background_color": "#0B0B0B",
            "background_opacity": 1.0,
            "frame_config": {"frame_shape": (14.233333, 8.0)},
            **kwargs.get("camera_config", {}),
        }
        super().__init__(**kwargs)

        # manimgl scales x by the 16:9 frame width; use this resolution's aspect ratio.
        from manimlib.constants import FRAME_HEIGHT
        camera = self.camera
        draw_uniforms = camera.refresh_uniforms

        def refresh_uniforms() -> None:
            draw_uniforms()
            factors = list(camera.uniforms["frame_rescale_factors"])
            factors[0] = 2.0 / (FRAME_HEIGHT * camera.get_aspect_ratio())
            camera.uniforms["frame_rescale_factors"] = tuple(factors)

        camera.refresh_uniforms = refresh_uniforms
    # --- end manim-studio:scene-settings ---

    def construct(self) -> None:
        # --- 1. Title ---------------------------------------------------------
        title = Text("Welcome to Manim Studio", font_size=64)
        title.set_color_by_gradient(BLUE, TEAL, GREEN)
        self.play(Write(title), run_time=2.4)
        self.wait(0.5)
        title1 = Text("Welcome to Manim Studio", font_size=44)
        title2 =  Text("Welcome to Manim Studio", font_size=44)
        
        
        
        group = VGroup(title1, title2).arrange(RIGHT, buff=0.5)
        self.play(group.animate.to_edge(DOWN))
        

        # ------------------------- 2. A ring of dots spins up around the title ---------------------
        ring = VGroup(
            *(
                Dot(
                    radius=0.09,
                    stroke_color=c,
                    fill_color=c,
                ).move_to(3.2 * np.array([np.cos(a), np.sin(a), 0]))
                for a, c in zip(np.linspace(0, TAU, 24, endpoint=False), cycle(PALETTE))
            )
        )
        self.play(
            title.animate.scale(0.6).to_edge(UP),
            LaggedStart(*(FadeIn(d, scale=0.1) for d in ring), lag_ratio=0.05),
            run_time=2.0,
        )
        self.play(Rotate(ring, TAU / 2, run_time=2), run_time=1.033)

        # --- 3. Circle -> square morph, ring collapses into the circle -------
        circle = Circle(radius=2, stroke_color=BLUE, stroke_width=6)
        square = Square(side_length=3.5, stroke_color=YELLOW, stroke_width=6)
        self.play(ShowCreation(circle), FadeOut(ring), run_time=1.5)
        self.play(Transform(circle, square), run_time=1.001)
        self.play(circle.animate.rotate(TAU / 8).set_stroke(PINK), run_time=1.0)
        for _ in range(3):
            self.play(Rotate(circle, 90 * DEG, run_time=1.0))
        self.play(FadeOut(circle))

        # --- 4. Travelling sine wave (redrawn from a ValueTracker) ------------
        phase = ValueTracker(0)

        def make_wave() -> VMobject:
            wave = ParametricCurve(
                lambda t: np.array([t, np.sin(1.5 * t + phase.get_value()), 0]),
                t_range=(-6, 6, 0.05),
                stroke_width=6,
            )
            return wave.set_color_by_gradient(BLUE, TEAL, GREEN, YELLOW)

        wave = always_redraw(make_wave)
        self.play(ShowCreation(wave), run_time=1.5)
        self.play(phase.animate.set_value(TAU), run_time=2.033, rate_func=linear)
        self.play(FadeOut(wave), run_time=1.833)

        # --- 5. Lissajous curve drawn by a moving dot ------------------------
        pen = ValueTracker(0)

        def lissajous(t: float) -> np.ndarray:
            return np.array([3.5 * np.sin(3 * t + np.pi / 2), 2.2 * np.sin(2 * t), 0])

        trail = always_redraw(
            lambda: ParametricCurve(
                lissajous, t_range=(0, max(pen.get_value(), 0.01), 0.03), stroke_width=4
            ).set_color_by_gradient(PINK, PURPLE, BLUE)
        )
        tracer = always_redraw(
            lambda: Dot(radius=0.12, stroke_color=YELLOW, fill_color=YELLOW).move_to(
                lissajous(pen.get_value())
            )
        )
        self.add(trail, tracer)
        self.play(pen.animate.set_value(TAU), run_time=1.734, rate_func=linear)
        self.play(FadeOut(tracer), run_time=0.267)
        self.play(FadeOut(trail), run_time=0.8)

        # --- 6. A grid of squares with a colour wave -------------------------
        grid = VGroup(*(Square(side_length=0.55) for _ in range(12 * 6)))
        grid.arrange_in_grid(6, 12, buff=0.12)
        grid.set_stroke(width=0).set_fill(BLUE, 1).shift(0.3 * DOWN)
        self.play(LaggedStart(*(FadeIn(s, scale=0.3) for s in grid), lag_ratio=0.01))
        for shift in range(3):
            self.play(
                *(
                    s.animate.set_fill(
                        interpolate_color(
                            BLUE, PINK, 0.5 + 0.5 * np.sin(0.6 * (i % 12) + 0.6 * (i // 12) + shift)
                        ),
                        1,
                    ).rotate(TAU / 4)
                    for i, s in enumerate(grid)
                ),
                run_time=1,
            )
        self.play(FadeOut(grid), run_time=1.0)

        # --- 7. Sign-off -------------------------------------------------------
        hint = Text("Edit this file, save, and the preview reloads.", font_size=36)
        hint.set_color(TEAL)
        hint2 = Text("Drag the timeline to scrub. Make it yours!", font_size=30)
        hint2.next_to(hint, DOWN, buff=0.4)
        self.play(Write(hint), run_time=1.5)
        self.play(FadeIn(hint2, shift=UP * 0.3))
        self.wait(0.956)



class Intro(InteractiveScene):
    def construct(self) -> None:
        # --- 1. Title ---------------------------------------------------------
        title = Text("Welcome to Manim Studio", font_size=64)
        title.set_color_by_gradient(BLUE, TEAL, GREEN)
        self.play(Write(title), run_time=2.4)
        self.wait(0.5)