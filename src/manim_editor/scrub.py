"""Replay-based scene scrubbing.

Poses a manimgl scene at an arbitrary point in time.  The technique avoids the
state-snapshot approach the original spec warned about (``save_state()`` per
boundary + ``anim.interpolate(alpha)``, which breaks with ``always_redraw`` and
updaters):

1. the scene is replayed from the beginning in skip-animations mode on a single
   reusable instance — runtime replay, so loops/branches stay correct and no
   snapshot has to be trusted;
2. the block containing the target time is intercepted: its animations are built
   and *not played*, but interpolated to the exact ``alpha`` (and waits advance
   updaters by the remaining delta), leaving the scene posed mid-animation;
3. the caller renders one frame from that pose.

Because the normal animation/update machinery runs, ``always_redraw`` and
updaters behave the same way they do when playback pauses on a frame.
"""

from __future__ import annotations

import random
import time as time_module
from typing import TYPE_CHECKING, Any

from manim_editor.timeline_model import TimelineBlock

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

__all__ = ["ScrubEngine", "ScrubError"]


class ScrubError(RuntimeError):
    """Raised when a scene cannot be replayed for scrubbing."""


class ScrubEngine:
    """Owns one scene instance and poses it at arbitrary times.

    The scene is created once by the host (so it keeps a single GL context and
    FBO set); :meth:`pose` rewinds and replays it for every target.
    """

    def __init__(
        self,
        scene: Any,
        blocks: Sequence[TimelineBlock],
        *,
        clock: Callable[[], float] = time_module.perf_counter,
    ) -> None:
        self._scene = scene
        self._blocks = tuple(blocks)
        self._clock = clock
        self._target = 0.0
        self._play_counter = 0
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

    def _play(self, *args: Any, **kwargs: Any) -> Any:
        block = self._blocks[self._play_counter] if self._play_counter < len(self._blocks) else None
        if not args:
            # Mirrors TraceRecorder: play() without animations is not a block.
            return self._orig_play(*args, **kwargs)
        if block is not None and block.start <= self._target < block.end:
            return self._partial_play(args, kwargs, block)
        self._play_counter += 1
        return self._orig_play(*args, **kwargs)

    def _wait(self, *args: Any, **kwargs: Any) -> Any:
        block = self._blocks[self._play_counter] if self._play_counter < len(self._blocks) else None
        if block is not None and block.start <= self._target < block.end:
            return self._partial_wait(block)
        self._play_counter += 1
        return self._orig_wait(*args, **kwargs)

    def _partial_play(
        self, args: tuple[Any, ...], kwargs: dict[str, Any], block: TimelineBlock
    ) -> None:
        """Interpolate the target block's animations to the exact alpha and stop."""
        from manimlib.animation.animation import prepare_animation
        from manimlib.scene.scene import EndScene

        scene = self._scene
        animations = list(map(prepare_animation, args))
        for animation in animations:
            animation.update_rate_info(
                kwargs.get("run_time"), kwargs.get("rate_func"), kwargs.get("lag_ratio")
            )
        scene.pre_play()
        scene.begin_animations(animations)
        local_time = max(0.0, self._target - block.start)
        for animation in animations:
            # Same per-animation alpha the normal playback loop computes.
            animation.interpolate(local_time / max(float(animation.run_time), 1e-9))
        scene.update_mobjects(0)
        self._sync_time(block)
        raise EndScene()

    def _partial_wait(self, block: TimelineBlock) -> None:
        """Advance updaters through part of a wait, then stop."""
        from manimlib.scene.scene import EndScene

        scene = self._scene
        delta = max(0.0, self._target - scene.time)
        scene.update_mobjects(delta)
        scene.increment_time(delta)
        raise EndScene()

    def _sync_time(self, block: TimelineBlock) -> None:
        scene = self._scene
        delta = self._target - scene.time
        if delta > 0:
            scene.increment_time(delta)
