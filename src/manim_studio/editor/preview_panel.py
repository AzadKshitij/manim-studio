"""Preview presentation panel: the embedded frame viewport.

The panel is intentionally dumb: it renders frames and shows status.  All
transport controls (play/pause, step, loop, speed, reload) live on the timeline's
transport toolbar and the main toolbar; orchestration lives in
:class:`~manim_studio.editor.main_window.MainWindow`.
"""

from __future__ import annotations

from PySide6.QtCore import QEvent, QObject, Qt, QTimer, Signal
from PySide6.QtGui import QImage, QMouseEvent, QPixmap, QResizeEvent, QShowEvent, QWheelEvent
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QStackedLayout,
    QVBoxLayout,
    QWidget,
)

from manim_studio.editor.error_banner import ErrorBanner
from manim_studio.editor.loading_overlay import LoadingOverlay
from manim_studio.stats import TimingStats
from manim_studio.theme import Theme, current_theme
from manim_studio.timeline_model import format_duration

__all__ = ["PreviewPanel"]


class PreviewPanel(QWidget):
    """Embedded preview viewport (transport lives on the timeline toolbar)."""

    errorGotoLineRequested = Signal(int)  # the error banner's Go to line
    errorReloadRequested = Signal()  # the error banner's Reload
    playToggleRequested = Signal()  # a click on the picture: play or pause
    frameStepRequested = Signal(int)  # wheel travel in frames (negative = back)

    #: How long after the last streamed frame the viewport re-scales smoothly.
    SETTLE_MS = 150
    #: Qt reports one mouse-wheel notch as 120 units of angle; one notch steps one frame.
    WHEEL_NOTCH = 120

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        timings: TimingStats | None = None,
    ) -> None:
        super().__init__(parent)
        self._image: QImage | None = None
        self._embedded = True
        self._wheel_travel = 0  # wheel units that do not make a whole notch yet
        self.timings = timings if timings is not None else TimingStats()
        # Streaming frames scale with a fast (nearest) filter — the eye cannot
        # resolve detail in motion — and a short settle timer re-renders the last
        # frame smoothly once the stream pauses, so stills stay crisp.
        self._settle_timer = QTimer(self)
        self._settle_timer.setSingleShot(True)
        self._settle_timer.setInterval(self.SETTLE_MS)
        self._settle_timer.timeout.connect(self._settle)

        # The status bar's chip says what the preview is doing; the header only names
        # the view and shows the playhead time.
        self._title_label = QLabel("Preview")
        self._title_label.setObjectName("previewTitle")
        self._cache_text = ""
        self._time_label = QLabel("0.0s")
        self._time_label.setObjectName("previewTime")

        header_bar = QWidget(self)
        header_bar.setObjectName("panelHeader")
        header_bar.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        header_bar.setFixedHeight(32)
        self._header_bar = header_bar
        header = QHBoxLayout(header_bar)
        header.setContentsMargins(12, 0, 12, 0)
        header.addWidget(self._title_label)
        self._format_label = QLabel("", self)
        self._format_label.setObjectName("panelMeta")
        header.addWidget(self._format_label)
        header.addStretch(1)
        header.addWidget(self._time_label)

        self._viewport = QLabel()
        self._viewport.setAlignment(Qt.AlignmentFlag.AlignCenter)
        # A small floor and an Ignored size hint: the body gives the viewport whatever
        # room it has (a tall portrait preview can shrink), and _rescale letterboxes.
        self._viewport.setMinimumSize(96, 96)
        self._viewport.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Ignored)
        self._viewport.setToolTip("Click: play or pause  ·  Wheel: step frames")

        self._placeholder = QLabel(
            "External GL window mode\n\n"
            "The scene renders in a separate OpenGL window.\n"
            "Switch to embedded mode to see it here."
        )
        self._placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        # Wrap, so a narrow (portrait) preview can shrink below the longest line.
        self._placeholder.setWordWrap(True)

        self._stack = QStackedLayout()
        self._stack.addWidget(self._placeholder)
        self._stack.addWidget(self._viewport)
        # The body holds the stacked pages and the spinner; the banner sits above it.
        self._body = QWidget(self)
        self._body.setObjectName("previewBody")
        self._body.setLayout(self._stack)
        self._loading = LoadingOverlay(self._body)
        self._body.installEventFilter(self)
        self._viewport.installEventFilter(self)  # after the body: the filter reads it
        self._banner = ErrorBanner(self)
        self._banner.gotoLineRequested.connect(self.errorGotoLineRequested)
        self._banner.reloadRequested.connect(self.errorReloadRequested)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(header_bar)
        layout.addWidget(self._banner)
        layout.addWidget(self._body, 1)

        self.set_mode(embedded=True)
        self.apply_theme()

    def apply_theme(self, theme: Theme | None = None) -> None:
        """Restyle the preview well and status text from ``theme`` (default: current)."""
        colors = (theme or current_theme()).colors
        well = f"background-color: {colors.preview}; color: {colors.muted};"
        self._viewport.setStyleSheet(well)
        self._placeholder.setStyleSheet(well)

    # ------------------------------------------------------------------
    # State
    # ------------------------------------------------------------------

    def set_mode(self, *, embedded: bool) -> None:
        self._embedded = embedded
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
        return self._cache_text

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

    def set_format_text(self, text: str) -> None:
        """The frame's size chip in the header, for example ``1080x1920 · 9:16``."""
        self._format_label.setText(text)

    def set_cache_status(self, text: str) -> None:
        """Small filmstrip indicator ('cache: 312 frames…')."""
        self._cache_text = text
        self._title_label.setToolTip(text)

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
        self._place_loading()
        super().resizeEvent(event)

    def show_error(self, message: str, line: int | None) -> None:
        """Name the error over the preview; the last good frame stays underneath."""
        self._banner.show_error(message, line)

    def clear_error(self) -> None:
        self._banner.clear()

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if watched is self._body and event.type() == QEvent.Type.Resize:
            self._place_loading()
        elif watched is self._viewport:
            if (
                isinstance(event, QMouseEvent)
                and event.type() == QEvent.Type.MouseButtonPress
                and event.button() == Qt.MouseButton.LeftButton
            ):
                self.playToggleRequested.emit()
                return True
            if isinstance(event, QWheelEvent):
                return self._wheel_step(event.angleDelta().y())
        return super().eventFilter(watched, event)

    def _wheel_step(self, delta: int) -> bool:
        """Step one frame per notch of wheel travel; wheel up steps back, as the timeline does.

        Trackpads send many small deltas, so travel is summed until it makes a notch.
        """
        self._wheel_travel += delta
        notches = int(self._wheel_travel / self.WHEEL_NOTCH)
        if notches:
            self._wheel_travel -= notches * self.WHEEL_NOTCH
            self.frameStepRequested.emit(-notches)
        return True

    def show_loading(self, text: str) -> None:
        """Spinner over the body; the last frame, if any, stays underneath, dimmed."""
        self._loading.set_caption(text, veil=self._image is not None)

    def hide_loading(self) -> None:
        self._loading.clear()

    def _place_loading(self) -> None:
        self._loading.setGeometry(self._body.rect())

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
