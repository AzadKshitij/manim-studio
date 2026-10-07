"""Preview presentation panel: embedded frame view + transport controls.

The panel is intentionally dumb: it renders frames, shows status and emits
button signals.  Orchestration lives in :class:`~manim_studio.editor.main_window.MainWindow`.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QImage, QPixmap, QResizeEvent, QShowEvent
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QStackedLayout,
    QVBoxLayout,
    QWidget,
)

from manim_studio.stats import TimingStats
from manim_studio.timeline_model import format_duration

__all__ = ["PreviewPanel"]


class PreviewPanel(QWidget):
    """Embedded preview viewport with Reload / Play / Stop controls."""

    reloadRequested = Signal()
    playRequested = Signal()
    stopRequested = Signal()

    #: How long after the last streamed frame the viewport re-scales smoothly.
    SETTLE_MS = 150

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        timings: TimingStats | None = None,
    ) -> None:
        super().__init__(parent)
        self._image: QImage | None = None
        self._embedded = True
        self.timings = timings if timings is not None else TimingStats()
        # Streaming frames scale with a fast (nearest) filter — the eye cannot
        # resolve detail in motion — and a short settle timer re-renders the last
        # frame smoothly once the stream pauses, so stills stay crisp.
        self._settle_timer = QTimer(self)
        self._settle_timer.setSingleShot(True)
        self._settle_timer.setInterval(self.SETTLE_MS)
        self._settle_timer.timeout.connect(self._settle)

        self._state_label = QLabel("idle")
        self._state_label.setObjectName("previewState")
        self._mode_label = QLabel("embedded")
        self._mode_label.setObjectName("previewMode")
        self._cache_label = QLabel("")
        self._cache_label.setObjectName("previewCache")
        self._cache_label.setStyleSheet("color: #888888;")
        self._time_label = QLabel("0.0s")
        self._time_label.setObjectName("previewTime")

        header = QHBoxLayout()
        header.addWidget(self._mode_label)
        header.addWidget(self._cache_label)
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
        if embedded:
            self._rescale(fast=False)

    @property
    def is_embedded(self) -> bool:
        return self._embedded

    @property
    def current_image(self) -> QImage | None:
        return self._image

    @property
    def cache_status(self) -> str:
        return self._cache_label.text().lstrip(" ·")

    def set_frame(self, image: QImage) -> None:
        self._image = image
        if not self._viewport.isVisible():
            # Off screen (minimised or another page): keep the frame but defer the
            # pixmap conversion and scale until the viewport is shown again.
            self._settle_timer.stop()
            return
        self._rescale(fast=True)
        self._settle_timer.start()

    def _settle(self) -> None:
        """Re-render the last streamed frame smoothly now that it has paused."""
        self._rescale(fast=False)

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

    def set_cache_status(self, text: str) -> None:
        """Small filmstrip indicator ('cache: 312 frames…')."""
        self._cache_label.setText(f" · {text}" if text else "")

    def set_time(self, seconds: float, total: float | None = None) -> None:
        if total is None:
            self._time_label.setText(format_duration(seconds))
        else:
            self._time_label.setText(f"{format_duration(seconds)} / {format_duration(total)}")

    def clear_frame(self) -> None:
        self._settle_timer.stop()
        self._image = None
        self._viewport.clear()

    def resizeEvent(self, event: QResizeEvent) -> None:
        # A real resize must re-scale regardless of streaming state; do it smoothly
        # and let the next streamed frame take over in fast mode.
        self._settle_timer.stop()
        self._rescale(fast=False)
        super().resizeEvent(event)

    def showEvent(self, event: QShowEvent) -> None:
        # A frame that arrived while hidden was stored but never scaled.
        self._rescale(fast=False)
        super().showEvent(event)

    def _rescale(self, *, fast: bool) -> None:
        if self._image is None:
            return
        mode = (
            Qt.TransformationMode.FastTransformation
            if fast
            else Qt.TransformationMode.SmoothTransformation
        )
        with self.timings.phase("scale"):
            pixmap = QPixmap.fromImage(self._image)
            scaled = pixmap.scaled(
                self._viewport.size(),
                Qt.AspectRatioMode.KeepAspectRatio,
                mode,
            )
        self._viewport.setPixmap(scaled)
