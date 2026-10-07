"""Preview-host event routing: traces, ticks, frames, errors and host state.

The preview host reports through :class:`PreviewController` signals; this router
turns them into editor/timeline/panel/status updates and owns the session state
those events define — the trace duration, the block Play starts from and the
playing flag — so the window reads them as properties instead of mirroring them.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from PySide6.QtCore import QObject
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QLabel

from manim_studio.timeline_model import format_duration, parse_log

if TYPE_CHECKING:
    from collections.abc import Callable

    from manim_studio.editor.code_editor import CodeEditor
    from manim_studio.editor.preview_controller import PreviewController
    from manim_studio.editor.preview_panel import PreviewPanel
    from manim_studio.editor.shell_panel import ShellPanel
    from manim_studio.editor.timeline_widget import TimelineWidget

__all__ = ["PreviewEvents"]


class PreviewEvents(QObject):
    """Routes preview-host events and owns trace/playback session state."""

    def __init__(
        self,
        *,
        editor: CodeEditor,
        timeline: TimelineWidget,
        preview_panel: PreviewPanel,
        shell: ShellPanel,
        controller: PreviewController,
        host_state_label: QLabel,
        initial_frame: str,
        status_line: Callable[[str], None],
        refresh_latex: Callable[[], None],
        video_mode_active: Callable[[], bool],
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._editor = editor
        self._timeline = timeline
        self._preview_panel = preview_panel
        self._shell = shell
        self._host_state_label = host_state_label
        self._initial_frame = initial_frame
        self._status_line = status_line
        self._refresh_latex = refresh_latex
        self._video_mode_active = video_mode_active
        self._total_duration = 0.0
        self._play_from_index: int | None = None
        self._playing = False

        controller.logReceived.connect(self.on_log)
        controller.timingChanged.connect(self.on_timing)
        controller.statusChanged.connect(self.on_status)
        controller.tickReceived.connect(self.on_tick)
        controller.stoppedReceived.connect(self.on_stopped)
        controller.errorReceived.connect(self.on_error)
        controller.frameReceived.connect(self.on_frame)
        controller.stateChanged.connect(self.on_host_state)

    # -- session state -----------------------------------------------------

    @property
    def total_duration(self) -> float:
        """Duration of the last successful trace (0.0 before the first one)."""
        return self._total_duration

    @total_duration.setter
    def total_duration(self, seconds: float) -> None:
        self._total_duration = max(0.0, float(seconds))

    @property
    def play_from_index(self) -> int | None:
        """Block that Play starts from, or ``None`` for the beginning."""
        return self._play_from_index

    @play_from_index.setter
    def play_from_index(self, index: int | None) -> None:
        self._play_from_index = index

    @property
    def playing(self) -> bool:
        return self._playing

    # -- host events -------------------------------------------------------

    def on_log(self, message: dict[str, Any]) -> None:
        try:
            timeline_log = parse_log(message.get("blocks", []))
        except Exception as exc:
            self._shell.append(f"bad trace payload: {exc}")
            return
        self._timeline.set_log(timeline_log)
        self._total_duration = timeline_log.total_duration
        # A successful trace means the scene now compiles/runs: clear any marker
        # left by the previous failure.
        self._editor.clear_error_marker()
        self._preview_panel.set_cache_status("")
        # The host shows the final frame by default; line the playhead up with it and
        # forget any previously selected block so Play restarts from the beginning.
        self._timeline.set_playhead(self._total_duration if self._initial_frame == "end" else 0.0)
        self._preview_panel.set_time(self._timeline.playhead_time, self._total_duration)
        self._play_from_index = None
        self._status_line(
            f"trace: {timeline_log.num_blocks} blocks, {format_duration(self._total_duration)}"
        )

    def on_timing(self, message: dict[str, Any]) -> None:
        """The host confirmed a retime: adopt its authoritative blocks.

        Unlike a fresh trace this keeps the current zoom and playhead (clamped to
        the new total), so stretching a section does not snap the view around.
        """
        try:
            timeline_log = parse_log(message.get("blocks", []))
        except Exception as exc:  # malformed payload must not kill the editor
            self._shell.append(f"bad timing payload: {exc}")
            return
        self._timeline.set_log(timeline_log, keep_view=True)
        self._total_duration = timeline_log.total_duration
        position = min(self._timeline.playhead_time, self._total_duration)
        self._timeline.set_playhead(position)
        self._preview_panel.set_time(position, self._total_duration)

    def on_status(self, state: str, message: dict[str, Any]) -> None:
        note = str(message.get("note", ""))
        self._playing = state == "playing"
        self._preview_panel.set_status(state, note)
        self._timeline.set_playing(self._playing)

    def on_tick(self, time_seconds: float, _index: int, scrubbing: bool = False) -> None:
        # While the user is dragging, the widget already owns the playhead: applying
        # host ticks here would fight the pointer and make it jitter (ticks for
        # older positions can arrive after the pointer has moved on).
        if scrubbing:
            return
        if self._video_mode_active():
            return
        self._timeline.set_playhead(time_seconds)
        self._preview_panel.set_time(time_seconds, self._total_duration)

    def on_stopped(self, message: dict[str, Any]) -> None:
        self._timeline.set_playing(False)
        self._status_line(
            "playback stopped" if message.get("user_stopped") else "playback finished"
        )

    def on_error(self, message: dict[str, Any]) -> None:
        text = str(message.get("message", "unknown error"))
        self._preview_panel.set_status("error", text)
        self._shell.append(f"ERROR: {text}")
        traceback_text = str(message.get("traceback", ""))
        if traceback_text:
            self._shell.append(traceback_text.rstrip())
        line = message.get("line")
        if isinstance(line, int):
            self._editor.set_error_marker(line, text)
        if "LaTeX" in text or "latex" in text:
            self._refresh_latex()
            self._status_line(
                f"preview error: {text} — open Tools > LaTeX setup and click Diagnose"
            )
        else:
            self._status_line(f"preview error: {text}")

    def on_frame(self, image: QImage) -> None:
        if not self._video_mode_active():
            self._preview_panel.set_frame(image)

    def on_host_state(self, state: str) -> None:
        self._host_state_label.setText(f"preview: {state}")
        if state == "crashed":
            self._preview_panel.set_status("crashed", "host process died — press Reload")
            self._status_line("preview host crashed")
