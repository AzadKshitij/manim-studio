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
import sys
from dataclasses import dataclass, field
from itertools import islice
from typing import TYPE_CHECKING, Any

from manim_studio.timeline_model import TimelineBlock

if TYPE_CHECKING:
    from collections.abc import Callable

__all__ = [
    "BlockSite",
    "ClipTrace",
    "SiteResolver",
    "TraceEvent",
    "TraceRecorder",
    "TraceSnapshot",
    "TraceStats",
    "describe_play_arguments",
    "parse_clips",
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

#: ``(filename, line, UTF-8 byte column)`` of a call -> the call-site fingerprint, or
#: ``None`` when the call is not in a file the caller cares about.
type SiteResolver = Callable[[str, int, int], str | None]


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
class ClipTrace:
    """One animation inside a ``play()``: what it animates and for how long.

    ``lane`` numbers the animated mobjects in order of first appearance across the
    whole trace, so the timeline can draw one row per mobject.  ``start`` is the
    offset inside the block (always ``0.0`` today: manimgl starts every animation
    of a ``play()`` together).
    """

    animation: str
    target: str
    lane: int
    start: float
    duration: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "animation": self.animation,
            "target": self.target,
            "lane": self.lane,
            "start": self.start,
            "duration": self.duration,
        }

    @classmethod
    def from_dict(cls, data: Any) -> ClipTrace | None:
        """Parse one wire clip; ``None`` when it is malformed."""
        if not isinstance(data, dict):
            return None
        try:
            animation, target = data["animation"], data["target"]
            lane, start, duration = int(data["lane"]), float(data["start"]), float(data["duration"])
        except (KeyError, TypeError, ValueError):
            return None
        if not (isinstance(animation, str) and isinstance(target, str)):
            return None
        if lane < 0 or not math.isfinite(start) or not math.isfinite(duration) or duration < 0:
            return None
        return cls(animation, target, lane, max(0.0, start), duration)


def parse_clips(raw: object) -> tuple[tuple[ClipTrace, ...], ...]:
    """Coerce a host ``clips`` payload into per-block clip tuples (bad entries dropped)."""
    if not isinstance(raw, list):
        return ()
    blocks: list[tuple[ClipTrace, ...]] = []
    for item in raw:
        entries = item if isinstance(item, list) else []
        blocks.append(tuple(c for c in map(ClipTrace.from_dict, entries) if c is not None))
    return tuple(blocks)


@dataclass(frozen=True, slots=True)
class BlockSite:
    """Which call site produced a block, and which execution of it (0-based)."""

    site_id: str
    occurrence: int

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.site_id, "occ": self.occurrence}


@dataclass(frozen=True, slots=True)
class TraceSnapshot:
    """Everything the tracer observed during one scene run."""

    blocks: tuple[TimelineBlock, ...]
    events: tuple[TraceEvent, ...]
    stats: dict[str, int]
    total_duration: float
    #: Source call site ``(filename, line)`` for each block, ``None`` when unknown.
    #: A parallel array so the frozen ``TimelineBlock``/``log.json`` shape stays as-is.
    call_sites: tuple[tuple[str, int] | None, ...] = ()
    #: Call-site fingerprint + loop occurrence per block; ``None`` when no resolver
    #: was installed or the call is outside the resolved file.  Parallel to ``blocks``.
    block_sites: tuple[BlockSite | None, ...] = ()
    #: The animations of each block (empty for ``wait()``), parallel to ``blocks``.
    block_clips: tuple[tuple[ClipTrace, ...], ...] = ()
    #: Whether a time-based updater (one that takes ``dt``) was alive at the start or the
    #: end of each block, parallel to ``blocks``.  Such updaters make the scene state
    #: depend on how long a block lasted, which decides whether frames can be reused
    #: after a retime (see ``manim_studio.reuse``).
    block_dynamic: tuple[bool, ...] = ()

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
            "call_sites": [
                None if site is None else {"file": site[0], "line": site[1]}
                for site in self.call_sites
            ],
            "sites": [None if site is None else site.to_dict() for site in self.block_sites],
            "clips": [[clip.to_dict() for clip in clips] for clips in self.block_clips],
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

    def __init__(
        self,
        *,
        record_events: bool = True,
        site_resolver: SiteResolver | None = None,
    ) -> None:
        self.record_events = record_events
        self._site_resolver = site_resolver
        self._blocks: list[TimelineBlock] = []
        self._call_sites: list[tuple[str, int] | None] = []
        self._block_sites: list[BlockSite | None] = []
        self._block_clips: list[tuple[ClipTrace, ...]] = []
        self._block_dynamic: list[bool] = []
        self._pending_clips: tuple[ClipTrace, ...] = ()
        self._lanes: dict[int, int] = {}
        self._lane_targets: list[Any] = []  # keeps traced mobjects alive: ids stay unique
        self._occurrences: dict[str, int] = {}
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
        begin = getattr(scene, "begin_animations", None)
        if begin is not None:
            self._saved["begin_animations"] = begin
            scene.begin_animations = self._wrap_begin(begin)

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
            self._pending_clips = ()
            call_site = _caller_site()
            site = self._resolve_site()
            self._stats.bump("play")
            dynamic = self._time_updaters_active()
            self._play_depth += 1
            try:
                return original(*args, **kwargs)
            finally:
                self._play_depth -= 1
                # manimgl warns and returns for play() without animations; do not
                # clutter the timeline with a zero-duration ghost block.
                if args:
                    self._record_block(index, label, start, call_site, site, dynamic)

        return wrapper

    def _wrap_begin(self, original: Callable[..., Any]) -> Callable[..., Any]:
        """Capture the animations of a ``play()`` once manimgl has resolved their run times."""

        def wrapper(animations: Any, *args: Any, **kwargs: Any) -> Any:
            animations = list(animations)
            self._pending_clips = tuple(self._clip(animation) for animation in animations)
            return original(animations, *args, **kwargs)

        return wrapper

    def _clip(self, animation: Any) -> ClipTrace:
        target = getattr(animation, "mobject", None)
        key = id(target)
        if key not in self._lanes:
            self._lanes[key] = len(self._lanes)
            self._lane_targets.append(target)
        try:
            duration = float(animation.get_run_time())
        except (AttributeError, TypeError, ValueError):
            duration = float(getattr(animation, "run_time", 0.0) or 0.0)
        return ClipTrace(
            animation=_animation_name(animation),
            target=_class_name(target),
            lane=self._lanes[key],
            start=0.0,
            duration=max(0.0, duration) if math.isfinite(duration) else 0.0,
        )

    def _wrap_wait(self, original: Callable[..., Any]) -> Callable[..., Any]:
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            start = self._time()
            index = self._num_plays()
            call_site = _caller_site()
            site = self._resolve_site()
            self._stats.bump("wait")
            dynamic = self._time_updaters_active()
            try:
                return original(*args, **kwargs)
            finally:
                self._record_block(index, "Wait", start, call_site, site, dynamic)

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

    def _record_block(
        self,
        index: int,
        label: str,
        start: float,
        call_site: tuple[str, int] | None = None,
        site: BlockSite | None = None,
        dynamic_before: bool = False,
    ) -> None:
        dynamic = dynamic_before or self._time_updaters_active()
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
            if self._call_sites:
                self._call_sites[-1] = self._call_sites[-1] or call_site
            if self._block_dynamic:
                self._block_dynamic[-1] = self._block_dynamic[-1] or dynamic
            return
        self._blocks.append(TimelineBlock(index=index, name=label, start=start, duration=duration))
        self._call_sites.append(call_site)
        self._block_sites.append(site)
        self._block_clips.append(self._pending_clips)
        self._block_dynamic.append(dynamic)
        self._pending_clips = ()

    def _time_updaters_active(self) -> bool:
        """True when any mobject in the scene has an updater that takes ``dt``.

        Mirrors manimgl's own rule (``"dt" in updater.__code__.co_varnames``).  Anything
        that cannot be inspected counts as time-based, so callers stay conservative.
        """
        scene = self._scene
        if scene is None:
            return True
        try:
            members_of = getattr(scene, "get_mobject_family_members", None)
            members = members_of() if members_of is not None else scene.mobjects
            for mobject in members:
                for updater in getattr(mobject, "updaters", ()):
                    code = getattr(updater, "__code__", None)
                    if code is None or "dt" in code.co_varnames:
                        return True
        except Exception:  # an exotic scene must never break tracing
            return True
        return False

    def _resolve_site(self) -> BlockSite | None:
        """Fingerprint + occurrence for the user call two frames above the wrapper."""
        if self._site_resolver is None:
            return None
        location = _caller_location(depth=3)
        if location is None:
            return None
        site_id = self._site_resolver(*location)
        if site_id is None:
            return None
        occurrence = self._occurrences.get(site_id, 0)
        self._occurrences[site_id] = occurrence + 1
        return BlockSite(site_id, occurrence)

    # -- results -----------------------------------------------------------

    def snapshot(self) -> TraceSnapshot:
        """Immutable view of everything recorded so far."""
        return TraceSnapshot(
            blocks=tuple(self._blocks),
            events=tuple(self._events),
            stats=self._stats.to_dict(),
            total_duration=self._time(),
            call_sites=tuple(self._call_sites),
            block_sites=tuple(self._block_sites),
            block_clips=tuple(self._block_clips),
            block_dynamic=tuple(self._block_dynamic),
        )

    def reset(self) -> None:
        """Clear recordings without detaching (reuse across runs)."""
        self._blocks.clear()
        self._call_sites.clear()
        self._block_sites.clear()
        self._block_clips.clear()
        self._block_dynamic.clear()
        self._pending_clips = ()
        self._lanes.clear()
        self._lane_targets.clear()
        self._occurrences.clear()
        self._events.clear()
        self._stats = TraceStats()
        self._play_depth = 0

    def __enter__(self) -> TraceRecorder:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.detach()


def _caller_site() -> tuple[str, int] | None:
    """Source ``(filename, line)`` of the code that called the wrapped method.

    Called from inside a tracer wrapper, so the user frame is two levels up
    (``_caller_site`` -> ``wrapper`` -> user code). Returns ``None`` when the
    interpreter cannot produce a frame (e.g. calls from C).
    """
    try:
        frame = sys._getframe(2)
    except ValueError:  # pragma: no cover - depends on the call stack
        return None
    return (frame.f_code.co_filename, frame.f_lineno)


def _caller_location(depth: int) -> tuple[str, int, int] | None:
    """``(filename, line, UTF-8 byte column)`` of the *call expression* being executed.

    ``depth`` counts frames above this function.  The column comes from the caller's
    current instruction (``co_positions``), which for a call spans the whole
    ``self.play(...)`` expression, so it matches the AST node's start exactly even
    when several calls share a line.  Falls back to ``None`` without position data.
    """
    try:
        frame = sys._getframe(depth)
    except ValueError:  # pragma: no cover - depends on the call stack
        return None
    entry = next(islice(frame.f_code.co_positions(), frame.f_lasti // 2, None), None)
    if entry is None:
        return None
    line, _end_line, col, _end_col = entry
    if line is None or col is None:
        return None
    return (frame.f_code.co_filename, line, col)


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
