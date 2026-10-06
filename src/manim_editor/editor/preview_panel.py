"""Preview presentation panel: embedded frame view + transport controls.

The panel is intentionally dumb: it renders frames, shows status and emits
button signals.  Orchestration lives in :class:`~manim_editor.editor.main_window.MainWindow`.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QImage, QPixmap, QResizeEvent
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QStackedLayout,
    QVBoxLayout,
    QWidget,
)

from manim_editor.timeline_model import format_duration

__all__ = ["PreviewPanel"]


class PreviewPanel(QWidget):
    """Embedded preview viewport with Reload / Play / Stop controls."""

    reloadRequested = Signal()
    playRequested = Signal()
    stopRequested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._image: QImage | None = None
        self._embedded = True

        self._state_label = QLabel("idle")
        self._state_label.setObjectName("previewState")
        self._mode_label = QLabel("embedded")
        self._mode_label.setObjectName("previewMode")
        self._time_label = QLabel("0.0s")
        self._time_label.setObjectName("previewTime")

        header = QHBoxLayout()
        header.addWidget(self._mode_label)
        header.addStretch(1)
        header.addWidget(self._time_label)
        header.addSpacing(8)
        header.addWidget(self._state_label)

        self._viewport = QLabel()
        self._viewport.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._viewport.setMinimumSize(320, 180)
        self._viewport.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._viewport.setStyleSheet("background-color: #101010; color: #888888;")

        self._placeholder = QLabel(
            "External GL window mode\n\n"
            "The scene renders in a separate OpenGL window.\n"
            "Switch to embedded mode to see it here."
        )
        self._placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._placeholder.setStyleSheet("background-color: #101010; color: #888888;")

        self._stack = QStackedLayout()
        self._stack.addWidget(self._placeholder)
        self._stack.addWidget(self._viewport)

        self._reload_button = QPushButton("Reload")
        self._reload_button.setToolTip("Reload the scene from disk (F5)")
        self._play_button = QPushButton("Play")
        self._play_button.setToolTip("Replay from the beginning")
        self._stop_button = QPushButton("Stop")
        self._stop_button.setToolTip("Stop live playback")

        self._reload_button.clicked.connect(self.reloadRequested)
        self._play_button.clicked.connect(self.playRequested)
        self._stop_button.clicked.connect(self.stopRequested)

        controls = QHBoxLayout()
        controls.addWidget(self._reload_button)
        controls.addWidget(self._play_button)
        controls.addWidget(self._stop_button)
        controls.addStretch(1)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.addLayout(header)
        layout.addLayout(self._stack, 1)
        layout.addLayout(controls)

        self.set_mode(embedded=True)

    # ------------------------------------------------------------------
    # State
    # ------------------------------------------------------------------

    def set_mode(self, *, embedded: bool) -> None:
        self._embedded = embedded
        self._mode_label.setText("embedded" if embedded else "external window")
        self._stack.setCurrentWidget(self._viewport if embedded else self._placeholder)

    @property
    def is_embedded(self) -> bool:
        return self._embedded

    @property
    def current_image(self) -> QImage | None:
        return self._image

    def set_frame(self, image: QImage) -> None:
        self._image = image
        self._rescale()

    def set_status(self, state: str, detail: str = "") -> None:
        text = state if not detail else f"{state} — {detail}"
        self._state_label.setText(text)
        color = {
            "idle": "#8FD48F",
            "playing": "#7FC6FF",
            "seeking": "#FFD37F",
            "error": "#FF8080",
            "crashed": "#FF8080",
        }.get(state, "#CCCCCC")
        self._state_label.setStyleSheet(f"color: {color};")

    def set_time(self, seconds: float, total: float | None = None) -> None:
        if total is None:
            self._time_label.setText(format_duration(seconds))
        else:
            self._time_label.setText(f"{format_duration(seconds)} / {format_duration(total)}")

    def clear_frame(self) -> None:
        self._image = None
        self._viewport.clear()

    def resizeEvent(self, event: QResizeEvent) -> None:
        self._rescale()
        super().resizeEvent(event)

    def _rescale(self) -> None:
        if self._image is None:
            return
        pixmap = QPixmap.fromImage(self._image)
        self._viewport.setPixmap(
            pixmap.scaled(
                self._viewport.size(),
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        )
