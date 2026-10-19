"""Render a scene at any time, and tile several times into one contact-sheet image.

Frames come from :class:`~manim_studio.scrub.ScrubEngine`, the same posing the preview host uses
(ADR 0002). The scene is built the way export builds it: the sidecar's timeline patches are
applied to the source text and its scene settings set the frame size and fps (ADR 0007, ADR 0010).
Nothing here opens a window; manimgl is only imported inside the functions that run a scene.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont

from manim_studio.contact_sheet import (
    Sampling,
    SheetError,
    SheetLayout,
    block_at_time,
    normalize_times,
    plan_sheet,
    sample_times,
)
from manim_studio.export import ExportError, effective_settings, resolve_patches, resolve_settings
from manim_studio.latex import apply_runtime_path
from manim_studio.patch_runtime import StudioRuntime
from manim_studio.patches import apply_patches
from manim_studio.scene_runner import (
    DEFAULT_TRACE_RESOLUTION,
    SceneLoadError,
    check_scene_file,
    format_scene_error,
    load_scene_module,
    make_scene,
    manimlib_argv_guard,
    run_construct,
    select_scene_class,
)
from manim_studio.scene_settings import SceneSettings
from manim_studio.scrub import ScrubEngine, ScrubError, read_upright_rgba
from manim_studio.timeline_model import TimelineBlock
from manim_studio.tracer import TraceRecorder

__all__ = [
    "DEFAULT_RESOLUTION",
    "SHEET_FRAME_RESOLUTION",
    "FrameError",
    "FrameResult",
    "PreparedScene",
    "SheetResult",
    "prepare_scene",
    "render_contact_sheet",
    "render_frame",
    "render_frames",
]

DEFAULT_RESOLUTION: tuple[int, int] = DEFAULT_TRACE_RESOLUTION
SHEET_FRAME_RESOLUTION: tuple[int, int] = (640, 360)
SHEET_BACKGROUND = "#1e1e1e"
SHEET_BORDER = "#3c3c3c"
SHEET_TEXT = "#d4d4d4"
_LABEL_FONT_SIZE = 14


class FrameError(RuntimeError):
    """A scene failed while it was being run or posed; the message names the scene line."""


@dataclass(frozen=True, slots=True)
class PreparedScene:
    """A scene loaded and traced once in skip mode, ready to be posed at any time."""

    scene_class: type
    scene_name: str
    scene_path: Path
    blocks: tuple[TimelineBlock, ...]
    total_duration: float
    fps: int
    resolution: tuple[int, int]  # final render size: scene settings win over the defaults
    settings: SceneSettings | None
    patches_applied: int
    patches_orphaned: int


@dataclass(frozen=True, slots=True)
class FrameResult:
    """One frame of a scene at one time."""

    image: Image.Image  # RGBA, upright
    time: float  # the time actually posed, clamped into the scene
    requested_time: float
    block: TimelineBlock | None
    prepared: PreparedScene

    @property
    def clamped(self) -> bool:
        return self.time != self.requested_time

    def to_dict(self) -> dict[str, Any]:
        return {
            "scene": self.prepared.scene_name,
            "time": self.time,
            "requested_time": self.requested_time,
            "clamped": self.clamped,
            "block_index": self.block.index if self.block is not None else None,
            "block_name": self.block.name if self.block is not None else "",
            "total_duration": self.prepared.total_duration,
            "size": list(self.image.size),
            "patches_applied": self.prepared.patches_applied,
            "patches_orphaned": self.prepared.patches_orphaned,
        }


@dataclass(frozen=True, slots=True)
class SheetResult:
    """A contact sheet: one tile per sampled time, with its layout."""

    image: Image.Image  # RGB
    layout: SheetLayout
    sampling: Sampling | None  # None when explicit times were given
    prepared: PreparedScene

    def to_dict(self) -> dict[str, Any]:
        return {
            "scene": self.prepared.scene_name,
            "frames": [
                {
                    "index": cell.index,
                    "time": cell.time,
                    "block_index": cell.block_index,
                    "block_name": cell.block_name,
                }
                for cell in self.layout.cells
            ],
            "columns": self.layout.columns,
            "rows": self.layout.rows,
            "size": [self.image.width, self.image.height],
            "thinned": self.sampling.thinned if self.sampling is not None else False,
            "step": self.sampling.step if self.sampling is not None else None,
            "total_duration": self.prepared.total_duration,
            "patches_applied": self.prepared.patches_applied,
            "patches_orphaned": self.prepared.patches_orphaned,
        }


def _scene_failure(error: BaseException, scene_path: Path) -> str:
    """One line for a user-scene exception, with its scene line when one is known."""
    report = format_scene_error(error, scene_path)
    if report.line is None:
        return report.message
    return f"{report.message} (line {report.line})"


def _grab(scene: Any) -> Image.Image:
    """Draw the scene's current state and copy the camera's pixels out."""
    scene.update_frame(dt=0, force_draw=True)
    rgba, width, height = read_upright_rgba(scene.camera)
    return Image.frombytes("RGBA", (width, height), rgba)


def prepare_scene(
    path: str | Path,
    scene_name: str | None = None,
    *,
    resolution: tuple[int, int] | None = None,
    fps: int | None = None,
    use_sidecar: bool = True,
    default_resolution: tuple[int, int] = DEFAULT_RESOLUTION,
) -> PreparedScene:
    """Load a scene the way export does and trace it once (skip mode, nothing drawn).

    The sidecar's patches are applied to the source, and its scene settings set the frame size
    when present. An explicit ``resolution`` or ``fps`` overrides them. Without settings the
    frame size is ``resolution``, else ``default_resolution``.
    """
    apply_runtime_path()
    scene_path = Path(path)
    check = check_scene_file(scene_path)
    if not check.ok:
        raise SceneLoadError(check.error or "scene check failed", line=check.error_line)
    try:
        patches = resolve_patches(scene_path, None, use_sidecar)
        settings = resolve_settings(scene_path, scene_name, None, use_sidecar)
    except ExportError as exc:
        raise SceneLoadError(str(exc)) from exc
    settings = effective_settings(settings, resolution, fps)
    if settings is not None:
        size = (settings.width, settings.height)
    else:
        size = resolution or default_resolution

    result = apply_patches(scene_path.read_text(encoding="utf-8"), patches)
    runtime = StudioRuntime() if result.changed else None
    try:
        with manimlib_argv_guard():
            module = load_scene_module(
                scene_path,
                source=result.text if result.changed else None,
                runtime=runtime,
            )
            scene_class = select_scene_class(module, scene_name, path=scene_path)
            scene = make_scene(
                scene_class, resolution=size, fps=fps, settings=settings, skip_animations=True
            )
            recorder = TraceRecorder(record_events=False)
            recorder.attach(scene)
            try:
                run_construct(scene)
                snapshot = recorder.snapshot()
            finally:
                recorder.detach()
    except SceneLoadError:
        raise
    except Exception as exc:  # the scene's own code can raise anything
        raise FrameError(_scene_failure(exc, scene_path)) from exc

    blocks = snapshot.blocks
    return PreparedScene(
        scene_class=scene_class,
        scene_name=scene_class.__name__,
        scene_path=scene_path,
        blocks=blocks,
        # Block ends are sums of floats; round so the reported duration reads as written.
        total_duration=round(max((block.end for block in blocks), default=0.0), 6),
        fps=int(scene.camera.fps),
        resolution=size,
        settings=settings,
        patches_applied=len(result.applied),
        patches_orphaned=len(result.orphaned),
    )


def render_frames(prepared: PreparedScene, times: Sequence[float]) -> list[Image.Image]:
    """Pose the scene at each time (clamped into the scene) and return the RGBA frames in order.

    One fresh scene is built for the whole batch. Each pose replays the scene from the start, so
    the cost grows with the time reached; callers cap the number of frames.
    """
    targets = [min(max(float(time), 0.0), prepared.total_duration) for time in times]
    if not targets:
        return []
    with manimlib_argv_guard():
        scene = make_scene(
            prepared.scene_class,
            resolution=prepared.resolution,
            fps=prepared.fps,
            settings=prepared.settings,
            skip_animations=True,
        )
        if not prepared.blocks:
            # Nothing to replay: the scene is static, so one frame serves every time.
            try:
                run_construct(scene)
            except Exception as exc:
                raise FrameError(_scene_failure(exc, prepared.scene_path)) from exc
            still = _grab(scene)
            return [still.copy() for _ in targets]

        engine = ScrubEngine(scene, prepared.blocks)
        frames: list[Image.Image] = []
        for target in targets:
            try:
                engine.pose(target)
            except ScrubError as exc:
                cause = exc.__cause__ if exc.__cause__ is not None else exc
                raise FrameError(_scene_failure(cause, prepared.scene_path)) from exc
            frames.append(_grab(scene))
    return frames


def render_frame(
    path: str | Path,
    at: float,
    scene_name: str | None = None,
    *,
    resolution: tuple[int, int] | None = None,
    use_sidecar: bool = True,
) -> FrameResult:
    """The frame at ``at`` seconds; times past the end show the final state."""
    if not math.isfinite(at) or at < 0:
        raise ValueError("--at must be a non-negative number")
    prepared = prepare_scene(path, scene_name, resolution=resolution, use_sidecar=use_sidecar)
    posed = min(at, prepared.total_duration)
    image = render_frames(prepared, [posed])[0]
    return FrameResult(
        image=image,
        time=posed,
        requested_time=at,
        block=block_at_time(prepared.blocks, posed),
        prepared=prepared,
    )


def _label_font() -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    return ImageFont.load_default(size=_LABEL_FONT_SIZE)


def _compose(layout: SheetLayout, frames: Sequence[Image.Image]) -> Image.Image:
    sheet = Image.new("RGB", (layout.width, layout.height), SHEET_BACKGROUND)
    draw = ImageDraw.Draw(sheet)
    font = _label_font()
    for cell, frame in zip(layout.cells, frames, strict=True):
        tile = frame.convert("RGB").resize((cell.width, cell.height), Image.Resampling.LANCZOS)
        sheet.paste(tile, (cell.x, cell.y))
        draw.rectangle(
            (cell.x - 1, cell.y - 1, cell.x + cell.width, cell.y + cell.height),
            outline=SHEET_BORDER,
        )
        draw.text((cell.x + 2, cell.label_y + 3), cell.label, fill=SHEET_TEXT, font=font)
    return sheet


def render_contact_sheet(
    path: str | Path,
    scene_name: str | None = None,
    *,
    every: float | None = None,
    times: Sequence[float] | None = None,
    start: float = 0.0,
    end: float | None = None,
    columns: int | None = None,
    max_frames: int = 48,
    sheet_width: int = 1600,
    resolution: tuple[int, int] | None = None,
    use_sidecar: bool = True,
) -> SheetResult:
    """Tile the scene's frames into one image, sampled every ``every`` seconds or at ``times``.

    Exactly one of ``every`` and ``times`` must be given. Frames are rendered at ``resolution``,
    else the scene's settings, else :data:`SHEET_FRAME_RESOLUTION`.
    """
    if (every is None) == (times is None):
        raise SheetError("give exactly one of every or times")
    prepared = prepare_scene(
        path,
        scene_name,
        resolution=resolution,
        use_sidecar=use_sidecar,
        default_resolution=SHEET_FRAME_RESOLUTION,
    )
    total = prepared.total_duration
    sampling: Sampling | None = None
    if every is not None:
        sampling = sample_times(total, every, start=start, end=end, max_frames=max_frames)
        chosen = sampling.times
    elif times is not None:
        chosen = normalize_times(times, total)
        if len(chosen) > max_frames:
            raise SheetError(f"{len(chosen)} times exceed max_frames={max_frames}")
    else:  # unreachable: the check above guarantees one of the two
        raise SheetError("give exactly one of every or times")

    layout = plan_sheet(
        chosen,
        prepared.blocks,
        prepared.resolution,
        columns=columns,
        max_width=sheet_width,
    )
    image = _compose(layout, render_frames(prepared, chosen))
    return SheetResult(image=image, layout=layout, sampling=sampling, prepared=prepared)
