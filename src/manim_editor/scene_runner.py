"""Headless scene execution: static discovery, syntax checking and runtime tracing.

Everything here is designed to work without a window: manimgl's ``Camera`` falls back
to a standalone OpenGL context when ``window=None``, so traces, stills and validation
run in a plain process.  All ``manimlib`` imports are function-local so that the CLI
starts fast and so ``sys.argv`` can be sanitized first (manimgl parses it at import).
"""

from __future__ import annotations

import ast
import sys
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from manim_editor.tracer import TraceRecorder, TraceSnapshot

if TYPE_CHECKING:
    from collections.abc import Iterator

__all__ = [
    "DEFAULT_TRACE_RESOLUTION",
    "SceneCheckResult",
    "SceneError",
    "SceneInfo",
    "SceneLoadError",
    "TraceResult",
    "check_scene_file",
    "discover_scenes",
    "format_scene_error",
    "load_scene_class",
    "load_scene_module",
    "render_still",
    "run_trace",
    "select_scene_class",
]

DEFAULT_TRACE_RESOLUTION: tuple[int, int] = (854, 480)

_SCENE_BASE_NAMES = {"Scene", "InteractiveScene", "ThreeDScene", "MovingCameraScene"}


class SceneLoadError(Exception):
    """Raised when a scene file cannot be loaded, parsed or resolved."""


@dataclass(frozen=True, slots=True)
class SceneError:
    """A user-scene failure prepared for the editor UI."""

    message: str
    traceback_text: str
    line: int | None = None
    scene_file: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "message": self.message,
            "traceback": self.traceback_text,
            "line": self.line,
            "scene_file": self.scene_file,
        }


def format_scene_error(exc: BaseException, scene_path: str | Path | None = None) -> SceneError:
    """Turn an exception into a compact editor-friendly payload.

    Finds the innermost traceback frame inside the scene file so the editor can put
    an error marker on the right line.  LaTeX failures get an actionable hint
    (Tools > LaTeX setup) instead of manimgl's bare "compilation failed".
    """
    import traceback as _traceback

    from manim_editor.latex import latex_hint_for

    text = "".join(_traceback.format_exception(type(exc), exc, exc.__traceback__))
    hint = latex_hint_for(exc)
    message = f"{type(exc).__name__}: {exc}"
    if hint is not None:
        message = f"{message}\n{hint}"
    line: int | None = None
    scene_file: str | None = None
    if scene_path is not None:
        target = Path(scene_path).resolve()
        scene_file = str(target)
        frames = _traceback.extract_tb(exc.__traceback__)
        for frame in frames:
            try:
                if Path(frame.filename).resolve() == target:
                    line = frame.lineno
            except OSError:
                continue
    return SceneError(
        message=message,
        traceback_text=text,
        line=line,
        scene_file=scene_file,
    )


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
    warnings: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "ok": self.ok,
            "scenes": [scene.to_dict() for scene in self.scenes],
            "error": self.error,
            "error_line": self.error_line,
            "warnings": list(self.warnings),
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
    warnings = _scan_color_traps(source) if ok else ()
    return SceneCheckResult(path=str(path), ok=ok, scenes=scenes, error=error, warnings=warnings)


#: manimgl classes whose ``__init__`` forces ``stroke_color=RED``, making the
#: documented ``color=`` argument affect only the (usually invisible) fill.
_COLOR_TRAP_CLASSES = {"Circle", "Dot", "Ellipse"}


def _scan_color_traps(source: str) -> tuple[str, ...]:
    """Warn about ``Circle(color=...)``-style calls that manimgl renders red.

    This is a well-known sharp edge in manimgl's geometry classes: ``Circle``,
    ``Dot`` and ``Ellipse`` default ``stroke_color`` to red and pass it explicitly,
    so ``color=`` never reaches the visible stroke.  The warning keeps the
    agent-facing ``check`` command honest without failing otherwise-valid scenes.
    """
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return ()
    warnings: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        function = node.func
        if isinstance(function, ast.Name):
            name = function.id
        elif isinstance(function, ast.Attribute):
            name = function.attr
        else:
            continue
        if name not in _COLOR_TRAP_CLASSES:
            continue
        keywords = {keyword.arg for keyword in node.keywords if keyword.arg}
        if "color" in keywords and not ({"stroke_color", "fill_color"} & keywords):
            warnings.append(
                f"line {node.lineno}: {name}(color=...) only sets the fill in manimgl; "
                f"the visible stroke keeps its default red. Pass stroke_color=... "
                f"(or fill_color=... for filled shapes)."
            )
    return tuple(warnings)


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


def load_scene_module(path: str | Path, *, is_reload: bool = False) -> Any:
    """Import a scene module through manimgl's loader.

    With ``is_reload=True`` manimgl performs a deep reload of user-defined modules
    the scene imported, which is what makes editor reloads pick up helper-file edits.
    """
    resolved = Path(path).resolve()
    with manimlib_argv_guard():
        from manimlib.module_loader import ModuleLoader

        from manim_editor.latex_compat import patch_manimgl_latex_compat

        patch_manimgl_latex_compat()
        try:
            module = ModuleLoader.get_module(str(resolved), is_during_reload=is_reload)
        except SceneLoadError:
            raise
        except Exception as exc:  # surface import failures with context
            raise SceneLoadError(f"{type(exc).__name__} while importing {resolved}: {exc}") from exc
    if module is None:
        raise SceneLoadError(f"cannot import {resolved}")
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


def select_scene_class(
    module: Any, scene_name: str | None = None, *, path: str | Path | None = None
) -> type:
    """Pick a scene class from a loaded module, with helpful errors."""
    available = list_module_scenes(module)
    location = str(path) if path is not None else str(getattr(module, "__name__", "module"))
    if not available:
        raise SceneLoadError(f"no Scene class found in {location}")
    if scene_name is None:
        if len(available) > 1:
            raise SceneLoadError(
                f"{location} defines multiple scenes {sorted(available)}; pass --scene NAME"
            )
        scene_name = available[0]
    if scene_name not in available:
        raise SceneLoadError(
            f"scene {scene_name!r} not found in {location}; available: {sorted(available)}"
        )
    return getattr(module, scene_name)


def load_scene_class(path: str | Path, scene_name: str | None = None) -> type:
    """Import a scene file and return the requested scene class."""
    module = load_scene_module(path)
    return select_scene_class(module, scene_name, path=path)


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

    from manim_editor.latex_compat import patch_manimgl_latex_compat

    patch_manimgl_latex_compat()
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
    from manim_editor.latex import apply_runtime_path

    apply_runtime_path()
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

    from manim_editor.latex import apply_runtime_path

    apply_runtime_path()
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
