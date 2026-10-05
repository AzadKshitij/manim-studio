"""Test doubles shared across the suite (no pytest fixtures here)."""

from __future__ import annotations

from typing import Any


class FakeMobject:
    """Minimal stand-in for ``manimlib.mobject.Mobject``."""

    def __init__(self, name: str = "FakeMobject") -> None:
        self.name = name

    def __str__(self) -> str:
        return self.name


class FakeAnimation:
    """Duck-types a manimlib animation for tracer tests.

    Use :func:`make_animation` to build instances whose class name matches a real
    manimlib animation (``ShowCreation``, ``Transform``, ...).
    """

    def __init__(self, run_time: float = 1.0, mobject: FakeMobject | None = None) -> None:
        self.run_time = run_time
        self.mobject = mobject if mobject is not None else FakeMobject()

    def interpolate(self, alpha: float) -> None:  # pragma: no cover - duck type
        pass


def make_animation(
    name: str, run_time: float = 1.0, mobject: FakeMobject | None = None
) -> FakeAnimation:
    """Create a FakeAnimation with a specific class name."""
    cls = type(name, (FakeAnimation,), {})
    return cls(run_time=run_time, mobject=mobject)


class FakeBuilder:
    """Duck-types ``mobject.animate`` (``_AnimationBuilder``)."""

    def __init__(self, mobject: FakeMobject | None = None) -> None:
        self.mobject = mobject if mobject is not None else FakeMobject()

    def build(self) -> FakeAnimation:  # pragma: no cover - duck type
        return make_animation("_MethodAnimation", run_time=1.0, mobject=self.mobject)


class FakeScene:
    """Deterministic scene double: advances ``time`` and ``num_plays`` like manimgl."""

    def __init__(self, *, internal_adds: bool = False) -> None:
        self.time = 0.0
        self.num_plays = 0
        self.calls: list[tuple[str, Any]] = []
        self.internal_adds = internal_adds
        self.mobjects: list[FakeMobject] = []

    def play(self, *animations: Any, run_time: float | None = None, **kwargs: Any) -> str:
        if not animations:
            return "noop"
        if self.internal_adds:
            self.add(animations[0].mobject)
        duration = run_time if run_time is not None else max(a.run_time for a in animations)
        self.time += duration
        self.num_plays += 1
        return "played"

    def wait(self, duration: float | None = 1.0, **kwargs: Any) -> str:
        self.time += 1.0 if duration is None else duration
        self.num_plays += 1
        return "waited"

    def add(self, *mobjects: FakeMobject) -> FakeScene:
        self.calls.append(("add", mobjects))
        self.mobjects.extend(mobjects)
        return self

    def remove(self, *mobjects: FakeMobject) -> FakeScene:
        self.calls.append(("remove", mobjects))
        return self

    def clear(self) -> FakeScene:
        self.calls.append(("clear", ()))
        return self

    def save_state(self) -> None:
        self.calls.append(("save_state", ()))

    def undo(self) -> None:
        self.calls.append(("undo", ()))

    def redo(self) -> None:
        self.calls.append(("redo", ()))
