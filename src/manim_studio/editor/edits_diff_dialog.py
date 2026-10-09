"""Read-only view of what applying the timeline edits would change in the source."""

from __future__ import annotations

import difflib
from typing import TYPE_CHECKING

from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QLabel,
    QPlainTextEdit,
    QVBoxLayout,
    QWidget,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

__all__ = ["EditsDiffDialog", "unified_diff"]


def unified_diff(before: str, after: str, name: str) -> str:
    """A unified diff of ``before`` -> ``after`` (empty when identical)."""
    lines = difflib.unified_diff(
        before.splitlines(keepends=True),
        after.splitlines(keepends=True),
        fromfile=f"{name} (source)",
        tofile=f"{name} (with timeline edits)",
    )
    return "".join(lines)


class EditsDiffDialog(QDialog):
    """Shows the diff and the edits that cannot be written to the source."""

    def __init__(
        self,
        diff: str,
        notes: Sequence[str] = (),
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Timeline edits")
        self.resize(720, 460)
        layout = QVBoxLayout(self)
        layout.addWidget(
            QLabel("This is what 'Apply timeline edits to source' would change in the file:")
        )
        self.view = QPlainTextEdit(self)
        self.view.setReadOnly(True)
        font = QFont("Consolas")
        font.setStyleHint(QFont.StyleHint.Monospace)
        self.view.setFont(font)
        self.view.setPlainText(diff or "(no changes)")
        layout.addWidget(self.view, 1)
        if notes:
            note = QLabel("\n".join(f"• {text}" for text in notes))
            note.setWordWrap(True)
            layout.addWidget(note)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close, self)
        buttons.rejected.connect(self.reject)
        buttons.accepted.connect(self.accept)
        layout.addWidget(buttons)
