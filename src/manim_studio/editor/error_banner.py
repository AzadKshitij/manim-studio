"""A strip above the preview that names the last error and offers the next step."""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QFrame, QHBoxLayout, QPushButton, QWidget

from manim_studio.editor.elided_label import ElidedLabel
from manim_studio.theme import current_theme

__all__ = ["ErrorBanner"]


class ErrorBanner(QFrame):
    """Names a scene or host error, with Go to line (when the error has a line) and Reload.

    Hidden until :meth:`show_error` is called.  The preview clears it once a trace
    succeeds again, so a stale error never sits over a working scene.
    """

    gotoLineRequested = Signal(int)
    reloadRequested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("errorBanner")
        colors = current_theme().colors
        self.setStyleSheet(
            f"QFrame#errorBanner {{ background: {colors.raised}; "
            f"border: 1px solid {colors.err}; border-radius: 6px; }}"
            f"QLabel#errorBannerText {{ color: {colors.text}; }}"
        )
        self._line: int | None = None
        # Error text comes from the scene, so it is elided rather than widening the window.
        self._text = ElidedLabel("", self)
        self._text.setObjectName("errorBannerText")
        self._goto = QPushButton("Go to line", self)
        self._reload = QPushButton("Reload", self)
        self._goto.clicked.connect(self._go_to_line)
        self._reload.clicked.connect(self.reloadRequested)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 6, 8, 6)
        layout.setSpacing(8)
        layout.addWidget(self._text, 1)
        layout.addWidget(self._goto)
        layout.addWidget(self._reload)
        self.hide()

    @property
    def line(self) -> int | None:
        return self._line

    @property
    def message(self) -> str:
        return self._text.text()

    def show_error(self, message: str, line: int | None) -> None:
        self._line = line
        full = f"Line {line} — {message}" if line is not None else message
        self._text.setText(full)
        self._text.setToolTip(full)
        self._goto.setVisible(line is not None)
        self.show()

    def clear(self) -> None:
        self._line = None
        self.hide()

    def _go_to_line(self) -> None:
        if self._line is not None:
            self.gotoLineRequested.emit(self._line)
