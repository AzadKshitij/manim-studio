"""Unit tests for timing-aware replay/playback scaling (no GL needed)."""

from __future__ import annotations

from typing import Any

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

    def play(self, *animations: Any, **kwargs: Any) -> None:
        self.calls.append(("play", [animation.run_time for animation in animations]))
        if animations:
            self.num_plays += 1

    def wait(self, duration: float | None = None, **kwargs: Any) -> None:
        self.calls.append(("wait", self.default_wait_time if duration is None else duration))
        self.num_plays += 1


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
    def test_speed_double_halves_run_time(self) -> None:
        scene = _FakeScene()
        scrub.TimingScaler(scene, {}, speed=2.0)
        scene.play(_FakeAnimation(1.0))
        assert scene.calls[-1] == ("play", [0.5])

    def test_half_speed_doubles_run_time(self) -> None:
        scene = _FakeScene()
        scrub.TimingScaler(scene, {}, speed=0.5)
        scene.play(_FakeAnimation(1.0))
        assert scene.calls[-1] == ("play", [2.0])

    def test_speed_composes_with_a_block_factor(self) -> None:
        scene = _FakeScene()
        scrub.TimingScaler(scene, {0: 2.0}, speed=2.0)  # 2.0 / 2.0 -> unchanged
        scene.play(_FakeAnimation(1.0))
        assert scene.calls[-1] == ("play", [1.0])

    def test_speed_scales_waits(self) -> None:
        scene = _FakeScene()
        scrub.TimingScaler(scene, {}, speed=0.5)
        scene.wait(1.0)
        assert scene.calls[-1] == ("wait", 2.0)

    def test_non_positive_speed_is_treated_as_normal(self) -> None:
        scene = _FakeScene()
        scrub.TimingScaler(scene, {}, speed=0.0)
        scene.play(_FakeAnimation(1.0))
        assert scene.calls[-1] == ("play", [1.0])
