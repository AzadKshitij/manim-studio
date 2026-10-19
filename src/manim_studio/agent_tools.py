"""Tool logic for AI agents, with no MCP imports so it is unit-testable (ADR 0013).

Every path an agent gives is resolved inside a workspace root. GL work (``trace``, ``frame``,
``contact_sheet``) runs in a child process (``manim-studio <cmd> --json``) so a crashing scene or a
long render cannot take this process down, and manimgl never loads here. Pure file work (``check``,
``write_scene``, ``new_scene``) and the export call run in-process.

Each function returns JSON-serialisable data, or an :class:`ImageResult` for the image tools. A
problem the agent should see is raised as :class:`ToolFailure`, whose message is passed on verbatim.
"""

from __future__ import annotations

import contextlib
import difflib
import json
import re
import subprocess
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from manim_studio import mobject_schema, schema_store, spawn
from manim_studio.api_index import build_index
from manim_studio.export import ExportError, build_export_plan, run_export
from manim_studio.ffmpeg_install import ffmpeg_status
from manim_studio.latex import latex_status
from manim_studio.scene_runner import check_scene_file
from manim_studio.templates import (
    SCENE_TEMPLATES,
    SNIPPET_TEMPLATES,
    TemplateError,
    render_scene_template,
)

__all__ = [
    "CliRun",
    "ImageResult",
    "ToolFailure",
    "api_index",
    "check",
    "contact_sheet",
    "describe_mobject",
    "export",
    "frame",
    "last_json",
    "list_templates",
    "new_scene",
    "prune_old_images",
    "resolve_in_root",
    "run_cli",
    "tail",
    "trace",
    "write_scene",
]

#: Longest source ``write_scene`` accepts, in characters.
MAX_SOURCE_CHARS = 500_000
#: Events beyond this many make a trace reply too large to be useful to an agent.
MAX_EVENTS = 200
#: Methods listed in ``describe_mobject``; the rest are counted, not listed.
MAX_METHODS = 80
#: Images kept in the temp folder before the oldest are removed.
KEEP_IMAGES = 100
_IMAGE_DIR_NAME = "manim-studio-agent"
_CHILD_TIMEOUT = 180.0
_CONTACT_SHEET_TIMEOUT = 300.0
_EXPORT_TIMEOUT = 1800.0
_CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


class ToolFailure(RuntimeError):
    """A failure the agent should see; its message is returned unchanged."""


@dataclass(frozen=True, slots=True)
class CliRun:
    """What a child ``manim-studio`` process printed and how it ended."""

    returncode: int
    stdout: str
    stderr: str


@dataclass(frozen=True, slots=True)
class ImageResult:
    """An image tool's reply: its JSON metadata, and the PNG both inline and on disk."""

    meta: dict[str, Any]
    png: bytes
    path: Path


def resolve_in_root(root: Path, file: str | Path) -> Path:
    """The absolute path of ``file``; it must sit inside ``root``, where relative paths start."""
    base = root.resolve()
    given = Path(file)
    resolved = (given if given.is_absolute() else base / given).resolve()
    if not resolved.is_relative_to(base):
        raise ToolFailure(f"path is outside the workspace root {base}: {file}")
    return resolved


def run_cli(args: list[str], *, timeout: float, cwd: Path | None = None) -> CliRun:
    """Run ``manim-studio <args>`` in a child process and capture its output."""
    executable, rest = spawn.cli_command(args)
    try:
        completed = subprocess.run(
            [executable, *rest],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            stdin=subprocess.DEVNULL,
            timeout=timeout,
            cwd=cwd,
            creationflags=_CREATE_NO_WINDOW,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise ToolFailure(f"timed out after {timeout:.0f}s") from exc
    return CliRun(completed.returncode, completed.stdout, completed.stderr)


_JSON_START = re.compile(r"^\{", re.MULTILINE)


def last_json(text: str) -> dict[str, Any]:
    """The last complete top-level JSON object printed in ``text``."""
    decoder = json.JSONDecoder()
    found: dict[str, Any] | None = None
    for match in _JSON_START.finditer(text):
        try:
            value, _ = decoder.raw_decode(text, match.start())
        except ValueError:
            continue
        if isinstance(value, dict):
            found = value
    if found is None:
        raise ToolFailure("the command printed no JSON")
    return found


def tail(text: str, limit: int = 4000) -> str:
    """The last ``limit`` characters of ``text``, stripped."""
    return text.strip()[-limit:]


def _image_dir() -> Path:
    directory = Path(tempfile.gettempdir()) / _IMAGE_DIR_NAME
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def prune_old_images(directory: Path, keep: int = KEEP_IMAGES) -> None:
    """Remove the oldest PNGs in ``directory`` beyond ``keep``."""
    images = sorted(directory.glob("*.png"), key=lambda p: p.stat().st_mtime, reverse=True)
    for stale in images[keep:]:
        with contextlib.suppress(OSError):
            stale.unlink()


def _new_image_path(stem: str, label: str) -> Path:
    directory = _image_dir()
    prune_old_images(directory)
    return directory / f"{stem}_{label}_{uuid.uuid4().hex[:8]}.png"


def _read_image(out: Path, meta: dict[str, Any]) -> ImageResult:
    if not out.is_file():
        raise ToolFailure(f"the command reported success but wrote no image to {out}")
    return ImageResult(meta=meta, png=out.read_bytes(), path=out)


def _size_args(width: int | None, height: int | None) -> list[str]:
    if width is None or height is None:
        return []
    return ["--resolution", str(width), str(height)]


def check(root: Path, file: str) -> dict[str, Any]:
    """Syntax and scene discovery for a scene file, plus the LaTeX status (no GL)."""
    path = resolve_in_root(root, file)
    payload = check_scene_file(path).to_dict()
    payload["latex"] = latex_status().to_dict()
    payload["ffmpeg"] = ffmpeg_status().to_dict()
    return payload


def trace(
    root: Path,
    file: str,
    scene: str | None = None,
    resolution: tuple[int, int] | None = None,
) -> dict[str, Any]:
    """Blocks, durations and stats from a headless run of the scene (its events are capped)."""
    path = resolve_in_root(root, file)
    args = ["trace", str(path), "--json"]
    if scene:
        args += ["--scene", scene]
    if resolution is not None:
        args += ["--resolution", str(resolution[0]), str(resolution[1])]
    run = run_cli(args, timeout=_CHILD_TIMEOUT, cwd=path.parent)
    if run.returncode != 0:
        raise ToolFailure(tail(run.stderr) or tail(run.stdout) or f"trace exited {run.returncode}")
    payload = last_json(run.stdout)
    events = payload.get("events")
    if isinstance(events, list) and len(events) > MAX_EVENTS:
        payload["events"] = {"omitted": len(events)}
    return payload


def frame(
    root: Path,
    file: str,
    at: float,
    scene: str | None = None,
    width: int | None = None,
    height: int | None = None,
    use_sidecar: bool = True,
) -> ImageResult:
    """One PNG of the scene at ``at`` seconds, with its metadata."""
    path = resolve_in_root(root, file)
    out = _new_image_path(path.stem, f"t{at:.2f}")
    args = ["frame", str(path), "--at", str(at), "-o", str(out), "--json"]
    if scene:
        args += ["--scene", scene]
    args += _size_args(width, height)
    if not use_sidecar:
        args.append("--no-sidecar")
    run = run_cli(args, timeout=_CHILD_TIMEOUT, cwd=path.parent)
    if run.returncode != 0:
        raise ToolFailure(tail(run.stderr) or tail(run.stdout) or f"frame exited {run.returncode}")
    return _read_image(out, last_json(run.stdout))


def contact_sheet(
    root: Path,
    file: str,
    every: float = 0.5,
    times: list[float] | None = None,
    start: float = 0.0,
    end: float | None = None,
    columns: int | None = None,
    max_frames: int = 48,
    scene: str | None = None,
    use_sidecar: bool = True,
) -> ImageResult:
    """One PNG tiling the scene's frames, each labelled with its time and block."""
    path = resolve_in_root(root, file)
    out = _new_image_path(path.stem, "sheet")
    args = ["contact-sheet", str(path), "-o", str(out), "--json"]
    if times is not None:
        args += ["--times", ",".join(str(t) for t in times)]
    else:
        args += ["--every", str(every)]
    if start:
        args += ["--from", str(start)]
    if end is not None:
        args += ["--to", str(end)]
    if columns is not None:
        args += ["--columns", str(columns)]
    args += ["--max-frames", str(max_frames)]
    if scene:
        args += ["--scene", scene]
    if not use_sidecar:
        args.append("--no-sidecar")
    run = run_cli(args, timeout=_CONTACT_SHEET_TIMEOUT, cwd=path.parent)
    if run.returncode != 0:
        raise ToolFailure(
            tail(run.stderr) or tail(run.stdout) or f"contact-sheet exited {run.returncode}"
        )
    return _read_image(out, last_json(run.stdout))


def export(
    root: Path,
    file: str,
    output: str | None = None,
    scene: str | None = None,
    quality: str = "low",
    fps: int | None = None,
    still: bool = False,
    gif: bool = False,
    resolution: tuple[int, int] | None = None,
    animation_range: tuple[int, int] | None = None,
    use_sidecar: bool = True,
) -> dict[str, Any]:
    """Render the scene to a video, still or GIF. Slow: callers run it off the event loop.

    Without ``output`` the file is written next to the scene. ``build_export_plan`` would
    otherwise use the server's working directory, which need not be inside the root.
    """
    path = resolve_in_root(root, file)
    default_suffix = ".png" if still else ".gif" if gif else ".mp4"
    target = resolve_in_root(root, output) if output else path.with_suffix(default_suffix)
    try:
        plan = build_export_plan(
            path,
            scene,
            output=target,
            quality=quality,
            fps=fps,
            still=still,
            gif=gif,
            resolution=resolution,
            animation_range=animation_range,
            use_sidecar=use_sidecar,
        )
    except ExportError as exc:
        raise ToolFailure(str(exc)) from exc
    result = run_export(plan, timeout=_EXPORT_TIMEOUT)
    return {
        "ok": result.ok,
        "returncode": result.returncode,
        "output": str(result.output_path),
        "log_tail": list(result.log_tail),
    }


def list_templates() -> dict[str, Any]:
    """The scene and snippet templates ``new_scene`` and the editor know about."""
    return {
        "scene": [
            {"key": t.key, "title": t.title, "description": t.description} for t in SCENE_TEMPLATES
        ],
        "snippet": [
            {"key": t.key, "title": t.title, "description": t.description, "code": t.code}
            for t in SNIPPET_TEMPLATES
        ],
    }


def api_index(query: str | None = None, limit: int = 200) -> dict[str, Any]:
    """Public manimlib names, filtered by a case-insensitive substring and capped at ``limit``."""
    symbols = build_index()
    needle = query.casefold() if query else None
    matched = [s for s in symbols if needle is None or needle in s.name.casefold()]
    shown = matched[: max(0, limit)]
    return {
        "total": len(symbols),
        "matched": len(matched),
        "returned": len(shown),
        "symbols": [symbol.to_dict() for symbol in shown],
    }


def _param_dict(param: mobject_schema.ParamSchema) -> dict[str, Any]:
    return {
        "name": param.name,
        "kind": param.kind,
        "annotation": param.annotation,
        "has_default": param.has_default,
        "default": param.default,
        "default_repr": param.default_repr,
        "owner": param.owner,
        "ui": mobject_schema.ui_kind(param),
    }


def _signature(params: tuple[mobject_schema.ParamSchema, ...]) -> str:
    parts: list[str] = []
    for param in params:
        text = param.name
        if param.kind == "var_positional":
            text = f"*{text}"
        elif param.kind == "var_keyword":
            text = f"**{text}"
        if param.has_default and param.default_repr is not None:
            text += f"={param.default_repr}"
        parts.append(text)
    return f"({', '.join(parts)})"


def describe_mobject(
    name: str, include_methods: bool = True, include_colours: bool = False
) -> dict[str, Any]:
    """Constructor parameters, chainable methods and (optionally) colour constants of a mobject."""
    schema = schema_store.load_schema()
    cls = mobject_schema.lookup(schema, name)
    if cls is None:
        close = difflib.get_close_matches(name, list(schema.exported), n=5)
        raise ToolFailure(f"unknown mobject class {name!r}; did you mean: {close}")
    payload: dict[str, Any] = {
        "name": cls.name,
        "key": cls.key,
        "module": cls.module,
        "lineage": list(cls.lineage),
        "manimlib_version": schema.manimlib_version,
        "constructor": [_param_dict(param) for param in mobject_schema.fields_for(schema, cls)],
    }
    if include_methods:
        methods = mobject_schema.methods_for(schema, cls)
        payload["methods"] = [
            {
                "name": method.name,
                "owner": method.owner,
                "signature": _signature(method.params),
                "doc": method.doc,
            }
            for method in methods[:MAX_METHODS]
        ]
        if len(methods) > MAX_METHODS:
            payload["methods_omitted"] = len(methods) - MAX_METHODS
    if include_colours:
        payload["colours"] = dict(schema.colours)
    return payload


def _scene_name_for(path: Path) -> str:
    name = "".join(part.capitalize() for part in path.stem.replace("-", "_").split("_"))
    return name if name.isidentifier() else "MyScene"


def _check_python_path(root: Path, file: str) -> Path:
    path = resolve_in_root(root, file)
    if path.suffix != ".py":
        raise ToolFailure(f"scene files must end in .py, got {file!r}")
    return path


def new_scene(
    root: Path,
    file: str,
    template: str = "circle_to_square",
    scene_name: str | None = None,
    overwrite: bool = False,
) -> dict[str, Any]:
    """Create a scene file from a template, then check it."""
    path = _check_python_path(root, file)
    if path.exists() and not overwrite:
        raise ToolFailure(f"{file} already exists; pass overwrite=true to replace it")
    name = scene_name or _scene_name_for(path)
    try:
        source = render_scene_template(template, name)
    except TemplateError as exc:
        raise ToolFailure(str(exc)) from exc
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(source, encoding="utf-8")
    return {"path": str(path), "scene_name": name, "check": check(root, str(path))}


def write_scene(root: Path, file: str, code: str, overwrite: bool = True) -> dict[str, Any]:
    """Write Python source for a scene, then check it so errors come back at once."""
    path = _check_python_path(root, file)
    if len(code) > MAX_SOURCE_CHARS:
        raise ToolFailure(f"code is {len(code)} characters; the limit is {MAX_SOURCE_CHARS}")
    if path.exists() and not overwrite:
        raise ToolFailure(f"{file} already exists; pass overwrite=true to replace it")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(code, encoding="utf-8")
    return {
        "path": str(path),
        "bytes": len(code.encode("utf-8")),
        "check": check(root, str(path)),
    }
