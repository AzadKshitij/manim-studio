"""Headless scene execution: static discovery, syntax checking and runtime tracing.

Everything here is designed to work without a window: manimgl's ``Camera`` falls back
to a standalone OpenGL context when ``window=None``, so traces, stills and validation
run in a plain process.  All ``manimlib`` imports are function-local so that the CLI
starts fast and so ``sys.argv`` can be sanitized first (manimgl parses it at import).
"""

from __future__ import annotations

import ast
import importlib.util
import sys
import time
from contextlib import contextmanager
from dataclasses import dataclass
from hashlib import md5
from pathlib import Path
from typing import TYPE_CHECKING, Any

from manim_editor.tracer import TraceRecorder, TraceSnapshot

if TYPE_CHECKING:
    from collections.abc import Iterator

__all__ = [
    "DEFAULT_TRACE_RESOLUTION",
    "SceneCheckResult",
    "SceneInfo",
    "SceneLoadError",
    "TraceResult",
    "check_scene_file",
    "discover_scenes",
    "load_scene_class",
    "render_still",
    "run_trace",
]

DEFAULT_TRACE_RESOLUTION: tuple[int, int] = (854, 480)

_SCENE_BASE_NAMES = {"Scene", "InteractiveScene", "ThreeDScene", "MovingCameraScene"}


class SceneLoadError(Exception):
    """Raised when a scene file cannot be loaded, parsed or resolved."""


@dataclass(frozen=True, slots=True)
class SceneInfo:
    """Static information about a scene class found in a file."""

    name: str
    lineno: int
    bases: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "lineno": self.lineno, "bases": list(self.bases)}


@dataclass(frozen=True, slots=True)
class SceneCheckResult:
    """Outcome of :func:`check_scene_file` — always returned, never raised."""

    path: str
    ok: bool
    scenes: tuple[SceneInfo, ...]
    error: str | None = None
    error_line: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "ok": self.ok,
            "scenes": [scene.to_dict() for scene in self.scenes],
            "error": self.error,
            "error_line": self.error_line,
        }


@dataclass(frozen=True, slots=True)
class TraceResult:
    """A completed runtime trace of one scene."""

    scene_name: str
    module_path: str
    snapshot: TraceSnapshot
    wall_seconds: float
    resolution: tuple[int, int]
    fps: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "scene": self.scene_name,
            "module": self.module_path,
            "resolution": list(self.resolution),
            "fps": self.fps,
            "wall_seconds": self.wall_seconds,
            **self.snapshot.to_dict(),
        }


# ---------------------------------------------------------------------------
# Static analysis (no manimlib import)
# ---------------------------------------------------------------------------


def _base_name(node: ast.expr) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return ""


def is_scene_base(name: str) -> bool:
    """Heuristic: a class is a scene if it derives from a ``*Scene`` base."""
    return name in _SCENE_BASE_NAMES or name.endswith("Scene")


def discover_scenes(path: str | Path) -> tuple[SceneInfo, ...]:
    """AST-scan a file for scene classes without importing it.

    Mirrors manimgl's rule: any class deriving from ``Scene`` (or a subclass whose
    name ends with ``Scene``) is a scene.  Order follows source order.
    """
    source = _read_source(path)
    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError as exc:
        raise SceneLoadError(_syntax_error_message(exc)) from exc

    scenes: list[SceneInfo] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue
        bases = tuple(filter(None, (_base_name(base) for base in node.bases)))
        if any(is_scene_base(base) for base in bases):
            scenes.append(SceneInfo(name=node.name, lineno=node.lineno, bases=bases))
    scenes.sort(key=lambda info: info.lineno)
    return tuple(scenes)


def check_scene_file(path: str | Path) -> SceneCheckResult:
    """Compile the file and list its scenes; report errors instead of raising."""
    try:
        source = _read_source(path)
    except OSError as exc:
        return SceneCheckResult(path=str(path), ok=False, scenes=(), error=str(exc))
    try:
        compile(source, str(path), "exec")
    except SyntaxError as exc:
        return SceneCheckResult(
            path=str(path),
            ok=False,
            scenes=(),
            error=f"{exc.msg} (line {exc.lineno}, column {exc.offset})",
            error_line=exc.lineno,
        )
    try:
        scenes = discover_scenes(path)
    except SceneLoadError as exc:
        return SceneCheckResult(path=str(path), ok=False, scenes=(), error=str(exc))
    ok = bool(scenes)
    error = None if ok else "no Scene class found"
    return SceneCheckResult(path=str(path), ok=ok, scenes=scenes, error=error)


def _read_source(path: str | Path) -> str:
    return Path(path).read_text(encoding="utf-8")


def _syntax_error_message(exc: SyntaxError) -> str:
    return f"{exc.msg} (line {exc.lineno}, column {exc.offset})"


# ---------------------------------------------------------------------------
# Dynamic loading
# ---------------------------------------------------------------------------


@contextmanager
def manimlib_argv_guard() -> Iterator[None]:
    """Hide our CLI arguments from manimgl's import-time ``parse_cli()``."""
    previous = sys.argv[:]
    sys.argv[:] = ["manimgl"]
    try:
        yield
    finally:
        sys.argv[:] = previous


def _load_module(path: str | Path) -> Any:
    resolved = Path(path).resolve()
    digest = md5(str(resolved).encode("utf-8")).hexdigest()[:8]
    module_name = f"manim_editor_scene_{resolved.stem}_{digest}"
    spec = importlib.util.spec_from_file_location(module_name, resolved)
    if spec is None or spec.loader is None:
        raise SceneLoadError(f"cannot import {resolved}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
    except Exception as exc:  # surfaced as SceneLoadError with context
        sys.modules.pop(module_name, None)
        raise SceneLoadError(f"{type(exc).__name__} while importing {resolved}: {exc}") from exc
    return module


def list_module_scenes(module: Any) -> tuple[str, ...]:
    """Names of scene classes exported by a loaded module, honoring SCENES_IN_ORDER."""
    ordered = getattr(module, "SCENES_IN_ORDER", None)
    if ordered:
        return tuple(cls.__name__ for cls in ordered)
    from manimlib.scene.scene import Scene

    names: list[str] = []
    for attr_name in sorted(vars(module)):
        value = getattr(module, attr_name)
        if (
            isinstance(value, type)
            and issubclass(value, Scene)
            and value is not Scene
            and getattr(value, "__module__", None) == module.__name__
        ):
            names.append(value.__name__)
    return tuple(names)


def load_scene_class(path: str | Path, scene_name: str | None = None) -> type:
    """Import a scene file and return the requested scene class.

    With one scene in the file, ``scene_name=None`` selects it; with several, the
    name is required and a :class:`SceneLoadError` lists the candidates.
    """
    module = _load_module(path)
    available = list_module_scenes(module)
    if not available:
        raise SceneLoadError(f"no Scene class found in {path}")
    if scene_name is None:
        if len(available) > 1:
            raise SceneLoadError(
                f"{path} defines multiple scenes {sorted(available)}; pass --scene NAME"
            )
        scene_name = available[0]
    if scene_name not in available:
        raise SceneLoadError(
            f"scene {scene_name!r} not found in {path}; available: {sorted(available)}"
        )
    return getattr(module, scene_name)


# ---------------------------------------------------------------------------
# Execution
# ---------------------------------------------------------------------------


def make_scene(
    scene_class: type,
    *,
    window: Any = None,
    resolution: tuple[int, int] = DEFAULT_TRACE_RESOLUTION,
    fps: int | None = None,
    skip_animations: bool = True,
    start_at_animation_number: int | None = None,
    end_at_animation_number: int | None = None,
    preview_while_skipping: bool = False,
) -> Any:
    """Instantiate a scene for headless execution with writing disabled."""
    camera_config: dict[str, Any] = {"resolution": resolution}
    if fps is not None:
        camera_config["fps"] = fps
    file_writer_config = {
        "write_to_movie": False,
        "save_last_frame": False,
        "quiet": True,
    }
    return scene_class(
        window=window,
        camera_config=camera_config,
        file_writer_config=file_writer_config,
        skip_animations=skip_animations,
        start_at_animation_number=start_at_animation_number,
        end_at_animation_number=end_at_animation_number,
        preview_while_skipping=preview_while_skipping,
    )


def run_construct(scene: Any) -> None:
    """Run ``setup()`` + ``construct()`` without tearing down or entering interact.

    ``EndScene`` (raised by manimgl for ``end_at_animation_number``) is treated as a
    normal early exit.
    """
    from manimlib.scene.scene import EndScene

    scene.virtual_animation_start_time = 0.0
    scene.real_animation_start_time = time.time()
    try:
        scene.setup()
        scene.construct()
    except EndScene:
        pass


def run_trace(
    path: str | Path,
    scene_name: str | None = None,
    *,
    resolution: tuple[int, int] = DEFAULT_TRACE_RESOLUTION,
    fps: int | None = None,
    record_events: bool = True,
) -> TraceResult:
    """Run a scene headlessly with skip-animations and return its runtime trace.

    Durations are the *requested* run times (skip mode), which is within one frame
    of the frame-quantized render durations and orders of magnitude faster to obtain.
    """
    check = check_scene_file(path)
    if not check.ok:
        raise SceneLoadError(check.error or "scene check failed")

    started = time.perf_counter()
    with manimlib_argv_guard():
        scene_class = load_scene_class(path, scene_name)
        scene = make_scene(scene_class, resolution=resolution, fps=fps, skip_animations=True)
        recorder = TraceRecorder(record_events=record_events)
        recorder.attach(scene)
        try:
            run_construct(scene)
            snapshot = recorder.snapshot()
        finally:
            recorder.detach()
    return TraceResult(
        scene_name=type(scene).__name__,
        module_path=str(Path(path).resolve()),
        snapshot=snapshot,
        wall_seconds=time.perf_counter() - started,
        resolution=resolution,
        fps=fps or int(scene.camera.fps),
    )


def render_still(
    path: str | Path,
    scene_name: str | None = None,
    *,
    output: str | Path | None = None,
    resolution: tuple[int, int] = DEFAULT_TRACE_RESOLUTION,
) -> Path:
    """Render the final frame of a scene to a PNG (fast skip-run, no writing).

    Returns the path of the written PNG.
    """
    import tempfile

    with manimlib_argv_guard():
        scene_class = load_scene_class(path, scene_name)
        scene = make_scene(scene_class, resolution=resolution, skip_animations=True)
        run_construct(scene)
        image = scene.get_image()
        destination = (
            Path(output)
            if output
            else Path(tempfile.mkdtemp(prefix="manim-editor-"))
            / (f"{scene.__class__.__name__}.png")
        )
        destination.parent.mkdir(parents=True, exist_ok=True)
        image.save(destination)
    return destination
