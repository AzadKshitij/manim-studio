"""Headless scene execution: static discovery, syntax checking and runtime tracing.

Everything here is designed to work without a window: manimgl's ``Camera`` falls back
to a standalone OpenGL context when ``window=None``, so traces, stills and validation
run in a plain process.  All ``manimlib`` imports are function-local so that the CLI
starts fast and so ``sys.argv`` can be sanitized first (manimgl parses it at import).
"""

from __future__ import annotations

import ast
import functools
import importlib.machinery
import importlib.util
import os
import sys
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from manim_studio.scene_settings import SceneSettings
from manim_studio.tracer import TraceRecorder, TraceSnapshot

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
    "load_patched_module",
    "load_scene_class",
    "load_scene_module",
    "render_still",
    "run_trace",
    "select_scene_class",
    "user_module_signature",
]

DEFAULT_TRACE_RESOLUTION: tuple[int, int] = (854, 480)

_SCENE_BASE_NAMES = {"Scene", "InteractiveScene", "ThreeDScene", "MovingCameraScene"}


class SceneLoadError(Exception):
    """Raised when a scene file cannot be loaded, parsed or resolved.

    ``line`` is the line of the scene file the failure points at, when a check found one.
    """

    def __init__(self, message: str, *, line: int | None = None) -> None:
        super().__init__(message)
        self.line = line


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


def _same_file(name: str, target: Path) -> bool:
    try:
        return Path(name).resolve() == target
    except OSError:
        return False


def _line_in_one(error: BaseException, target: Path) -> int | None:
    """The line in ``target`` where this one exception was raised, if it was raised there."""
    import traceback as _traceback

    if isinstance(error, SceneLoadError) and error.line is not None:
        return error.line  # a failed check already located it
    if (
        isinstance(error, SyntaxError)
        and error.lineno is not None
        and error.filename
        and _same_file(error.filename, target)
    ):
        # compile() reports a syntax error without any traceback frame: its lineno is the location.
        return error.lineno
    line: int | None = None
    for frame in _traceback.extract_tb(error.__traceback__):
        if _same_file(frame.filename, target):
            line = frame.lineno
    return line


def _location_in_scene(exc: BaseException, target: Path) -> int | None:
    """The line in ``target`` that caused ``exc``, looking through wrapped causes.

    Scene loading wraps the real failure: a SyntaxError or NameError raised while importing
    the scene arrives inside a SceneLoadError.  The search starts at the root cause, so the
    innermost line in the scene is the one reported.
    """
    chain: list[BaseException] = []
    current: BaseException | None = exc
    while current is not None and not any(current is seen for seen in chain):
        chain.append(current)
        current = current.__cause__ or current.__context__
    for error in reversed(chain):
        line = _line_in_one(error, target)
        if line is not None:
            return line
    return None


def format_scene_error(exc: BaseException, scene_path: str | Path | None = None) -> SceneError:
    """Turn an exception into a compact editor-friendly payload.

    Finds the innermost traceback frame inside the scene file so the editor can put
    an error marker on the right line.  LaTeX failures get an actionable hint
    (Tools > LaTeX setup) instead of manimgl's bare "compilation failed".
    """
    import traceback as _traceback

    from manim_studio.latex import latex_hint_for

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
        line = _location_in_scene(exc, target)
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


class _PatchedSourceLoader(importlib.machinery.SourceFileLoader):
    """Executes in-memory (timeline-patched) source as if it were the file on disk.

    ``co_filename`` stays the real scene path, so tracebacks, call sites and
    breakpoints keep pointing at the file the user has open.  The bytecode cache is
    bypassed on purpose: the text differs from what is on disk.
    """

    def __init__(self, fullname: str, path: str, source: str) -> None:
        super().__init__(fullname, path)
        self._patched_source = source

    def get_code(self, fullname: str) -> Any:
        # dont_inherit: this module uses ``from __future__ import annotations``,
        # which must not leak into user scenes.
        return compile(self._patched_source, self.path, "exec", dont_inherit=True)


def _reset_runtime_on_construct(module: Any, runtime: Any) -> None:
    """Make every scene in ``module`` restart the runtime's loop counters per run.

    ``construct`` is replayed many times (trace, scrub, crawler, playback); the
    per-iteration duration helper numbers iterations from the start of each run.
    """
    for value in list(vars(module).values()):
        if not isinstance(value, type) or value.__module__ != module.__name__:
            continue
        original = value.__dict__.get("construct")
        if original is None:
            continue

        def construct(self: Any, *args: Any, _original: Any = original, **kwargs: Any) -> Any:
            runtime.reset()
            return _original(self, *args, **kwargs)

        functools.update_wrapper(construct, original)
        value.construct = construct  # type: ignore[attr-defined]


def user_module_signature(scene_path: Path) -> tuple[tuple[str, int, int], ...]:
    """Fingerprint of the user modules a loaded scene may depend on.

    ``(path, mtime_ns, size)`` of every imported module that lives next to (or below)
    the scene file, excluding the scene itself and any virtualenv/site-packages.  It is
    how a reload notices an edited helper module that the scene file's own text cannot
    reveal.  Call it *after* the scene module was imported.
    """
    try:
        scene_file = scene_path.resolve()
    except OSError:  # pragma: no cover - defensive
        return ()
    root = scene_file.parent
    # Thousands of modules are loaded; reject almost all of them with a plain string
    # prefix test before touching the filesystem (``Path.resolve`` is slow on Windows).
    prefix = os.path.normcase(str(root))
    found: list[tuple[str, int, int]] = []
    for module in list(sys.modules.values()):
        filename = module.__dict__.get("__file__") if hasattr(module, "__dict__") else None
        if not isinstance(filename, str) or not filename.endswith(".py"):
            continue
        if not os.path.normcase(filename).startswith(prefix):
            continue
        try:
            path = Path(filename).resolve()
            if path == scene_file or not path.is_relative_to(root):
                continue
            if {"site-packages", ".venv", "venv", "__pycache__"} & set(path.parts):
                continue
            stat = path.stat()
        except (OSError, ValueError):
            continue
        found.append((str(path), stat.st_mtime_ns, stat.st_size))
    return tuple(sorted(found))


def load_scene_module(
    path: str | Path,
    *,
    is_reload: bool = False,
    source: str | None = None,
    runtime: Any = None,
) -> Any:
    """Import a scene module through manimgl's loader.

    With ``is_reload=True`` manimgl performs a deep reload of user-defined modules
    the scene imported, which is what makes editor reloads pick up helper-file edits.

    ``source`` runs that text instead of the file's contents (still reporting
    ``path`` as the file), which is how timeline patches take effect without touching
    the ``.py``.  ``runtime`` is exposed to the module as ``__studio__`` for
    per-iteration edits.
    """
    resolved = Path(path).resolve()
    with manimlib_argv_guard():
        from manimlib.module_loader import ModuleLoader

        from manim_studio.latex_compat import patch_manimgl_latex_compat
        from manim_studio.patches import RUNTIME_NAME

        patch_manimgl_latex_compat()
        try:
            if source is None:
                module = ModuleLoader.get_module(str(resolved), is_during_reload=is_reload)
            else:
                module = load_patched_module(
                    ModuleLoader, resolved, source, runtime, RUNTIME_NAME, is_reload
                )
        except SceneLoadError:
            raise
        except Exception as exc:  # surface import failures with context
            raise SceneLoadError(f"{type(exc).__name__} while importing {resolved}: {exc}") from exc
    if module is None:
        raise SceneLoadError(f"cannot import {resolved}")
    return module


def load_patched_module(
    loader_cls: Any,
    resolved: Path,
    source: str,
    runtime: Any,
    runtime_name: str,
    is_reload: bool,
) -> Any:
    """Mirror ``ModuleLoader.get_module`` but execute ``source`` instead of the file."""
    file_name = str(resolved)
    module_name = file_name.replace(os.sep, ".").replace(".py", "")
    loader = _PatchedSourceLoader(module_name, file_name, source)
    spec = importlib.util.spec_from_file_location(module_name, file_name, loader=loader)
    if spec is None:
        raise SceneLoadError(f"cannot import {resolved}")
    module = importlib.util.module_from_spec(spec)
    if runtime is not None:
        setattr(module, runtime_name, runtime)
    if is_reload:
        imported = loader_cls._exec_module_and_track_imports(spec, module)
        loader_cls._reload_modules(imported, set())
    loader.exec_module(module)
    if runtime is not None:
        _reset_runtime_on_construct(module, runtime)
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


def fit_camera_to_pixel_aspect(camera: Any) -> None:
    """Correct manimgl's horizontal frame scale for this camera's pixel aspect ratio.

    manimgl 1.7.2 builds ``frame_rescale_factors`` from the module-level 16:9 frame width,
    so any other resolution is stretched sideways.  The correction is the same one the
    managed scene block installs (``scene_settings._PIXEL_FIX``); for 16:9 it changes
    nothing.
    """
    from manimlib.constants import FRAME_HEIGHT

    draw_uniforms = camera.refresh_uniforms

    def refresh_uniforms() -> None:
        draw_uniforms()
        factors = list(camera.uniforms["frame_rescale_factors"])
        factors[0] = 2.0 / (FRAME_HEIGHT * camera.get_aspect_ratio())
        camera.uniforms["frame_rescale_factors"] = tuple(factors)

    camera.refresh_uniforms = refresh_uniforms


def make_scene(
    scene_class: type,
    *,
    window: Any = None,
    resolution: tuple[int, int] = DEFAULT_TRACE_RESOLUTION,
    fps: int | None = None,
    settings: SceneSettings | None = None,
    skip_animations: bool = True,
    start_at_animation_number: int | None = None,
    end_at_animation_number: int | None = None,
    preview_while_skipping: bool = False,
) -> Any:
    """Instantiate a scene for headless execution with writing disabled.

    ``settings``, when given, win over ``resolution`` and ``fps`` and add the background.
    """
    camera_config: dict[str, Any] = {"resolution": resolution}
    if fps is not None:
        camera_config["fps"] = fps
    if settings is not None:
        camera_config.update(settings.camera_config())
    file_writer_config = {
        "write_to_movie": False,
        "save_last_frame": False,
        "quiet": True,
    }
    scene = scene_class(
        window=window,
        camera_config=camera_config,
        file_writer_config=file_writer_config,
        skip_animations=skip_animations,
        start_at_animation_number=start_at_animation_number,
        end_at_animation_number=end_at_animation_number,
        preview_while_skipping=preview_while_skipping,
    )
    if settings is not None and settings.needs_pixel_fix:
        fit_camera_to_pixel_aspect(scene.camera)
    return scene


def run_construct(scene: Any) -> None:
    """Run ``setup()`` + ``construct()`` without tearing down or entering interact.

    ``EndScene`` (raised by manimgl for ``end_at_animation_number``) is treated as a
    normal early exit.
    """
    from manimlib.scene.scene import EndScene

    from manim_studio.latex_compat import patch_manimgl_latex_compat

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
    from manim_studio.latex import apply_runtime_path

    apply_runtime_path()
    check = check_scene_file(path)
    if not check.ok:
        raise SceneLoadError(check.error or "scene check failed", line=check.error_line)

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

    from manim_studio.latex import apply_runtime_path

    apply_runtime_path()
    with manimlib_argv_guard():
        scene_class = load_scene_class(path, scene_name)
        scene = make_scene(scene_class, resolution=resolution, skip_animations=True)
        run_construct(scene)
        # get_image() reads the framebuffer as it is. Nothing has drawn the final state yet in
        # skip mode, so without this the PNG is fully transparent (see docs/adr/0013).
        scene.update_frame(dt=0, force_draw=True)
        image = scene.get_image()
        destination = (
            Path(output)
            if output
            else Path(tempfile.mkdtemp(prefix="manim-studio-"))
            / (f"{scene.__class__.__name__}.png")
        )
        destination.parent.mkdir(parents=True, exist_ok=True)
        image.save(destination)
    return destination
