"""A one-line label that never forces its window wider (text is elided, tooltip has it all).

A plain ``QLabel`` asks its layout for the *full width of its text* as a minimum.  When the
text comes from the scene or the host (animation lists, error messages, paths) that
minimum can be thousands of pixels: the main window then grows past the screen, the
splitter hands most of that width to the editor and the preview panel ends up off screen.
Use this label for any text whose length is not under the editor's control.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import QSize, Qt
from PySide6.QtWidgets import QLabel, QSizePolicy

if TYPE_CHECKING:
    from PySide6.QtGui import QResizeEvent
    from PySide6.QtWidgets import QWidget

__all__ = ["ElidedLabel"]


class ElidedLabel(QLabel):
    """Plain-text label that shows as much of its text as fits and elides the rest.

    ``text()`` still returns the full string; when the display is shortened the tooltip
    shows the whole text (after any tooltip set with :meth:`setToolTip`).
    """

    def __init__(
        self,
        text: str = "",
        parent: QWidget | None = None,
        *,
        mode: Qt.TextElideMode = Qt.TextElideMode.ElideRight,
        max_hint: int = 320,
    ) -> None:
        super().__init__("", parent)
        self._full = ""
        self._base_tip = ""
        self._mode = mode
        self._max_hint = max_hint
        self.setTextFormat(Qt.TextFormat.PlainText)
        # Preferred, but with a zero minimum and a capped preferred width (below): the label
        # gets a real slot in a layout without ever being able to demand a wide window.
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)
        self.setText(text)

    def text(self) -> str:
        return self._full

    def setText(self, text: str) -> None:
        changed = text != self._full
        self._full = text
        self._refresh()
        if changed:
            self.updateGeometry()  # the preferred width follows the full text

    def setToolTip(self, tip: str) -> None:
        self._base_tip = tip
        self._refresh()

    def minimumSizeHint(self) -> QSize:
        return QSize(0, super().minimumSizeHint().height())

    def sizeHint(self) -> QSize:
        # Based on the *full* text (not the elided display, which would shrink as the
        # layout shrinks it) but capped, so a huge text cannot ask for a huge slot.
        base = super().sizeHint()
        wanted = self.fontMetrics().horizontalAdvance(self._full) + self._chrome(base.width())
        return QSize(min(wanted, self._max_hint), base.height())

    def _chrome(self, hint_width: int) -> int:
        """Width the style adds around the text (padding, margins, indent)."""
        return max(0, hint_width - self.fontMetrics().horizontalAdvance(super().text()))

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        self._refresh()

    def _refresh(self) -> None:
        width = self.width() - self._chrome(super().sizeHint().width())
        shown = self._full
        metrics = self.fontMetrics()
        # Check the fit ourselves: elidedText can shorten text that fits exactly.
        if self.width() > 0 and metrics.horizontalAdvance(self._full) > width:
            shown = metrics.elidedText(self._full, self._mode, max(width, 0))
        if shown != super().text():
            super().setText(shown)
        elided = shown != self._full
        if elided and self._base_tip:
            tip = f"{self._base_tip}\n\n{self._full}"
        elif elided:
            tip = self._full
        else:
            tip = self._base_tip
        if tip != self.toolTip():
            super().setToolTip(tip)
