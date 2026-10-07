"""Runtime tracer for ManimGL scenes.

The tracer wraps a *scene instance* before ``construct()`` runs and records every
``play()``/``wait()`` with its actual start time and duration, plus counters for the
rest of the scene API (``add``/``remove``/``save_state``/``undo``/...).

Runtime tracing (as opposed to static AST analysis) is what makes loops, conditionals
and computed run-times appear correctly on the timeline.

This module must stay free of manimlib/Qt imports; it works on duck-typed scenes.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from manim_studio.timeline_model import TimelineBlock

if TYPE_CHECKING:
    from collections.abc import Callable

__all__ = [
    "TraceEvent",
    "TraceRecorder",
    "TraceSnapshot",
    "TraceStats",
    "describe_play_arguments",
]

#: Scene methods that are wrapped on attach.  ``play``/``wait`` additionally produce
#: timeline blocks; everything else feeds :class:`TraceStats` and (optionally) events.
WRAPPED_METHODS: tuple[str, ...] = (
    "play",
    "wait",
    "add",
    "remove",
    "clear",
    "replace",
    "bring_to_front",
    "bring_to_back",
    "save_state",
    "undo",
    "redo",
)

_MAX_NAMES = 3


def _class_name(obj: Any) -> str:
    return type(obj).__name__


def _is_animation_like(obj: Any) -> bool:
    # Duck typing keeps this module free of a manimlib import: animations expose
    # run_time/mobject/interpolate, animation builders expose build/mobject.
    return hasattr(obj, "run_time") and hasattr(obj, "mobject") and hasattr(obj, "interpolate")


def _is_builder_like(obj: Any) -> bool:
    return hasattr(obj, "build") and hasattr(obj, "mobject") and not _is_animation_like(obj)


def _animation_name(obj: Any) -> str:
    if _is_builder_like(obj):
        mobject_name = _class_name(obj.mobject)
        return f"Animate({mobject_name})"
    if _is_animation_like(obj):
        name = _class_name(obj)
        if name.startswith("_"):
            return f"Animate({_class_name(obj.mobject)})"
        return name
    return _class_name(obj)


def describe_play_arguments(args: tuple[Any, ...]) -> str:
    """Build a compact human label for the mobjects/animations passed to ``play``.

    ``play(ShowCreation(c), Transform(c, s))`` -> ``"ShowCreation+Transform"``;
    long lists are truncated to ``"A+B+2"``.
    """
    if not args:
        return "Play"
    names: list[str] = []
    for arg in args:
        name = _animation_name(arg)
        if name not in names:
            names.append(name)
    if len(names) <= _MAX_NAMES:
        return "+".join(names)
    return "+".join(names[: _MAX_NAMES - 1]) + f"+{len(names) - (_MAX_NAMES - 1)}"


@dataclass(slots=True)
class TraceStats:
    """Counts of wrapped scene API calls (includes manimgl's internal ``add`` calls)."""

    counts: dict[str, int] = field(default_factory=dict)

    def bump(self, kind: str) -> None:
        self.counts[kind] = self.counts.get(kind, 0) + 1

    def get(self, kind: str) -> int:
        return self.counts.get(kind, 0)

    def to_dict(self) -> dict[str, int]:
        return dict(sorted(self.counts.items()))


@dataclass(frozen=True, slots=True)
class TraceEvent:
    """A non-timeline scene API call (``add``, ``save_state``, ...)."""

    kind: str
    time: float
    detail: str = ""
    during_play: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "time": self.time,
            "detail": self.detail,
            "during_play": self.during_play,
        }


@dataclass(frozen=True, slots=True)
class TraceSnapshot:
    """Everything the tracer observed during one scene run."""

    blocks: tuple[TimelineBlock, ...]
    events: tuple[TraceEvent, ...]
    stats: dict[str, int]
    total_duration: float

    @property
    def num_plays(self) -> int:
        return len(self.blocks)

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": 1,
            "blocks": [block.to_dict() for block in self.blocks],
            "events": [event.to_dict() for event in self.events],
            "stats": dict(self.stats),
            "total_duration": self.total_duration,
        }


class TraceRecorder:
    """Attaches to a scene instance and records its runtime behavior.

    Usage::

        recorder = TraceRecorder()
        recorder.attach(scene)
        scene.run()          # or scene.construct()
        snapshot = recorder.snapshot()

    The recorder shadows the wrapped methods on the *instance* only, so the scene
    class itself is never modified and ``detach()`` restores the originals.
    """

    def __init__(self, *, record_events: bool = True) -> None:
        self.record_events = record_events
        self._blocks: list[TimelineBlock] = []
        self._events: list[TraceEvent] = []
        self._stats = TraceStats()
        self._scene: Any = None
        self._saved: dict[str, Any] = {}
        self._play_depth = 0

    # -- lifecycle ---------------------------------------------------------

    @property
    def attached(self) -> bool:
        return self._scene is not None

    def attach(self, scene: Any) -> None:
        """Wrap the scene's API methods on the instance."""
        if self.attached:
            raise RuntimeError("TraceRecorder is already attached to a scene")
        self._scene = scene
        for name in WRAPPED_METHODS:
            method = getattr(scene, name, None)
            if method is None:
                continue
            self._saved[name] = method
            setattr(scene, name, self._make_wrapper(name, method))

    def detach(self) -> None:
        """Restore the original bound methods."""
        if self._scene is None:
            return
        for name, method in self._saved.items():
            setattr(self._scene, name, method)
        self._saved.clear()
        self._scene = None

    # -- recording ---------------------------------------------------------

    def _time(self) -> float:
        try:
            return float(self._scene.time)
        except AttributeError:
            return 0.0

    def _num_plays(self) -> int:
        try:
            return int(self._scene.num_plays)
        except AttributeError:
            return len(self._blocks)

    def _make_wrapper(self, name: str, original: Callable[..., Any]) -> Callable[..., Any]:
        if name == "play":
            return self._wrap_play(original)
        if name == "wait":
            return self._wrap_wait(original)
        return self._wrap_other(name, original)

    def _wrap_play(self, original: Callable[..., Any]) -> Callable[..., Any]:
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            start = self._time()
            index = self._num_plays()
            label = describe_play_arguments(args)
            self._stats.bump("play")
            self._play_depth += 1
            try:
                return original(*args, **kwargs)
            finally:
                self._play_depth -= 1
                # manimgl warns and returns for play() without animations; do not
                # clutter the timeline with a zero-duration ghost block.
                if args:
                    self._record_block(index, label, start)

        return wrapper

    def _wrap_wait(self, original: Callable[..., Any]) -> Callable[..., Any]:
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            start = self._time()
            index = self._num_plays()
            self._stats.bump("wait")
            try:
                return original(*args, **kwargs)
            finally:
                self._record_block(index, "Wait", start)

        return wrapper

    def _wrap_other(self, name: str, original: Callable[..., Any]) -> Callable[..., Any]:
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            self._stats.bump(name)
            if self.record_events:
                self._events.append(
                    TraceEvent(
                        kind=name,
                        time=self._time(),
                        detail=_describe_detail(args),
                        during_play=self._play_depth > 0,
                    )
                )
            return original(*args, **kwargs)

        return wrapper

    def _record_block(self, index: int, label: str, start: float) -> None:
        duration = max(0.0, self._time() - start)
        # Guard against scenes that never advance their clock (custom runners).
        if not math.isfinite(duration):
            duration = 0.0
        if self._blocks and self._blocks[-1].index == index:
            # Nested/edge case: keep one block per index, extend the existing one.
            existing = self._blocks[-1]
            self._blocks[-1] = TimelineBlock(
                index=existing.index,
                name=existing.name,
                start=existing.start,
                duration=max(0.0, self._time() - existing.start),
            )
            return
        self._blocks.append(TimelineBlock(index=index, name=label, start=start, duration=duration))

    # -- results -----------------------------------------------------------

    def snapshot(self) -> TraceSnapshot:
        """Immutable view of everything recorded so far."""
        return TraceSnapshot(
            blocks=tuple(self._blocks),
            events=tuple(self._events),
            stats=self._stats.to_dict(),
            total_duration=self._time(),
        )

    def reset(self) -> None:
        """Clear recordings without detaching (reuse across runs)."""
        self._blocks.clear()
        self._events.clear()
        self._stats = TraceStats()
        self._play_depth = 0

    def __enter__(self) -> TraceRecorder:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.detach()


def _describe_detail(args: tuple[Any, ...]) -> str:
    """Short description of mobject arguments for events."""
    if not args:
        return ""
    names = list(dict.fromkeys(_class_name(arg) for arg in args))
    if len(names) == 1:
        return names[0]
    if len(names) <= 3:
        return "+".join(names)
    return "+".join(names[:2]) + f"+{len(names) - 2}"
