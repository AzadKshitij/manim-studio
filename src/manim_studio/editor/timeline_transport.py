"""Compact icon toolbar above the timeline.

A passive row of icon buttons (full names live in the tooltips): jump to start/end,
step one frame, play/pause, loop and a playback-speed choice; zoom controls; and the
timeline *operations* - undo/redo, delete, reset, insert a hold, and the edit
lifecycle (apply to source, show diff, discard orphaned/all edits).  The widget only
emits signals; :class:`~manim_studio.editor.main_window.MainWindow` drives the
preview host, the edit controller and the rendered-video transport from them.
"""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QToolButton,
    QWidget,
)

from manim_studio.editor.glyphs import glyph, icon_button

__all__ = ["EDIT_ACTIONS", "SPEEDS", "TimelineTransport"]

#: Playback-speed choices offered in the selector (1.0 = scene speed).
SPEEDS: tuple[float, ...] = (0.25, 0.5, 0.75, 1.0, 1.5, 2.0)

#: Timeline operations, in toolbar order: ``(id, glyph, tooltip)``.
EDIT_ACTIONS: tuple[tuple[str, str, str], ...] = (
    ("undo", "undo", "Undo timeline edit  (Ctrl+Z on the timeline)"),
    ("redo", "redo", "Redo timeline edit  (Ctrl+Y on the timeline)"),
    ("delete", "delete", "Delete the selected block's call (a timeline edit)"),
    ("reset", "reset", "Reset the selected block's timeline edits"),
    ("hold", "hold", "Insert a hold (self.wait) at the playhead"),
    ("apply", "apply", "Apply timeline edits to the source code"),
    ("diff", "diff", "Show what applying the timeline edits would change"),
    ("discard_orphans", "discard_orphans", "Discard orphaned edits (their call no longer exists)"),
    ("discard_all", "discard_all", "Discard all timeline edits"),
)


def _separator(parent: QWidget) -> QFrame:
    line = QFrame(parent)
    line.setFrameShape(QFrame.Shape.VLine)
    line.setFrameShadow(QFrame.Shadow.Sunken)
    return line


class TimelineTransport(QWidget):
    """Timeline toolbar: transport, zoom and timeline operations."""

    playPauseRequested = Signal()
    stepRequested = Signal(int)  # -1 or +1 frame
    jumpRequested = Signal(str)  # "start" | "end"
    loopToggled = Signal(bool)
    speedChanged = Signal(float)
    zoomRequested = Signal(str)  # "fit" | "selection" | "in" | "out"
    editRequested = Signal(str)  # one of the ids in EDIT_ACTIONS

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._playing = False
        self._start_button = icon_button("start", "Jump to the beginning  (Home)", self)
        self._back_button = icon_button("step_back", "Step back one frame  (Left)", self)
        self._play_button = icon_button("play", "Play  (Space)", self)
        self._forward_button = icon_button("step_forward", "Step forward one frame  (Right)", self)
        self._end_button = icon_button("end", "Jump to the end  (End)", self)
        self._loop_check = icon_button(
            "loop", "Loop the selected range (or the whole scene)", self, checkable=True
        )

        self._start_button.clicked.connect(lambda: self.jumpRequested.emit("start"))
        self._back_button.clicked.connect(lambda: self.stepRequested.emit(-1))
        self._play_button.clicked.connect(self.playPauseRequested)
        self._forward_button.clicked.connect(lambda: self.stepRequested.emit(1))
        self._end_button.clicked.connect(lambda: self.jumpRequested.emit("end"))
        self._loop_check.toggled.connect(self.loopToggled)

        self._speed_combo = QComboBox(self)
        for speed in SPEEDS:
            self._speed_combo.addItem(f"{speed:g}x", speed)
        self._speed_combo.setCurrentIndex(SPEEDS.index(1.0))
        self._speed_combo.setToolTip("Playback speed (1x = the scene's own timing)")
        self._speed_combo.currentIndexChanged.connect(self._emit_speed)

        self._fit_button = icon_button("fit", "Fit the whole timeline  (Ctrl+0)", self)
        self._selection_button = icon_button(
            "fit_selection", "Zoom to the selected range (or the selected block)", self
        )
        self._zoom_out_button = icon_button("zoom_out", "Zoom out  (-)", self)
        self._zoom_in_button = icon_button("zoom_in", "Zoom in  (+)", self)
        self._fit_button.clicked.connect(lambda: self.zoomRequested.emit("fit"))
        self._selection_button.clicked.connect(lambda: self.zoomRequested.emit("selection"))
        self._zoom_out_button.clicked.connect(lambda: self.zoomRequested.emit("out"))
        self._zoom_in_button.clicked.connect(lambda: self.zoomRequested.emit("in"))

        self._edit_buttons: dict[str, QToolButton] = {}
        for action_id, glyph_name, tooltip in EDIT_ACTIONS:
            button = icon_button(glyph_name, tooltip, self)
            button.setEnabled(False)
            button.clicked.connect(lambda _checked=False, a=action_id: self.editRequested.emit(a))
            self._edit_buttons[action_id] = button

        #: "3 timeline edits (1 orphaned)"; hidden while there are none.
        self.edits_label = QLabel("", self)
        self.edits_label.setToolTip(
            "Timeline edits live in a sidecar file next to the scene, not in the source.\n"
            "Apply them to the source with the check button."
        )
        self.edits_label.setVisible(False)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 2, 4, 2)
        layout.setSpacing(2)
        for widget in (
            self._start_button,
            self._back_button,
            self._play_button,
            self._forward_button,
            self._end_button,
            self._loop_check,
        ):
            layout.addWidget(widget)
        layout.addWidget(self._speed_combo)
        layout.addSpacing(4)
        layout.addWidget(_separator(self))
        for widget in (
            self._fit_button,
            self._selection_button,
            self._zoom_out_button,
            self._zoom_in_button,
        ):
            layout.addWidget(widget)
        layout.addSpacing(4)
        layout.addWidget(_separator(self))
        for action_id, _glyph, _tip in EDIT_ACTIONS:
            if action_id == "apply":
                layout.addWidget(_separator(self))
            layout.addWidget(self._edit_buttons[action_id])
        layout.addSpacing(6)
        layout.addWidget(self.edits_label)
        layout.addStretch(1)

    # ------------------------------------------------------------------

    def set_playing(self, playing: bool) -> None:
        """Flip the play/pause button to match the host's state."""
        self._playing = bool(playing)
        self._play_button.setIcon(glyph("pause" if self._playing else "play"))
        text = "Pause  (Space)" if self._playing else "Play  (Space)"
        self._play_button.setToolTip(text)
        self._play_button.setAccessibleName(text.split("  (")[0])

    def playing(self) -> bool:
        return self._playing

    def loop_enabled(self) -> bool:
        return self._loop_check.isChecked()

    def set_loop(self, enabled: bool) -> None:
        self._loop_check.setChecked(bool(enabled))

    def speed(self) -> float:
        return float(self._speed_combo.currentData())

    def set_speed(self, speed: float) -> None:
        index = self._speed_combo.findData(speed)
        if index >= 0:
            self._speed_combo.setCurrentIndex(index)

    def edit_button(self, action_id: str) -> QToolButton:
        """The button for one timeline operation (see :data:`EDIT_ACTIONS`)."""
        return self._edit_buttons[action_id]

    def set_edit_state(self, enabled: dict[str, bool]) -> None:
        """Enable the operations that make sense right now; absent ids are disabled."""
        for action_id, button in self._edit_buttons.items():
            button.setEnabled(bool(enabled.get(action_id, False)))

    def set_edits_summary(self, text: str) -> None:
        self.edits_label.setText(text)
        self.edits_label.setVisible(bool(text))

    def _emit_speed(self, *_args: object) -> None:
        self.speedChanged.emit(self.speed())
