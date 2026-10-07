"""Unit tests for timing-aware replay/playback scaling (no GL needed)."""

from __future__ import annotations

import types
from typing import Any

import numpy as np
import pytest

from manim_studio import scrub
from manim_studio.timeline_model import TimelineBlock


class _FakeAnimation:
    def __init__(self, run_time: float) -> None:
        self.run_time = run_time
        self.alphas: list[float] = []

    def interpolate(self, alpha: float) -> None:
        self.alphas.append(alpha)


class _FakeScene:
    """Mirrors the tracer's contract: play-with-args and wait advance ``num_plays``."""

    def __init__(self) -> None:
        self.num_plays = 0
        self.default_wait_time = 1.0
        self.calls: list[tuple[str, Any]] = []
        self.skip_animations = False
        self.show_animation_progress = False
        self.camera = types.SimpleNamespace(fps=30)
        # Real-time pacing fields manimgl touches in update_frame.
        self.time = 0.0
        self.window: Any = None
        self.virtual_animation_start_time = 0.0
        self.real_animation_start_time = 0.0

    def update_frame(self, dt: float = 0, force_draw: bool = False) -> None:
        self.time += dt

    def play(self, *animations: Any, run_time: float | None = None, **kwargs: Any) -> None:
        # manimgl's Scene.play applies an explicit run_time to every animation,
        # overwriting any prior scaling; the fake mirrors that so scaling bugs show.
        if run_time is not None:
            for animation in animations:
                animation.run_time = run_time
        self.calls.append(("play", [animation.run_time for animation in animations]))
        if animations:
            self.num_plays += 1

    def wait(self, duration: float | None = None, **kwargs: Any) -> None:
        self.calls.append(("wait", self.default_wait_time if duration is None else duration))
        self.num_plays += 1

    def get_time_progression(
        self,
        run_time: float,
        n_iterations: int | None = None,
        desc: str = "",
        override_skip_animations: bool = False,
    ) -> Any:
        step = 1.0 / self.camera.fps
        return np.arange(0.0, float(run_time), step) + step


@pytest.fixture(autouse=True)
def _identity_prepare(monkeypatch: pytest.MonkeyPatch) -> None:
    """Skip manimgl entirely: the fakes are already valid animation objects."""
    monkeypatch.setattr(scrub, "_prepare_animation", lambda animation: animation)


class TestScrubEngineScaling:
    def test_played_animation_is_scaled_by_the_block_factor(self) -> None:
        scene = _FakeScene()
        blocks = (
            TimelineBlock(0, "ShowCreation", 0.0, 1.0),
            TimelineBlock(1, "Wait", 1.0, 1.0),
        )
        engine = scrub.ScrubEngine(scene, blocks, factors={0: 2.0})
        engine._target = 5.0  # past the timeline: replay every block instead of pausing
        scene.play(_FakeAnimation(0.5))
        assert scene.calls[-1] == ("play", [1.0])  # 0.5 * 2.0

    def test_wait_duration_is_scaled_by_the_block_factor(self) -> None:
        scene = _FakeScene()
        engine = scrub.ScrubEngine(scene, (TimelineBlock(0, "Wait", 0.0, 1.0),), factors={0: 3.0})
        engine._target = 5.0
        scene.wait(1.0)
        assert scene.calls[-1] == ("wait", 3.0)

    def test_unscaled_blocks_are_left_alone(self) -> None:
        scene = _FakeScene()
        engine = scrub.ScrubEngine(scene, (TimelineBlock(0, "ShowCreation", 0.0, 1.0),), factors={})
        engine._target = 5.0
        scene.play(_FakeAnimation(0.25))
        assert scene.calls[-1] == ("play", [0.25])

    def test_replay_scales_an_explicit_run_time(self) -> None:
        scene = _FakeScene()
        blocks = (TimelineBlock(0, "ShowCreation", 0.0, 1.0),)
        engine = scrub.ScrubEngine(scene, blocks, factors={0: 2.0})
        engine._target = 5.0  # past the timeline: replay the block instead of pausing
        scene.play(_FakeAnimation(0.5), run_time=0.5)
        assert scene.calls[-1] == ("play", [1.0])  # 0.5 * 2.0 via the kwarg


class TestTimingScaler:
    def test_play_uses_the_current_block_factor(self) -> None:
        scene = _FakeScene()
        scrub.TimingScaler(scene, {0: 2.0, 2: 0.5})
        scene.play(_FakeAnimation(0.6))  # block 0 -> x2
        assert scene.calls[-1] == ("play", [1.2])
        scene.play(_FakeAnimation(0.4))  # block 1 -> unchanged
        assert scene.calls[-1] == ("play", [0.4])
        scene.play(_FakeAnimation(0.4))  # block 2 -> x0.5
        assert scene.calls[-1] == ("play", [0.2])

    def test_wait_scales_the_current_block_factor(self) -> None:
        scene = _FakeScene()
        scrub.TimingScaler(scene, {0: 4.0})
        scene.wait(0.5)
        assert scene.calls[-1] == ("wait", 2.0)

    def test_bare_wait_scales_the_default_duration(self) -> None:
        scene = _FakeScene()
        scrub.TimingScaler(scene, {0: 2.0})
        scene.wait()
        assert scene.calls[-1] == ("wait", 2.0)

    def test_empty_play_does_not_consume_a_block_index(self) -> None:
        scene = _FakeScene()
        scrub.TimingScaler(scene, {0: 2.0})
        scene.play()  # not a block; still block 0 afterwards
        assert scene.num_plays == 0
        scene.play(_FakeAnimation(1.0))
        assert scene.calls[-1] == ("play", [2.0])


class TestTimingScalerSpeed:
    def test_speed_leaves_run_time_alone(self) -> None:
        # run_time must NOT shrink, or scene.time would stop short of the timeline.
        scene = _FakeScene()
        scrub.TimingScaler(scene, {}, speed=2.0)
        scene.play(_FakeAnimation(1.0))
        assert scene.calls[-1] == ("play", [1.0])

    def test_speed_scales_the_frame_step_to_run_time(self) -> None:
        scene = _FakeScene()
        scrub.TimingScaler(scene, {}, speed=2.0)
        times = scene.get_time_progression(1.0)
        assert times[1] - times[0] == pytest.approx(2.0 / 30)
        assert times[-1] == pytest.approx(1.0, abs=2.0 / 30)  # covers the whole block

    def test_half_speed_uses_a_finer_step(self) -> None:
        scene = _FakeScene()
        scrub.TimingScaler(scene, {}, speed=0.5)
        times = scene.get_time_progression(1.0)
        assert times[1] - times[0] == pytest.approx(0.5 / 30)
        assert times[-1] == pytest.approx(1.0, abs=0.5 / 30)

    def test_speed_composes_with_a_block_factor(self) -> None:
        scene = _FakeScene()
        scrub.TimingScaler(scene, {0: 2.0}, speed=2.0)  # retime x2 only; speed is separate
        scene.play(_FakeAnimation(1.0))
        assert scene.calls[-1] == ("play", [2.0])
        times = scene.get_time_progression(2.0)  # the retimed duration
        assert times[1] - times[0] == pytest.approx(2.0 / 30)

    def test_speed_scales_waits_via_the_time_step(self) -> None:
        scene = _FakeScene()
        scrub.TimingScaler(scene, {}, speed=2.0)
        scene.wait(1.0)
        assert scene.calls[-1] == ("wait", 1.0)  # duration unchanged
        times = scene.get_time_progression(1.0)
        assert times[1] - times[0] == pytest.approx(2.0 / 30)

    def test_speed_one_uses_the_scene_default(self) -> None:
        scene = _FakeScene()
        scrub.TimingScaler(scene, {}, speed=1.0)
        times = scene.get_time_progression(1.0)
        assert times[1] - times[0] == pytest.approx(1.0 / 30)

    def test_non_positive_speed_is_treated_as_normal(self) -> None:
        scene = _FakeScene()
        scrub.TimingScaler(scene, {}, speed=0.0)
        scene.play(_FakeAnimation(1.0))
        assert scene.calls[-1] == ("play", [1.0])
        times = scene.get_time_progression(1.0)
        assert times[1] - times[0] == pytest.approx(1.0 / 30)

    def test_update_frame_shortens_the_pacing_clock(self) -> None:
        scene = _FakeScene()
        scene.window = object()  # a window makes manimgl pace to real time
        scene.virtual_animation_start_time = 0.0
        scene.real_animation_start_time = 100.0
        scene.time = 0.5
        scaler = scrub.TimingScaler(scene, {}, speed=2.0)
        scaler._update_frame(0.1)
        # target = 0.6; start shifted by target*(1 - 1/2) = 0.3.
        assert scene.real_animation_start_time == pytest.approx(99.7)
        scene.time = 0.6
        scaler._update_frame(0.1)  # same block reuses the captured start
        assert scene.real_animation_start_time == pytest.approx(100.0 - 0.7 * 0.5)

    def test_update_frame_is_a_passthrough_without_a_window(self) -> None:
        scene = _FakeScene()
        scaler = scrub.TimingScaler(scene, {}, speed=2.0)
        scaler._update_frame(0.1)
        assert scene.real_animation_start_time == 0.0  # untouched
