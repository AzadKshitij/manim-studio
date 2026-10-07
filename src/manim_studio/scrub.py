"""Replay-based scene scrubbing and filmstrip crawling.

Posing a scene at an arbitrary time is done by replaying it (see ADR 0002):

1. the scene is replayed from the beginning in skip-animations mode on a single
   reusable instance — runtime replay, so loops/branches stay correct and no
   snapshot has to be trusted;
2. the block containing the target time is intercepted: its animations are built
   and *not played*, but interpolated to the exact ``alpha`` (and waits advance
   updaters by the remaining delta), leaving the scene posed mid-animation;
3. the caller renders one frame from that pose.

For smooth scrubbing, :class:`FilmstripCrawler` walks the same machinery through
every block once, rendering each frame at O(1) per frame (like manimgl's own
playback loop, minus timing sleeps and encoding) and filling a
:class:`~manim_studio.filmstrip.Filmstrip` cache that the host serves directly.
"""

from __future__ import annotations

import math
import random
import time as time_module
from typing import TYPE_CHECKING, Any

from manim_studio.timeline_model import TimelineBlock

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence

    from manim_studio.filmstrip import Filmstrip
    from manim_studio.stats import TimingStats

__all__ = ["FilmstripCrawler", "ScrubEngine", "ScrubError", "TimingScaler", "read_upright_rgba"]


class ScrubError(RuntimeError):
    """Raised when a scene cannot be replayed for scrubbing."""


def read_upright_rgba(camera: Any) -> tuple[bytes, int, int]:
    """Read a camera's current frame as upright RGBA bytes (top row first)."""
    import numpy as np

    width, height = (int(value) for value in camera.default_pixel_shape)
    raw = camera.get_raw_fbo_data()
    array = np.frombuffer(raw, dtype=np.uint8).reshape(height, width, 4)[::-1]
    return array.tobytes(), width, height


def _prepare_animation(animation: Any) -> Any:
    """Lazy wrapper around manimgl's ``prepare_animation`` (keeps this module GL-free)."""
    from manimlib.animation.animation import prepare_animation

    return prepare_animation(animation)


class ScrubEngine:
    """Owns one scene instance and poses it at arbitrary times.

    The scene is created once by the host (so it keeps a single GL context and
    FBO set); :meth:`pose` rewinds and replays it for every target.  After a pose
    that stopped inside a play or wait block, :attr:`paused_block` describes that
    block and the paused animations can be advanced with
    :meth:`interpolate_paused` — this is what the filmstrip crawler uses.
    """

    def __init__(
        self,
        scene: Any,
        blocks: Sequence[TimelineBlock],
        *,
        factors: Mapping[int, float] | None = None,
        clock: Callable[[], float] = time_module.perf_counter,
    ) -> None:
        self._scene = scene
        self._blocks = tuple(blocks)
        self._factors = dict(factors or {})
        self._clock = clock
        self._target = 0.0
        self._play_counter = 0
        self._paused_block: TimelineBlock | None = None
        self._paused_animations: list[Any] | None = None
        self._orig_play = scene.play
        self._orig_wait = scene.wait
        self.last_pose_seconds = 0.0
        scene.play = self._play
        scene.wait = self._wait

    # ------------------------------------------------------------------

    @property
    def total_duration(self) -> float:
        return max((block.end for block in self._blocks), default=0.0)

    @property
    def scene(self) -> Any:
        return self._scene

    @property
    def paused_block(self) -> TimelineBlock | None:
        return self._paused_block

    @property
    def paused_animations(self) -> list[Any] | None:
        return self._paused_animations

    def block_at(self, time: float) -> TimelineBlock | None:
        """The block containing ``time`` (clamped), or None for an empty trace."""
        if not self._blocks:
            return None
        for block in self._blocks:
            if block.start <= time < block.end:
                return block
        return self._blocks[0] if time < self._blocks[0].start else self._blocks[-1]

    def pose(self, time: float) -> TimelineBlock | None:
        """Replay the scene so it displays the state at ``time`` seconds.

        Returns the block containing ``time`` (or ``None`` when there is no trace).
        Raises :class:`ScrubError` if the user's construct fails during replay.
        """
        from manimlib.scene.scene import EndScene

        if not self._blocks:
            return None
        target = min(max(float(time), 0.0), self.total_duration)
        started = self._clock()
        self._reset(target)
        try:
            self._scene.setup()
            self._scene.construct()
        except EndScene:
            pass
        except Exception as exc:  # reported to the editor
            raise ScrubError(f"{type(exc).__name__}: {exc}") from exc
        self.last_pose_seconds = self._clock() - started
        return self.block_at(target)

    def interpolate_paused(self, local_time: float, dt: float) -> None:
        """Advance the paused play's animations to ``local_time`` inside its block.

        Mirrors manimgl's playback loop: per-animation alpha, animation updaters
        with ``dt``, then the scene's own updater pass and clock.
        """
        if self._paused_animations is None:
            raise ScrubError("no paused play to interpolate")
        scene = self._scene
        for animation in self._paused_animations:
            animation.update_mobjects(dt)
            animation.interpolate(local_time / max(float(animation.run_time), 1e-9))
        scene.update_mobjects(dt)
        scene.increment_time(dt)

    def advance_wait(self, dt: float) -> None:
        """Advance updaters through part of a paused wait block."""
        scene = self._scene
        scene.update_mobjects(dt)
        scene.increment_time(dt)

    # ------------------------------------------------------------------
    # Replay internals
    # ------------------------------------------------------------------

    def _reset(self, target: float) -> None:
        scene = self._scene
        # Deterministic replay, mirroring what Scene.__init__ does for the seed.
        random.seed(scene.random_seed if scene.random_seed is not None else 0)
        try:
            import numpy as np

            np.random.seed(scene.random_seed if scene.random_seed is not None else 0)
        except Exception:  # pragma: no cover - numpy is a manimgl dependency
            pass
        scene.mobjects = [scene.camera.frame]
        scene.render_groups = []
        scene.id_to_mobject_map = {}
        scene.time = 0.0
        scene.num_plays = 0
        scene.undo_stack = []
        scene.redo_stack = []
        scene.skip_animations = True
        scene.start_at_animation_number = None
        scene.end_at_animation_number = None
        scene.virtual_animation_start_time = 0.0
        scene.real_animation_start_time = time_module.time()
        self._target = target
        self._play_counter = 0
        self._paused_block = None
        self._paused_animations = None

    def _factor(self, block: TimelineBlock | None) -> float:
        if block is None:
            return 1.0
        return float(self._factors.get(block.index, 1.0))

    def _play(self, *args: Any, **kwargs: Any) -> Any:
        block = self._blocks[self._play_counter] if self._play_counter < len(self._blocks) else None
        if not args:
            # Mirrors TraceRecorder: play() without animations is not a block.
            return self._orig_play(*args, **kwargs)
        if block is not None and block.start <= self._target < block.end:
            return self._partial_play(args, kwargs, block)
        factor = self._factor(block)
        self._play_counter += 1
        if factor != 1.0:
            return self._orig_play(*self._scaled_animations(args, factor), **kwargs)
        return self._orig_play(*args, **kwargs)

    @staticmethod
    def _scaled_animations(args: tuple[Any, ...], factor: float) -> list[Any]:
        """Prepare ``play`` arguments and scale their run times by ``factor``.

        Preparing here is safe because manimgl's ``prepare_animation`` is
        idempotent for already-prepared animations, so ``Scene.play`` keeps the
        scaled ``run_time`` while preserving every lag ratio in the block.
        """
        animations = [_prepare_animation(arg) for arg in args]
        for animation in animations:
            animation.run_time = float(getattr(animation, "run_time", 1.0)) * factor
        return animations

    def _wait(self, *args: Any, **kwargs: Any) -> Any:
        block = self._blocks[self._play_counter] if self._play_counter < len(self._blocks) else None
        if block is not None and block.start <= self._target < block.end:
            return self._partial_wait(block)
        factor = self._factor(block)
        self._play_counter += 1
        if factor != 1.0:
            duration = kwargs.get("duration")
            if duration is None and args:
                duration = args[0]
            if duration is None:
                duration = float(getattr(self._scene, "default_wait_time", 1.0))
            if args:
                return self._orig_wait(duration * factor, *args[1:], **kwargs)
            scaled = {**kwargs, "duration": duration * factor}
            return self._orig_wait(**scaled)
        return self._orig_wait(*args, **kwargs)

    def _partial_play(
        self, args: tuple[Any, ...], kwargs: dict[str, Any], block: TimelineBlock
    ) -> None:
        """Interpolate the target block's animations to the exact alpha and stop."""
        from manimlib.scene.scene import EndScene

        scene = self._scene
        animations = list(map(_prepare_animation, args))
        factor = self._factor(block)
        for animation in animations:
            animation.update_rate_info(
                kwargs.get("run_time"), kwargs.get("rate_func"), kwargs.get("lag_ratio")
            )
            if factor != 1.0:
                animation.run_time = float(animation.run_time) * factor
        scene.pre_play()
        scene.begin_animations(animations)
        local_time = max(0.0, self._target - block.start)
        for animation in animations:
            # Same per-animation alpha the normal playback loop computes.
            animation.interpolate(local_time / max(float(animation.run_time), 1e-9))
        scene.update_mobjects(0)
        self._sync_time()
        self._paused_block = block
        self._paused_animations = animations
        raise EndScene()

    def _partial_wait(self, block: TimelineBlock) -> None:
        """Advance updaters through part of a wait, then stop."""
        from manimlib.scene.scene import EndScene

        scene = self._scene
        delta = max(0.0, self._target - scene.time)
        scene.update_mobjects(delta)
        scene.increment_time(delta)
        self._paused_block = block
        self._paused_animations = None
        raise EndScene()

    def _sync_time(self) -> None:
        scene = self._scene
        delta = self._target - scene.time
        if delta > 0:
            scene.increment_time(delta)


class TimingScaler:
    """Scale a scene's ``play``/``wait`` durations from per-block factors.

    Real playback runs the user's ``construct`` at manimgl's own speed; wrapping
    ``play``/``wait`` scales each animation's ``run_time`` (and each wait's
    duration) so playback reproduces the timeline the user edited, without
    touching the scene source.  Block indexes come from ``scene.num_plays`` at
    call entry — exactly the value the tracer records.
    """

    def __init__(self, scene: Any, factors: Mapping[int, float]) -> None:
        self._scene = scene
        self._factors = dict(factors)
        self._orig_play = scene.play
        self._orig_wait = scene.wait
        scene.play = self._play
        scene.wait = self._wait

    def _factor(self) -> float:
        try:
            index = int(self._scene.num_plays)
        except (AttributeError, TypeError, ValueError):
            return 1.0
        return float(self._factors.get(index, 1.0))

    def _play(self, *args: Any, **kwargs: Any) -> Any:
        factor = self._factor()
        if not args or factor == 1.0:
            return self._orig_play(*args, **kwargs)
        return self._orig_play(*ScrubEngine._scaled_animations(args, factor), **kwargs)

    def _wait(self, *args: Any, **kwargs: Any) -> Any:
        factor = self._factor()
        if factor == 1.0:
            return self._orig_wait(*args, **kwargs)
        duration = kwargs.get("duration")
        if duration is None and args:
            duration = args[0]
        if duration is None:
            duration = float(getattr(self._scene, "default_wait_time", 1.0))
        if args:
            return self._orig_wait(duration * factor, *args[1:], **kwargs)
        return self._orig_wait(**{**kwargs, "duration": duration * factor})


class FilmstripCrawler:
    """Fills a filmstrip by walking every block once, rendering each frame.

    Per block the crawler first replays the scene to the block start (fast, skip
    mode) and then advances frames incrementally — O(1) per frame instead of one
    replay per frame, which is what makes scrubbing smooth in already-visited
    regions.  :meth:`step` renders a bounded number of frames per call so the
    host keeps servicing commands while the cache fills.
    """

    def __init__(
        self,
        engine: ScrubEngine,
        filmstrip: Filmstrip,
        fps: int,
        *,
        on_progress: Callable[[int, bool, bool], None] | None = None,
        frame_budget: int = 6,
        timings: TimingStats | None = None,
        start_time: float = 0.0,
    ) -> None:
        self._engine = engine
        self._filmstrip = filmstrip
        self._fps = max(1, int(fps))
        self._on_progress = on_progress
        self._timings = timings
        self.frame_budget = max(1, int(frame_budget))
        self._blocks = tuple(engine._blocks)
        # Visit order is playhead-first: the block nearest ``start_time`` renders
        # first, then its neighbours.  Reordering costs nothing extra because
        # posing any block replays the scene from the start regardless.
        self._start_time = float(start_time)
        self._order = sorted(
            range(len(self._blocks)), key=lambda index: abs(self._blocks[index].start - start_time)
        )
        self._position = 0
        self._done_duration = 0.0
        self._frame = 0
        self._frames = 0
        self._done = False
        self._capped = False
        #: Seconds from construction until the playhead's block was cached.
        self._t0 = time_module.perf_counter()
        self.seconds_to_playhead: float | None = None

    # ------------------------------------------------------------------

    @property
    def done(self) -> bool:
        return self._done

    @property
    def capped(self) -> bool:
        return self._capped

    @property
    def frames(self) -> int:
        return self._frames

    @property
    def progress(self) -> float:
        """Monotonic completion estimate in [0, 1] based on block durations."""
        total = self._engine.total_duration
        if total <= 0 or self._position >= len(self._order):
            return 1.0
        block = self._blocks[self._order[self._position]]
        done = self._done_duration + min(max(self._frame - 1, 0) / self._fps, block.duration)
        return min(max(done / total, 0.0), 1.0)

    def prioritize(self, time: float) -> None:
        """Re-aim the pending crawl at ``time`` (a new playhead or seek).

        The block currently being rendered keeps its slot so its partial frames
        stay valid; only the queue behind it is re-sorted nearest-first.
        """
        if self._done:
            return
        head = self._position + (1 if self._frame > 0 else 0)
        if head >= len(self._order):
            return
        blocks = self._blocks
        self._order[head:] = sorted(
            self._order[head:], key=lambda index: abs(blocks[index].start - time)
        )

    def step(self) -> int:
        """Render up to :attr:`frame_budget` frames; returns the number rendered."""
        rendered = 0
        while rendered < self.frame_budget and not self._done:
            if self._position >= len(self._order):
                self._finish()
                break
            block = self._blocks[self._order[self._position]]
            if self._engine.paused_block is not block:
                self._engine.pose(block.start)
                self._frame = 0
                if self._engine.paused_block is not block:
                    # Zero-length block (or a target the replay skipped): no frames.
                    self._complete_block(block)
                    continue
            frame_count = self._block_frame_count(block)
            if self._frame == 0:
                self._render()
                self._frame = 1
                rendered += 1
                continue
            if self._frame <= frame_count:
                dt = 1.0 / self._fps
                local = min(self._frame / self._fps, block.duration)
                if self._engine.paused_animations is not None:
                    self._engine.interpolate_paused(local, dt)
                else:
                    self._engine.advance_wait(dt)
                self._render()
                self._frame += 1
                rendered += 1
                continue
            self._complete_block(block)
        return rendered

    # ------------------------------------------------------------------

    def _block_frame_count(self, block: TimelineBlock) -> int:
        """Frame count manimgl's own loop would produce for this block."""
        return max(1, math.ceil(block.duration * self._fps - 1e-9))

    def _complete_block(self, block: TimelineBlock) -> None:
        self._done_duration += block.duration
        self._position += 1
        self._frame = 0
        if self.seconds_to_playhead is None and self._is_playhead_block(block):
            self.seconds_to_playhead = time_module.perf_counter() - self._t0

    def _is_playhead_block(self, block: TimelineBlock) -> bool:
        start = self._start_time
        if block.start <= start < block.end:
            return True
        # The playhead often sits exactly at the end, which no half-open block
        # contains; treat that as the final block.
        return bool(self._blocks) and block is self._blocks[-1] and start >= block.end

    def _finish(self) -> None:
        if not self._done:
            self._done = True
            self._notify()

    def _render(self) -> None:
        from manim_studio.ipc import encode_frame

        scene = self._engine.scene
        timings = self._timings
        if timings is None:
            scene.camera.capture(*scene.render_groups)
            rgba, width, height = read_upright_rgba(scene.camera)
        else:
            with timings.phase("crawl_capture"):
                scene.camera.capture(*scene.render_groups)
                rgba, width, height = read_upright_rgba(scene.camera)
        index = round(scene.time * self._fps)
        if timings is None:
            blob = encode_frame(width, height, rgba)
        else:
            with timings.phase("crawl_encode"):
                blob = encode_frame(width, height, rgba)
        if not self._filmstrip.put(index, blob):
            self._capped = True
            self._finish()
            return
        self._frames += 1
        if self._filmstrip.frames >= self._filmstrip.max_frames:
            self._capped = True
            self._finish()
            return
        if self._frames % 15 == 0:
            self._notify()

    def _notify(self) -> None:
        if self._on_progress is not None:
            self._on_progress(self._frames, self._done, self._capped)
