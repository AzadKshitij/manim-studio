"""The chooser shown above the code when the scene changed on disk and the editor has edits."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QWidget

__all__ = ["ExternalChangeBar"]


class ExternalChangeBar(QFrame):
    """Keep mine · Take theirs · Show diff.  Hidden until a change needs a decision."""

    keepRequested = Signal()
    takeRequested = Signal()
    diffRequested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("externalChangeBar")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._message = QLabel(self)
        self._message.setWordWrap(True)
        keep = QPushButton("Keep mine", self)
        take = QPushButton("Take theirs", self)
        diff = QPushButton("Show diff", self)
        keep.setToolTip("Keep the edits in the editor; saving overwrites the file on disk")
        take.setToolTip("Show the file from disk (Ctrl+Z brings your edits back)")
        diff.setToolTip("Show what taking the file from disk would change")
        keep.clicked.connect(self._keep)
        take.clicked.connect(self._take)
        diff.clicked.connect(self._show_diff)
        row = QHBoxLayout(self)
        row.setContentsMargins(12, 4, 12, 4)
        row.addWidget(self._message, 1)
        for button in (diff, keep, take):
            row.addWidget(button)
        self.hide()

    def show_change(self, name: str) -> None:
        self._message.setText(f"{name} changed on disk while you have unsaved edits.")
        self.show()

    def dismiss(self) -> None:
        self.hide()

    def _keep(self) -> None:
        self.dismiss()
        self.keepRequested.emit()

    def _take(self) -> None:
        self.dismiss()
        self.takeRequested.emit()

    def _show_diff(self) -> None:
        self.diffRequested.emit()
