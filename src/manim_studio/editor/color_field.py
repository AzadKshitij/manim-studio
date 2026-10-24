"""Colour widgets for the inspector: one colour, and a list of them for gradients (ADR 0012).

A :class:`ColorField` is a painted chip, a hex box and a picker button.  A :class:`ColorListField`
stacks colour fields with a remove button each and an "add a colour" button at the bottom.  Both
only report changes; the inspector decides what they mean in source.
"""

from __future__ import annotations

import re

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPaintEvent, QPen
from PySide6.QtWidgets import (
    QColorDialog,
    QHBoxLayout,
    QLineEdit,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

__all__ = ["ColorChip", "ColorField", "ColorListField"]

CHIP_SIZE = 16
_HEX = re.compile(r"#[0-9A-Fa-f]{6}")


class ColorChip(QWidget):
    """A small square painted in one colour."""

    def __init__(self, hex_value: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._colour = QColor(hex_value)
        self.setFixedSize(CHIP_SIZE, CHIP_SIZE)

    def set_colour(self, hex_value: str) -> None:
        self._colour = QColor(hex_value)
        self.update()

    def paintEvent(self, _event: QPaintEvent) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QPen(QColor(128, 128, 128, 200), 1))
        painter.setBrush(self._colour)
        painter.drawRoundedRect(self.rect().adjusted(0, 0, -1, -1), 3, 3)
        painter.end()


class ColorField(QWidget):
    """One colour as ``#RRGGBB``.  ``changed`` carries the new value when the user changes it."""

    changed = Signal(str)

    def __init__(self, hex_value: str = "#FFFFFF", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._hex = hex_value.upper()
        self._chip = ColorChip(self._hex, self)
        self._edit = QLineEdit(self._hex, self)
        self._edit.setMaxLength(7)
        self._edit.setFixedWidth(84)
        self._edit.setToolTip("Hex colour, for example #58C4DD")
        self._edit.setAccessibleName("Hex colour")
        self._pick = QToolButton(self)
        self._pick.setText("…")
        self._pick.setToolTip("Pick a colour")
        self._pick.setAccessibleName("Pick a colour")
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(4)
        row.addWidget(self._chip)
        row.addWidget(self._edit)
        row.addWidget(self._pick)
        row.addStretch(1)
        self._edit.editingFinished.connect(self._on_text_edited)
        self._pick.clicked.connect(self._on_pick)

    def value(self) -> str:
        return self._hex

    def set_value(self, hex_value: str) -> None:
        """Show ``hex_value`` without reporting a change."""
        self._apply(hex_value.upper(), emit=False)

    def _apply(self, hex_value: str, *, emit: bool) -> None:
        if hex_value == self._hex:
            return
        self._hex = hex_value
        self._chip.set_colour(hex_value)
        self._edit.setText(hex_value)
        if emit:
            self.changed.emit(hex_value)

    def _on_text_edited(self) -> None:
        text = self._edit.text().strip().upper()
        if _HEX.fullmatch(text):
            self._apply(text, emit=True)
        else:
            self._edit.setText(self._hex)  # not a colour: go back to the last good one

    def _on_pick(self) -> None:
        colour = QColorDialog.getColor(QColor(self._hex), self, "Colour")
        if colour.isValid():
            self._apply(colour.name().upper(), emit=True)


class ColorListField(QWidget):
    """An ordered list of colours with a remove button per row and an "add" button below."""

    changed = Signal()

    def __init__(self, values: list[str], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._column = QVBoxLayout(self)
        self._column.setContentsMargins(0, 0, 0, 0)
        self._column.setSpacing(2)
        self._fields: list[ColorField] = []
        self._rows: list[QWidget] = []
        self._add = QToolButton(self)
        self._add.setText("+")
        self._add.setToolTip("Add a colour")
        self._add.setAccessibleName("Add a colour")
        self._add.clicked.connect(self._on_add)
        self._column.addWidget(self._add, 0, Qt.AlignmentFlag.AlignLeft)
        for value in values or ["#FFFFFF"]:
            self._append(value)

    def values(self) -> list[str]:
        return [field.value() for field in self._fields]

    def _append(self, value: str) -> None:
        field = ColorField(value)
        field.changed.connect(lambda _hex: self.changed.emit())
        row = QWidget(self)
        line = QHBoxLayout(row)
        line.setContentsMargins(0, 0, 0, 0)
        line.setSpacing(4)
        line.addWidget(field)
        remove = QToolButton(row)
        remove.setText("x")
        remove.setToolTip("Remove this colour")
        remove.setAccessibleName("Remove this colour")
        remove.clicked.connect(lambda _checked=False, r=row: self._remove(r))
        line.addWidget(remove)
        self._fields.append(field)
        self._rows.append(row)
        self._column.insertWidget(len(self._rows) - 1, row)

    def _remove(self, row: QWidget) -> None:
        if len(self._rows) <= 1:
            return  # a gradient keeps at least one colour
        index = self._rows.index(row)
        self._rows.pop(index)
        self._fields.pop(index)
        self._column.removeWidget(row)
        row.deleteLater()
        self.changed.emit()

    def _on_add(self) -> None:
        last = self._fields[-1].value() if self._fields else "#FFFFFF"
        self._append(last)
        self.changed.emit()
