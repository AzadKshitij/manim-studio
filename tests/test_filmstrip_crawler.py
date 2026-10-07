"""FilmstripCrawler visit order: playhead-first, with no GL context needed."""

from __future__ import annotations

from typing import Any

import pytest

from manim_studio import scrub
from manim_studio.filmstrip import Filmstrip
from manim_studio.timeline_model import TimelineBlock

BLOCKS = tuple(TimelineBlock(i, "Wait", float(i), 1.0) for i in range(4))


class _StubCamera:
    def __init__(self) -> None:
        self.default_pixel_shape = (2, 2)

    def capture(self, *_groups: Any) -> None:
        pass


class _StubScene:
    def __init__(self) -> None:
        self.camera = _StubCamera()
        self.render_groups: list[Any] = []
        self.time = 0.0


class _StubEngine:
    """Just enough of ScrubEngine for FilmstripCrawler, without any GL."""

    def __init__(self, blocks: tuple[TimelineBlock, ...]) -> None:
        self._blocks = blocks
        self.scene = _StubScene()
        self.paused_block: TimelineBlock | None = None
        self.paused_animations: list[Any] | None = None
        self.poses: list[float] = []

    @property
    def total_duration(self) -> float:
        return max((block.end for block in self._blocks), default=0.0)

    def pose(self, time: float) -> TimelineBlock | None:
        block = next((b for b in self._blocks if b.start == time), None)
        self.poses.append(time)
        self.paused_block = block
        self.paused_animations = None
        self.scene.time = time
        return block

    def advance_wait(self, dt: float) -> None:
        self.scene.time += dt


@pytest.fixture(autouse=True)
def _fake_readback(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(scrub, "read_upright_rgba", lambda camera: (bytes(16), 2, 2))


def _crawler(*, start_time: float = 0.0, frame_budget: int = 1000):
    engine = _StubEngine(BLOCKS)
    crawler = scrub.FilmstripCrawler(
        engine, Filmstrip(), 10, start_time=start_time, frame_budget=frame_budget
    )
    return engine, crawler


def _run(crawler: scrub.FilmstripCrawler) -> None:
    while not crawler.done:
        crawler.step()


class TestPlayheadFirstOrder:
    def test_start_time_orders_blocks_nearest_first(self) -> None:
        _, crawler = _crawler(start_time=3.5)
        assert crawler._order == [3, 2, 1, 0]

    def test_center_start_expands_outward(self) -> None:
        _, crawler = _crawler(start_time=1.5)
        assert crawler._order == [1, 2, 0, 3]

    def test_zero_start_keeps_sequential_order(self) -> None:
        _, crawler = _crawler(start_time=0.0)
        assert crawler._order == [0, 1, 2, 3]

    def test_step_visits_blocks_in_playhead_order(self) -> None:
        engine, crawler = _crawler(start_time=3.5)
        _run(crawler)
        assert engine.poses == [3.0, 2.0, 1.0, 0.0]
        assert crawler.frames > 0
        assert crawler.done and not crawler.capped

    def test_prioritize_reaims_the_remaining_queue(self) -> None:
        engine, crawler = _crawler(start_time=0.0, frame_budget=5)
        crawler.step()  # begin block 0
        crawler.prioritize(3.5)
        assert crawler._order[0] == 0  # the in-flight block keeps its slot
        assert crawler._order[1:] == [3, 2, 1]
        _run(crawler)
        assert engine.poses == [0.0, 3.0, 2.0, 1.0]

    def test_progress_is_monotonic_and_reaches_one(self) -> None:
        _, crawler = _crawler(start_time=3.5, frame_budget=7)
        seen: list[float] = []
        while not crawler.done:
            crawler.step()
            seen.append(crawler.progress)
        assert seen == sorted(seen)
        assert crawler.progress == pytest.approx(1.0)

    def test_seconds_to_playhead_is_recorded(self) -> None:
        _, crawler = _crawler(start_time=3.5)  # playhead in the last block
        assert crawler.seconds_to_playhead is None
        _run(crawler)
        assert crawler.seconds_to_playhead is not None
        assert crawler.seconds_to_playhead >= 0.0
