"""Replay-based scene scrubbing and filmstrip crawling.

Posing a scene at an arbitrary time is done by replaying it (see ADR 0002):

1. the scene is replayed from the beginning in skip-animations mode on a single
   reusable instance — runtime replay, so loops/branches stay correct and no
   snapshot has to be trusted;
2. the block containing the target time is intercepted: its animations are built
   and *not played*, but interpolated to the exact ``alpha`` (and waits advance
   updaters by the remaining delta), leaving the scene posed mid-animation;
3. the caller renders one frame from that pose.

For smooth scrubbing, :class:`FilmstripCrawler` streams the scene once (construct()
suspended at every frame, see :meth:`ScrubEngine.stream_frames`), rendering each frame
at O(1) per frame (like manimgl's own playback loop, minus timing sleeps and
encoding) and filling a
:class:`~manim_studio.filmstrip.Filmstrip` cache that the host serves directly.
"""

from __future__ import annotations

import math
import random
import time as time_module
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from manim_studio.timeline_model import TimelineBlock

if TYPE_CHECKING:
    from collections.abc import Callable, Collection, Iterator, Mapping, Sequence

    from manim_studio.filmstrip import Filmstrip
    from manim_studio.stats import TimingStats

__all__ = [
    "FilmstripCrawler",
    "ScrubEngine",
    "ScrubError",
    "TimingScaler",
    "block_frame_count",
    "read_upright_rgba",
]


class ScrubError(RuntimeError):
    """Raised when a scene cannot be replayed for scrubbing."""


def block_frame_count(block: TimelineBlock, fps: int) -> int:
    """Frame count manimgl's own loop would produce for ``block`` at ``fps``."""
    return max(1, math.ceil(block.duration * max(1, int(fps)) - 1e-9))


@dataclass(frozen=True)
class _StreamState:
    """Settings of one :meth:`ScrubEngine.stream_frames` pass."""

    fps: int
    wanted: Callable[[TimelineBlock], bool]


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


def _scale_play_call(
    args: tuple[Any, ...], kwargs: dict[str, Any], factor: float
) -> tuple[tuple[Any, ...], dict[str, Any]]:
    """Scale a ``play()`` call's duration by ``factor``.

    manimgl's ``Scene.play`` re-applies an explicit ``run_time`` keyword to every
    animation (``update_rate_info``), which *overwrites* any per-animation scaling.
    So when the scene passes ``run_time=`` explicitly, that keyword must be scaled
    instead; only when it is absent do the animations' own run times need scaling.
    Getting this wrong was why retimed/speed-adjusted playback looked unchanged for
    the many scenes that pass ``run_time=`` to every ``play``.
    """
    run_time = kwargs.get("run_time")
    if run_time is not None:
        return args, {**kwargs, "run_time": float(run_time) * factor}
    return tuple(ScrubEngine._scaled_animations(args, factor)), kwargs


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
        self._stream: _StreamState | None = None
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

    def stream_frames(
        self, fps: int, wanted: Callable[[TimelineBlock], bool]
    ) -> Iterator[tuple[TimelineBlock, int]]:
        """Replay the scene **once**, pausing at every frame of every wanted block.

        Yields ``(block, frame)`` with the scene posed exactly at that frame (frame
        0 is the block's start, ``block_frame_count`` its last); the caller renders
        it before asking for the next one.  Blocks for which ``wanted`` is false
        run in skip mode like any replayed prefix.  ``construct()`` is suspended on
        a greenlet (same OS thread, so GL stays single-threaded) instead of being
        replayed once per block as :meth:`pose` would require.

        Raises :class:`ScrubError` if the user's construct fails.  Closing the
        generator early unwinds the suspended ``construct()``.
        """
        import greenlet
        from manimlib.scene.scene import EndScene

        if not self._blocks:
            return
        self._reset(float("inf"))  # no target: nothing is intercepted as a partial block
        self._stream = _StreamState(max(1, int(fps)), wanted)
        scene = self._scene

        def run() -> None:
            try:
                scene.setup()
                scene.construct()
            except EndScene:
                pass

        child = greenlet.greenlet(run)
        started = self._clock()
        try:
            while True:
                try:
                    item = child.switch()
                except Exception as exc:  # reported to the editor
                    raise ScrubError(f"{type(exc).__name__}: {exc}") from exc
                if child.dead:
                    return
                yield item
        finally:
            self._stream = None
            if not child.dead:
                child.throw()  # GreenletExit: unwind construct() without running it on
            self.last_pose_seconds = self._clock() - started

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
        if block is not None and self._streams(block):
            return self._stream_play(args, kwargs, block)
        if block is not None and block.start <= self._target < block.end:
            return self._partial_play(args, kwargs, block)
        factor = self._factor(block)
        self._play_counter += 1
        if factor != 1.0:
            scaled_args, scaled_kwargs = _scale_play_call(args, kwargs, factor)
            return self._orig_play(*scaled_args, **scaled_kwargs)
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
        if block is not None and self._streams(block):
            return self._stream_wait(block)
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

    def _streams(self, block: TimelineBlock) -> bool:
        stream = self._stream
        return stream is not None and block.duration > 0 and stream.wanted(block)

    @staticmethod
    def _emit(block: TimelineBlock, frame: int) -> None:
        """Hand control to the consumer; returns when it asks for the next frame."""
        import greenlet

        parent = greenlet.getcurrent().parent
        if parent is not None:
            parent.switch((block, frame))

    def _stream_play(
        self, args: tuple[Any, ...], kwargs: dict[str, Any], block: TimelineBlock
    ) -> None:
        """Play ``block`` frame by frame, yielding each pose to the stream consumer."""
        scene = self._scene
        stream = self._stream
        assert stream is not None
        animations = list(map(_prepare_animation, args))
        factor = self._factor(block)
        for animation in animations:
            animation.update_rate_info(
                kwargs.get("run_time"), kwargs.get("rate_func"), kwargs.get("lag_ratio")
            )
            if factor != 1.0:
                animation.run_time = float(animation.run_time) * factor
        self._play_counter += 1
        scene.pre_play()
        scene.begin_animations(animations)
        for animation in animations:
            animation.interpolate(0.0)
        scene.update_mobjects(0)
        self._emit(block, 0)
        dt = 1.0 / stream.fps
        for frame in range(1, block_frame_count(block, stream.fps) + 1):
            local = min(frame * dt, block.duration)
            for animation in animations:
                animation.update_mobjects(dt)
                animation.interpolate(local / max(float(animation.run_time), 1e-9))
            scene.update_mobjects(dt)
            scene.increment_time(dt)
            self._emit(block, frame)
        for animation in animations:
            animation.finish()
            animation.clean_up_from_scene(scene)
        scene.update_mobjects(0)
        self._finish_streamed(block)

    def _stream_wait(self, block: TimelineBlock) -> None:
        """Advance updaters through ``block`` frame by frame, yielding each pose."""
        scene = self._scene
        stream = self._stream
        assert stream is not None
        self._play_counter += 1
        scene.pre_play()
        scene.update_mobjects(0)
        self._emit(block, 0)
        dt = 1.0 / stream.fps
        for frame in range(1, block_frame_count(block, stream.fps) + 1):
            scene.update_mobjects(dt)
            scene.increment_time(dt)
            self._emit(block, frame)
        self._finish_streamed(block)

    def _finish_streamed(self, block: TimelineBlock) -> None:
        # Frame stepping overshoots by up to one frame; land exactly on the block end so
        # the next block starts where the trace says it does.  ``num_plays`` is what
        # ``Scene.post_play`` would bump (its preview draw is wasted work here).
        self._scene.time = block.end
        self._scene.num_plays += 1

    def _sync_time(self) -> None:
        scene = self._scene
        delta = self._target - scene.time
        if delta > 0:
            scene.increment_time(delta)


class TimingScaler:
    """Retime playback and apply a review-speed multiplier to a scene.

    Real playback runs the user's ``construct`` at manimgl's own pace, so the
    timeline only becomes real if the replay is adjusted:

    * **retiming** (per-block ``factors``) wraps ``play``/``wait`` and scales each
      animation's ``run_time`` / wait duration, so blocks take the duration the
      user dragged them to;
    * **speed** (a global multiplier) instead scales the *frame step* through
      ``get_time_progression``.  Scaling ``run_time`` would make ``scene.time``
      stop short of the timeline total (the head would halt partway at 2x), so
      the animation durations are left alone and only the number of frames per
      second of scene time changes — ``scene.time`` still reaches the timeline end.

    Block indexes come from ``scene.num_plays`` at call entry, exactly the value
    the tracer records.
    """

    def __init__(self, scene: Any, factors: Mapping[int, float], *, speed: float = 1.0) -> None:
        self._scene = scene
        self._factors = dict(factors)
        self._speed = float(speed) if float(speed) > 0 else 1.0
        self._orig_play = scene.play
        self._orig_wait = scene.wait
        self._orig_time_progression = scene.get_time_progression
        self._orig_update_frame = scene.update_frame
        self._pace_virtual: float | None = None
        self._pace_real = 0.0
        scene.play = self._play
        scene.wait = self._wait
        if self._speed != 1.0:
            scene.get_time_progression = self._time_progression
            scene.update_frame = self._update_frame

    def _factor(self) -> float:
        try:
            index = int(self._scene.num_plays)
            return float(self._factors.get(index, 1.0))
        except (AttributeError, TypeError, ValueError):
            return 1.0

    def _play(self, *args: Any, **kwargs: Any) -> Any:
        factor = self._factor()
        if not args or factor == 1.0:
            return self._orig_play(*args, **kwargs)
        scaled_args, scaled_kwargs = _scale_play_call(args, kwargs, factor)
        return self._orig_play(*scaled_args, **scaled_kwargs)

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

    def _time_progression(
        self,
        run_time: float,
        n_iterations: int | None = None,
        desc: str = "",
        override_skip_animations: bool = False,
    ) -> Any:
        """Frame times for one play/wait, stepped ``speed``x coarser.

        The per-frame step is ``speed / fps`` of *scene* time, so the loop covers
        the full ``run_time`` in ``speed``x fewer frames (faster wall-clock) while
        ``scene.time`` still advances by exactly ``run_time`` — the playhead stays
        on the timeline.  Skip mode (a single step) and the interactive progress
        display are left to manimgl.
        """
        scene = self._scene
        if (
            self._speed == 1.0
            or getattr(scene, "show_animation_progress", False)
            or (getattr(scene, "skip_animations", False) and not override_skip_animations)
            or run_time is None
            or float(run_time) <= 0
        ):
            return self._orig_time_progression(
                run_time, n_iterations, desc, override_skip_animations
            )
        import numpy as np

        fps = float(getattr(scene.camera, "fps", 30) or 30)
        step = self._speed / max(fps, 1.0)
        return np.arange(0.0, float(run_time), step) + step

    def _update_frame(self, dt: float = 0, force_draw: bool = False) -> Any:
        """Render a frame, but let the wall clock run ``speed``x faster.

        manimgl paces playback to real time (``time.sleep(max(vt - rt, 0))`` where
        ``vt`` is scene time since the block started and ``rt`` is wall time), so
        halving the frame count alone would just lengthen each sleep.  Shifting
        ``real_animation_start_time`` to ``block_start - vt*(1 - 1/speed)`` makes the
        sleep target ``vt/speed`` — one ``speed``-th of the wall time for the same
        scene time — so the animation really plays ``speed``x faster.
        """
        scene = self._scene
        if getattr(scene, "window", None) and not getattr(scene, "skip_animations", False):
            virtual_start = float(getattr(scene, "virtual_animation_start_time", 0.0))
            if self._pace_virtual != virtual_start:
                # manimgl resets both clocks at the start of every play/wait.
                self._pace_virtual = virtual_start
                self._pace_real = float(getattr(scene, "real_animation_start_time", 0.0))
            target = (float(scene.time) - virtual_start) + dt
            scene.real_animation_start_time = self._pace_real - target * (1.0 - 1.0 / self._speed)
        return self._orig_update_frame(dt, force_draw)


class FilmstripCrawler:
    """Fills a filmstrip in one pass over the scene, rendering each frame.

    Two phases, both bounded per :meth:`step` so the host keeps servicing commands:

    1. **Head**: the block under the playhead is posed directly (one replay) so the
       region the user is looking at is cached first.  Skipped when the playhead is
       already in the first block.
    2. **Stream**: one replay of ``construct()`` suspended at every frame of every
       block not yet cached (:meth:`ScrubEngine.stream_frames`), so the whole scene
       costs a single replay instead of one per block.

    Rendering is O(1) per frame, like manimgl's own playback loop minus timing
    sleeps and encoding.
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
        cached_blocks: Collection[int] = (),
    ) -> None:
        self._engine = engine
        self._filmstrip = filmstrip
        self._fps = max(1, int(fps))
        self._on_progress = on_progress
        self._timings = timings
        self.frame_budget = max(1, int(frame_budget))
        self._blocks = tuple(engine._blocks)
        self._start_time = float(start_time)
        #: Indices of blocks whose frames are all cached (carried over from a reload or
        #: completed by this crawl).
        self._cached: set[int] = set(cached_blocks)
        self._head: TimelineBlock | None = self._nearest_head(self._start_time)
        self._stream: Iterator[tuple[TimelineBlock, int]] | None = None
        #: Block being rendered and the next frame to render within it.
        self._cur_block: TimelineBlock | None = None
        self._done_duration = sum(b.duration for b in self._blocks if b.index in self._cached)
        self._frame = 0
        self._frames = filmstrip.frames
        self._done = False
        self._capped = False
        #: Seconds from construction until the playhead's block was cached.
        self._t0 = time_module.perf_counter()
        self.seconds_to_playhead: float | None = (
            0.0
            if any(self._is_playhead_block(b) for b in self._blocks if b.index in self._cached)
            else None
        )

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
    def cached_blocks(self) -> frozenset[int]:
        """Blocks whose frames are fully cached (reused from before or crawled)."""
        return frozenset(self._cached)

    @property
    def progress(self) -> float:
        """Monotonic completion estimate in [0, 1] based on block durations."""
        total = self._engine.total_duration
        if self._done or total <= 0:
            return 1.0
        partial = 0.0
        if self._cur_block is not None:
            partial = min(max(self._frame - 1, 0) / self._fps, self._cur_block.duration)
        return min(max((self._done_duration + partial) / total, 0.0), 1.0)

    def prioritize(self, time: float) -> None:
        """Re-aim the not-yet-started head phase at ``time`` (a new playhead or seek).

        Once the stream is running it renders blocks in timeline order: it owns the
        engine's scene, so it cannot be interrupted for a far-away block.  Scrubbing
        there is still served by the host's own replay pose on a cache miss.
        """
        if self._done or self._stream is not None or self._cur_block is not None:
            return
        self._start_time = float(time)
        self._head = self._nearest_head(self._start_time)

    def step(self) -> int:
        """Render up to :attr:`frame_budget` frames; returns the number rendered."""
        rendered = 0
        while rendered < self.frame_budget and not self._done:
            if self._stream is None and self._head is None and not self._has_pending():
                self._finish()  # everything was reused: no need to replay the scene at all
                break
            if self._head is not None:
                rendered += self._step_head(self._head)
                continue
            if self._stream is None:
                self._stream = self._engine.stream_frames(self._fps, self._wanted)
            try:
                block, frame = next(self._stream)
            except StopIteration:
                self._finish()
                break
            self._cur_block = block
            self._frame = frame + 1
            self._render(boundary=frame > 0 and frame >= self._block_frame_count(block))
            rendered += 1
            if self._done:
                break
            if frame >= self._block_frame_count(block):
                self._complete_block(block)
        return rendered

    # ------------------------------------------------------------------

    def _has_pending(self) -> bool:
        return any(b.duration > 0 and b.index not in self._cached for b in self._blocks)

    def _wanted(self, block: TimelineBlock) -> bool:
        return block.index not in self._cached

    def _nearest_head(self, time: float) -> TimelineBlock | None:
        """The uncached block nearest ``time``, unless the stream reaches it first anyway."""
        pending = [block for block in self._blocks if block.index not in self._cached]
        if not pending:
            return None
        nearest = min(pending, key=lambda block: abs(block.start - time))
        return None if nearest is pending[0] else nearest

    def _step_head(self, block: TimelineBlock) -> int:
        """One frame of the head block via a direct pose; returns frames rendered."""
        engine = self._engine
        if engine.paused_block is not block:
            engine.pose(block.start)
            self._frame = 0
            self._cur_block = block
            if engine.paused_block is not block:
                # Zero-length block (or a target the replay skipped): no frames.
                self._complete_block(block)
                return 0
        if self._frame == 0:
            self._render()
            self._frame = 1
            return 1
        if self._frame <= self._block_frame_count(block):
            dt = 1.0 / self._fps
            local = min(self._frame / self._fps, block.duration)
            if engine.paused_animations is not None:
                engine.interpolate_paused(local, dt)
            else:
                engine.advance_wait(dt)
            self._render(boundary=self._frame >= self._block_frame_count(block))
            self._frame += 1
            return 1
        self._complete_block(block)
        return 0

    def _block_frame_count(self, block: TimelineBlock) -> int:
        """Frame count manimgl's own loop would produce for this block."""
        return block_frame_count(block, self._fps)

    def _complete_block(self, block: TimelineBlock) -> None:
        self._cached.add(block.index)
        self._done_duration += block.duration
        self._cur_block = None
        self._frame = 0
        if block is self._head:
            self._head = None
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
            self._close_stream()
            self._notify()

    def _close_stream(self) -> None:
        stream, self._stream = self._stream, None
        close = getattr(stream, "close", None)
        if close is not None:
            close()

    def _render(self, *, boundary: bool = False) -> None:
        """Store the scene's current pose as a frame.

        A block's end pose (``boundary``) is only a placeholder for the next block's first
        frame.  When that frame is already cached (the next block was reused and shifted),
        the cached frame must win, or the boundary would show this block's last pose.
        """
        from manim_studio.ipc import encode_frame

        scene = self._engine.scene
        if boundary and self._filmstrip.has(round(scene.time * self._fps)):
            return
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
