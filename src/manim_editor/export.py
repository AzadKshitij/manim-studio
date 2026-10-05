"""Video / still export through headless ``manimgl -w``.

The editor never renders exports through its live preview process: exporting shells
out to ``python -m manimlib`` with writing enabled, which keeps the live GL context
healthy and gives deterministic files.
"""

from __future__ import annotations

import subprocess
import sys
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

__all__ = [
    "QUALITY_FLAGS",
    "ExportError",
    "ExportPlan",
    "ExportResult",
    "build_export_plan",
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
    extra_args: Sequence[str] = (),
) -> ExportPlan:
    """Translate editor-friendly options into a ``python -m manimlib`` invocation.

    ``output`` may include a directory and extension; manimgl itself is driven with
    ``--video_dir`` + ``--file_name``.
    """
    if quality not in QUALITY_FLAGS:
        raise ExportError(f"unknown quality {quality!r}; choose from {sorted(QUALITY_FLAGS)}")
    scene_file = Path(scene_path)
    if not scene_file.is_file():
        raise ExportError(f"scene file does not exist: {scene_file}")
    if still and fps is not None:
        raise ExportError("fps is not applicable when exporting a still")

    requested = Path(output) if output is not None else None
    if requested is not None:
        suffix = requested.suffix.lower()
        if suffix and suffix not in _EXTENSIONS.values():
            raise ExportError(f"unsupported output extension {suffix!r}")
        extension = suffix if suffix else _EXTENSIONS["png" if still else "mp4"]
        output_path = requested.with_suffix(extension)
        output_dir = output_path.parent
        file_name = output_path.stem
    else:
        extension = ".png" if still else ".mp4"
        output_dir = Path.cwd() / "videos"
        stem = scene_name or scene_file.stem
        file_name = stem
        output_path = output_dir / f"{stem}{extension}"

    argv: list[str] = [
        sys.executable,
        "-m",
        "manimlib",
        str(scene_file),
        *([scene_name] if scene_name else []),
        "-w",
        "--quiet",
        *QUALITY_FLAGS[quality],
    ]
    if still:
        argv.append("-s")
    argv.extend(["--video_dir", str(output_dir), "--file_name", file_name])
    if fps is not None:
        argv.extend(["--fps", str(fps)])
    argv.extend(extra_args)
    return ExportPlan(
        argv=tuple(argv),
        output_path=output_path,
        scene_name=scene_name,
        quality=quality,
        still=still,
    )


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
    lines: list[str] = []
    process = subprocess.Popen(
        plan.argv,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
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
