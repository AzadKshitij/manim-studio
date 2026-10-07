"""Compact transport controls above the timeline.

A passive row of buttons and selectors: jump to start/end, step one frame,
play/pause, loop, and a playback-speed choice.  The widget only emits signals;
:class:`~manim_studio.editor.main_window.MainWindow` drives the preview host and
the rendered-video transport from them.
"""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QWidget,
)

__all__ = ["SPEEDS", "TimelineTransport"]

#: Playback-speed choices offered in the selector (1.0 = scene speed).
SPEEDS: tuple[float, ...] = (0.25, 0.5, 0.75, 1.0, 1.5, 2.0)


class TimelineTransport(QWidget):
    """Timeline transport: start/end, frame stepping, play/pause, loop, speed."""

    playPauseRequested = Signal()
    stepRequested = Signal(int)  # -1 or +1 frame
    jumpRequested = Signal(str)  # "start" | "end"
    loopToggled = Signal(bool)
    speedChanged = Signal(float)
    zoomRequested = Signal(str)  # "fit" | "selection" | "in" | "out"

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._start_button = QPushButton("Start", self)
        self._start_button.setToolTip("Jump to the beginning (Home)")
        self._back_button = QPushButton("-1fr", self)
        self._back_button.setToolTip("Step back one frame (Left)")
        self._play_button = QPushButton("Play", self)
        self._play_button.setToolTip("Play or pause (Space)")
        self._forward_button = QPushButton("+1fr", self)
        self._forward_button.setToolTip("Step forward one frame (Right)")
        self._end_button = QPushButton("End", self)
        self._end_button.setToolTip("Jump to the end (End)")
        self._loop_check = QCheckBox("Loop", self)
        self._loop_check.setToolTip("Repeat playback over the selected range (or the scene)")

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

        self._fit_button = QPushButton("Fit", self)
        self._fit_button.setToolTip("Fit the whole timeline (Ctrl+0)")
        self._selection_button = QPushButton("Fit selection", self)
        self._selection_button.setToolTip("Zoom to the selected range (or the caret's block)")
        self._zoom_out_button = QPushButton("-", self)
        self._zoom_out_button.setToolTip("Zoom out")
        self._zoom_in_button = QPushButton("+", self)
        self._zoom_in_button.setToolTip("Zoom in")
        self._fit_button.clicked.connect(lambda: self.zoomRequested.emit("fit"))
        self._selection_button.clicked.connect(lambda: self.zoomRequested.emit("selection"))
        self._zoom_out_button.clicked.connect(lambda: self.zoomRequested.emit("out"))
        self._zoom_in_button.clicked.connect(lambda: self.zoomRequested.emit("in"))

        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 2, 4, 2)
        layout.setSpacing(4)
        for widget in (
            self._start_button,
            self._back_button,
            self._play_button,
            self._forward_button,
            self._end_button,
        ):
            layout.addWidget(widget)
        layout.addSpacing(6)
        layout.addWidget(self._loop_check)
        layout.addSpacing(6)
        layout.addWidget(QLabel("Speed:"))
        layout.addWidget(self._speed_combo)
        layout.addSpacing(6)
        for widget in (
            self._fit_button,
            self._selection_button,
            self._zoom_out_button,
            self._zoom_in_button,
        ):
            layout.addWidget(widget)
        layout.addStretch(1)

    # ------------------------------------------------------------------

    def set_playing(self, playing: bool) -> None:
        """Flip the play/pause button to match the host's state."""
        self._play_button.setText("Pause" if playing else "Play")

    def playing(self) -> bool:
        return self._play_button.text() == "Pause"

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

    def _emit_speed(self, *_args: object) -> None:
        self.speedChanged.emit(self.speed())
