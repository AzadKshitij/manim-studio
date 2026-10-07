"""Child-process controller for preview renders, video exports and stills.

The editor never renders exports itself: it shells out to the manimgl CLI through
:func:`manim_editor.export.build_export_plan` and streams the child's output into
the live-shell log.  This controller owns those ``QProcess`` instances, keeps at
most one render of each kind running, and republishes outcomes as Qt signals so
the window only handles UI.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtCore import QObject, QProcess, Signal

from manim_editor import ipc
from manim_editor.export import build_export_plan

if TYPE_CHECKING:
    from collections.abc import Callable

__all__ = ["PREVIEW_FPS", "ExportController"]

#: Frame rate of the low-quality scrub video the preview button renders.
PREVIEW_FPS = 15


class ExportController(QObject):
    """Owns the export child processes and reports their outcome."""

    statusChanged = Signal(str)  # transient status-line text
    outputLine = Signal(str)  # one line of child stdout/stderr
    previewVideoReady = Signal(Path)  # preview render finished, file exists
    previewVideoFailed = Signal(int)  # preview render exit code
    exportFinished = Signal(int)  # export/still exit code

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._preview_process: QProcess | None = None
        self._export_process: QProcess | None = None

    def preview_render_running(self) -> bool:
        return self._is_running(self._preview_process)

    def export_running(self) -> bool:
        return self._is_running(self._export_process)

    def render_preview_video(self, scene_path: Path, scene_name: str | None, output: Path) -> bool:
        """Render the low-quality scrub video; False when one is already running."""
        if self._is_running(self._preview_process):
            self.statusChanged.emit("preview render already running")
            return False
        plan = build_export_plan(
            scene_path, scene_name, output=output, quality="preview", fps=PREVIEW_FPS
        )
        self._preview_process = self._spawn(
            plan.argv, lambda exit_code: self._on_preview_finished(exit_code, output)
        )
        self.statusChanged.emit(f"rendering preview video ({output.name})…")
        return True

    def export_video(
        self,
        scene_path: Path,
        scene_name: str | None,
        output: str | Path,
        *,
        quality: str = "high",
        fps: int | None = None,
    ) -> bool:
        """Export a video file; False when an export is already running."""
        if self._is_running(self._export_process):
            self.statusChanged.emit("export already running")
            return False
        plan = build_export_plan(scene_path, scene_name, output=output, quality=quality, fps=fps)
        self._export_process = self._spawn(plan.argv, self.exportFinished.emit)
        self.statusChanged.emit(f"exporting {plan.output_path}…")
        return True

    def export_still(self, scene_path: Path, scene_name: str | None, output: str | Path) -> bool:
        """Export a single frame; False when an export is already running."""
        if self._is_running(self._export_process):
            self.statusChanged.emit("export already running")
            return False
        plan = build_export_plan(scene_path, scene_name, output=output, still=True)
        self._export_process = self._spawn(plan.argv, self.exportFinished.emit)
        self.statusChanged.emit(f"exporting still {plan.output_path.name}…")
        return True

    def shutdown(self) -> None:
        """Kill any running render (called from the window's ``closeEvent``)."""
        for process in (self._preview_process, self._export_process):
            if process is not None and process.state() != QProcess.ProcessState.NotRunning:
                process.kill()
                process.waitForFinished(1500)

    # -- internals ---------------------------------------------------------

    @staticmethod
    def _is_running(process: QProcess | None) -> bool:
        return process is not None and process.state() != QProcess.ProcessState.NotRunning

    def _spawn(self, argv: tuple[str, ...], finished: Callable[[int], None]) -> QProcess:
        process = QProcess(self)
        process.readyReadStandardOutput.connect(lambda: self._drain_output(process))
        process.readyReadStandardError.connect(lambda: self._drain_output(process))
        process.finished.connect(lambda exit_code, _status: finished(exit_code))
        process.start(argv[0], list(argv[1:]))
        return process

    def _drain_output(self, process: QProcess) -> None:
        for stream in (process.readAllStandardOutput(), process.readAllStandardError()):
            text = ipc.as_bytes(stream).decode("utf-8", "replace")
            for line in text.splitlines():
                if line.strip():
                    self.outputLine.emit(line)

    def _on_preview_finished(self, exit_code: int, output: Path) -> None:
        if exit_code == 0 and output.is_file():
            self.previewVideoReady.emit(output)
        else:
            self.previewVideoFailed.emit(exit_code)
