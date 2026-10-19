"""Video / still export through headless ``manimgl -w``.

The editor never renders exports through its live preview process: exporting shells
out to ``python -m manim_studio.manimgl_launcher`` (the manimgl CLI with the LaTeX
compatibility fix applied) with writing enabled, which keeps the live GL context
healthy and gives deterministic files.  Frozen builds re-invoke their own exe with
an internal flag instead — see :mod:`manim_studio.spawn` (ADR 0005).
"""

from __future__ import annotations

import subprocess
import tempfile
import threading
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import TYPE_CHECKING

from manim_studio import spawn
from manim_studio.sidecar import (
    SidecarError,
    load_scene_settings,
    load_sidecar,
    write_patch_file,
    write_settings_file,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from manim_studio.patches import Patch
    from manim_studio.scene_settings import SceneSettings

__all__ = [
    "QUALITY_FLAGS",
    "ExportError",
    "ExportPlan",
    "ExportResult",
    "build_export_plan",
    "effective_settings",
    "resolve_patches",
    "resolve_settings",
    "run_export",
]

#: manimgl resolution presets: -l 854x480, -m 1280x720, --hd 1920x1080, --uhd 3840x2160.
QUALITY_FLAGS: dict[str, tuple[str, ...]] = {
    "preview": ("-l",),
    "low": ("-l",),
    "medium": ("-m",),
    "high": ("--hd",),
    "4k": ("--uhd",),
}

_EXTENSIONS = {"mp4": ".mp4", "mov": ".mov", "gif": ".gif", "png": ".png"}


class ExportError(Exception):
    """Raised for invalid export requests (not for a failed render)."""


@dataclass(frozen=True, slots=True)
class ExportPlan:
    """Everything needed to run one export, without running it."""

    argv: tuple[str, ...]
    output_path: Path
    scene_name: str | None
    quality: str
    still: bool = False
    gif: bool = False
    resolution: tuple[int, int] | None = None
    animation_range: tuple[int, int] | None = None
    #: Timeline edits baked into the render (from the sidecar unless given explicitly).
    patches: tuple[Patch, ...] = ()

    def command_line(self) -> str:
        return " ".join(self.argv)


@dataclass(frozen=True, slots=True)
class ExportResult:
    """Outcome of :func:`run_export`."""

    returncode: int
    output_path: Path
    log_tail: tuple[str, ...] = field(default=())

    @property
    def ok(self) -> bool:
        return self.returncode == 0


def build_export_plan(
    scene_path: str | Path,
    scene_name: str | None = None,
    *,
    output: str | Path | None = None,
    quality: str = "high",
    fps: int | None = None,
    still: bool = False,
    gif: bool = False,
    resolution: tuple[int, int] | None = None,
    animation_range: tuple[int, int] | None = None,
    patches: Sequence[Patch] | None = None,
    settings: SceneSettings | None = None,
    use_sidecar: bool = True,
    extra_args: Sequence[str] = (),
) -> ExportPlan:
    """Translate editor-friendly options into a ``python -m manimlib`` invocation.

    ``output`` may include a directory and extension; manimgl itself is driven with
    ``--video_dir`` + ``--file_name``.

    ``fps`` is passed through a generated ``custom_config``-style YAML file rather
    than manimgl's ``--fps`` flag: in manimgl 1.7.2 that flag is never converted to
    ``int`` (argparse has no ``type=int``), so the camera ends up with a string and
    ``get_time_progression`` crashes with ``TypeError``.  The config file is written
    eagerly (deterministically and idempotently) because callers may execute the
    argv directly through ``QProcess`` without going through :func:`run_export`.

    ``gif`` maps to manimgl's ``-i`` (GIF output); ``resolution`` maps to ``-r WxH``
    and overrides the preset ``quality`` flags; ``animation_range`` maps to
    ``-n start,end`` (0-based, inclusive) so a long scene can be rendered in parts.

    ``patches`` are the timeline edits (:mod:`manim_studio.patches`) the render runs
    with.  When omitted, the sidecar next to the scene (``<scene>.studio.json``) is
    used, so the editor, ``manim-studio export`` and a plain script all produce what
    the preview shows; ``use_sidecar=False`` renders the untouched source.
    """
    if quality not in QUALITY_FLAGS:
        raise ExportError(f"unknown quality {quality!r}; choose from {sorted(QUALITY_FLAGS)}")
    scene_file = Path(scene_path)
    if not scene_file.is_file():
        raise ExportError(f"scene file does not exist: {scene_file}")
    if still and fps is not None:
        raise ExportError("fps is not applicable when exporting a still")
    if still and gif:
        raise ExportError("gif and still are mutually exclusive")
    if fps is not None and not 1 <= int(fps) <= 240:
        raise ExportError(f"fps {fps!r} out of range (1..240)")
    if resolution is not None:
        width, height = (int(value) for value in resolution)
        if not (64 <= width <= 4096 and 64 <= height <= 4096):
            raise ExportError(f"resolution {width}x{height} out of range (64..4096 per side)")
    if animation_range is not None:
        start, end = (int(value) for value in animation_range)
        if not 0 <= start <= end:
            raise ExportError(f"invalid animation range {animation_range!r}; expected start <= end")
    chosen_patches = resolve_patches(scene_file, patches, use_sidecar)
    effective = effective_settings(
        resolve_settings(scene_file, scene_name, settings, use_sidecar), resolution, fps
    )

    requested = Path(output) if output is not None else None
    if requested is not None:
        suffix = requested.suffix.lower()
        if suffix and suffix not in _EXTENSIONS.values():
            raise ExportError(f"unsupported output extension {suffix!r}")
        if gif and suffix and suffix != ".gif":
            raise ExportError("gif output must use a .gif extension")
        if still and suffix and suffix != ".png":
            raise ExportError("still output must use a .png extension")
        extension = suffix if suffix else _EXTENSIONS["gif" if gif else ("png" if still else "mp4")]
        output_path = requested.with_suffix(extension)
        output_dir = output_path.parent
        file_name = output_path.stem
    else:
        extension = _EXTENSIONS["gif" if gif else ("png" if still else "mp4")]
        output_dir = Path.cwd() / "videos"
        stem = scene_name or scene_file.stem
        file_name = stem
        output_path = output_dir / f"{stem}{extension}"

    # Scene settings decide the size and fps when present, so the preset flags would only
    # be overridden; an explicit resolution still wins, through the settings.
    quality_flags: tuple[str, ...] = (
        () if (resolution is not None or effective is not None) else QUALITY_FLAGS[quality]
    )
    resolution_flags: tuple[str, ...] = (
        ("-r", f"{int(resolution[0])}x{int(resolution[1])}")
        if resolution is not None and effective is None
        else ()
    )
    module_args: list[str] = [
        str(scene_file),
        *([scene_name] if scene_name else []),
        "-w",
        "--quiet",
        *quality_flags,
        *resolution_flags,
    ]
    if still:
        module_args.append("-s")
    if gif:
        module_args.append("-i")
    if animation_range is not None:
        module_args.extend(["-n", f"{int(animation_range[0])},{int(animation_range[1])}"])
    if chosen_patches:
        module_args.extend(["--patch-file", str(write_patch_file(chosen_patches))])
    module_args.extend(["--video_dir", str(output_dir), "--file_name", file_name])
    if effective is not None:
        module_args.extend(["--settings-file", str(write_settings_file(effective))])
    elif fps is not None:
        module_args.extend(["--config_file", str(_fps_config_path(int(fps)))])
    module_args.extend(extra_args)
    executable, tail = spawn.manimgl_export_command(module_args)
    argv: list[str] = [executable, *tail]
    return ExportPlan(
        argv=tuple(argv),
        output_path=output_path,
        scene_name=scene_name,
        quality=quality,
        still=still,
        gif=gif,
        resolution=resolution,
        animation_range=animation_range,
        patches=chosen_patches,
    )


def resolve_patches(
    scene_file: Path, patches: Sequence[Patch] | None, use_sidecar: bool
) -> tuple[Patch, ...]:
    if patches is not None:
        return tuple(patches)
    if not use_sidecar:
        return ()
    try:
        return load_sidecar(scene_file)
    except SidecarError as exc:
        raise ExportError(str(exc)) from exc


def resolve_settings(
    scene_file: Path,
    scene_name: str | None,
    settings: SceneSettings | None,
    use_sidecar: bool,
) -> SceneSettings | None:
    """The settings to render with: explicit, else the sidecar's for this scene."""
    if settings is not None:
        return settings
    if not use_sidecar:
        return None
    try:
        stored = load_scene_settings(scene_file)
    except SidecarError as exc:
        raise ExportError(str(exc)) from exc
    if scene_name is not None:
        return stored.get(scene_name)
    # Without a name, settings are only unambiguous when the file stores one scene's.
    return next(iter(stored.values())) if len(stored) == 1 else None


def effective_settings(
    settings: SceneSettings | None,
    resolution: tuple[int, int] | None,
    fps: int | None,
) -> SceneSettings | None:
    """Apply the caller's explicit resolution and fps on top of the scene's settings."""
    if settings is None:
        return None
    if resolution is not None:
        settings = replace(settings, width=int(resolution[0]), height=int(resolution[1]))
    if fps is not None:
        settings = replace(settings, fps=int(fps))
    return settings


def _fps_config_path(fps: int) -> Path:
    """Write (once) the config YAML that sets manimgl's camera fps reliably."""
    config_dir = Path(tempfile.gettempdir()) / "manim-studio"
    config_dir.mkdir(parents=True, exist_ok=True)
    path = config_dir / f"fps_{fps}.yml"
    content = (
        "# Generated by Manim Studio. manimgl 1.7.2 does not convert --fps to int,\n"
        "# so the frame rate is provided through a config file instead.\n"
        f"camera:\n  fps: {fps}\n"
    )
    if not path.exists() or path.read_text(encoding="utf-8") != content:
        path.write_text(content, encoding="utf-8")
    return path


def run_export(
    plan: ExportPlan,
    *,
    on_line: Callable[[str], None] | None = None,
    timeout: float | None = None,
    log_tail_size: int = 40,
) -> ExportResult:
    """Run an export plan, streaming output lines to ``on_line``.

    Never raises for a failed render — check :attr:`ExportResult.ok`.  ``timeout``
    kills the process and returns ``returncode=-1`` with the log tail.  ``on_line``
    is invoked from a reader thread, so it must be thread-safe.
    """
    from manim_studio.latex import runtime_env

    lines: list[str] = []
    process = subprocess.Popen(
        plan.argv,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=runtime_env(),
    )
    assert process.stdout is not None

    def drain() -> None:
        for raw_line in process.stdout:  # type: ignore[union-attr]
            line = raw_line.rstrip("\r\n")
            lines.append(line)
            if on_line is not None:
                on_line(line)

    reader = threading.Thread(target=drain, name="export-reader", daemon=True)
    reader.start()
    try:
        returncode = process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()
        returncode = -1
        lines.append(f"export timed out after {timeout}s")
    finally:
        process.stdout.close()
    reader.join(timeout=5)
    return ExportResult(
        returncode=returncode,
        output_path=plan.output_path,
        log_tail=tuple(lines[-log_tail_size:]),
    )
