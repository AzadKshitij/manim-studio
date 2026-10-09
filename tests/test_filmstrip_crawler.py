"""FilmstripCrawler: head block first, then one streamed pass; no GL context needed."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from typing import Any

import pytest

from manim_studio import scrub
from manim_studio.filmstrip import Filmstrip
from manim_studio.timeline_model import TimelineBlock

BLOCKS = tuple(TimelineBlock(i, "Wait", float(i), 1.0) for i in range(4))
FPS = 10


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
        self.streams = 0
        self.streamed: list[tuple[int, int]] = []
        self.closed = False

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

    def stream_frames(
        self, fps: int, wanted: Callable[[TimelineBlock], bool]
    ) -> Iterator[tuple[TimelineBlock, int]]:
        self.streams += 1
        try:
            for block in self._blocks:
                if not wanted(block):
                    continue
                self.scene.time = block.start
                for frame in range(scrub.block_frame_count(block, fps) + 1):
                    self.scene.time = block.start + frame / fps
                    self.streamed.append((block.index, frame))
                    yield block, frame
        finally:
            self.closed = True


@pytest.fixture(autouse=True)
def _fake_readback(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(scrub, "read_upright_rgba", lambda camera: (bytes(16), 2, 2))


def _crawler(
    *, start_time: float = 0.0, frame_budget: int = 1000, filmstrip: Filmstrip | None = None
):
    engine = _StubEngine(BLOCKS)
    crawler = scrub.FilmstripCrawler(
        engine,
        filmstrip if filmstrip is not None else Filmstrip(),
        FPS,
        start_time=start_time,
        frame_budget=frame_budget,
    )
    return engine, crawler


def _run(crawler: scrub.FilmstripCrawler) -> None:
    while not crawler.done:
        crawler.step()


def _blocks_streamed(engine: _StubEngine) -> list[int]:
    seen: list[int] = []
    for index, _frame in engine.streamed:
        if not seen or seen[-1] != index:
            seen.append(index)
    return seen


class TestSinglePass:
    def test_whole_scene_costs_one_stream_and_no_poses(self) -> None:
        engine, crawler = _crawler(start_time=0.0)
        _run(crawler)
        assert engine.streams == 1
        assert engine.poses == []
        assert _blocks_streamed(engine) == [0, 1, 2, 3]
        assert crawler.done and not crawler.capped

    def test_every_frame_of_every_block_is_cached(self) -> None:
        strip = Filmstrip()
        _, crawler = _crawler(filmstrip=strip)
        _run(crawler)
        # 4 one-second blocks at 10 fps, boundaries shared: frames 0..40.
        assert all(strip.get(index) is not None for index in range(0, 41))

    def test_stream_is_closed_when_done(self) -> None:
        engine, crawler = _crawler()
        _run(crawler)
        assert engine.closed

    def test_stream_is_closed_when_the_cache_caps(self) -> None:
        engine, crawler = _crawler(filmstrip=Filmstrip(max_frames=5))
        _run(crawler)
        assert crawler.capped and engine.closed


class TestPlayheadFirst:
    def test_playhead_block_is_posed_first_then_stream_skips_it(self) -> None:
        engine, crawler = _crawler(start_time=3.5)
        _run(crawler)
        assert engine.poses == [3.0]
        assert _blocks_streamed(engine) == [0, 1, 2]
        assert crawler.frames > 0
        assert crawler.done and not crawler.capped

    def test_playhead_in_first_block_needs_no_head_pose(self) -> None:
        engine, crawler = _crawler(start_time=0.4)
        _run(crawler)
        assert engine.poses == []
        assert _blocks_streamed(engine) == [0, 1, 2, 3]

    def test_prioritize_before_start_reaims_the_head(self) -> None:
        engine, crawler = _crawler(start_time=0.0)
        crawler.prioritize(2.5)
        _run(crawler)
        assert engine.poses == [2.0]
        assert _blocks_streamed(engine) == [0, 1, 3]

    def test_prioritize_is_a_noop_once_streaming(self) -> None:
        engine, crawler = _crawler(start_time=0.0, frame_budget=5)
        crawler.step()  # streaming has begun
        crawler.prioritize(3.5)
        _run(crawler)
        assert engine.poses == []
        assert _blocks_streamed(engine) == [0, 1, 2, 3]

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

    def test_seconds_to_playhead_is_recorded_for_the_first_block(self) -> None:
        _, crawler = _crawler(start_time=0.0)
        _run(crawler)
        assert crawler.seconds_to_playhead is not None
