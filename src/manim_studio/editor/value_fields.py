"""Value widgets for the inspector beyond one number or one colour (ADR 0012).

A :class:`VectorField` is a row of number boxes (a point, a range).  A :class:`DictField` edits a
dict whose keys are strings, one row per entry, with a colour picker per value when the values are
colours (``t2c={"x": BLUE}``).  Both only report changes; the inspector turns their state into
source text.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractSpinBox,
    QDoubleSpinBox,
    QHBoxLayout,
    QLineEdit,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from manim_studio.call_edits import format_colour
from manim_studio.editor.color_field import ColorField
from manim_studio.mobject_schema import resolve_colour

__all__ = ["DictField", "VectorField"]

_LIMIT = 1_000_000.0


class VectorField(QWidget):
    """A fixed number of number boxes.  ``changed`` fires when any of them changes."""

    changed = Signal()

    def __init__(self, values: Sequence[float], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(4)
        self._boxes: list[QDoubleSpinBox] = []
        for value in values:
            box = QDoubleSpinBox(self)
            # Compact: three boxes must fit a narrow panel, so no arrows and a small minimum.
            box.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
            box.setMinimumWidth(52)
            box.setDecimals(4)
            box.setRange(-_LIMIT, _LIMIT)
            box.setSingleStep(0.1)
            box.setValue(float(value))
            box.valueChanged.connect(lambda _value: self.changed.emit())
            row.addWidget(box)
            self._boxes.append(box)

    def values(self) -> list[float]:
        return [box.value() for box in self._boxes]


@dataclass(eq=False)
class _DictRow:
    container: QWidget
    key: QLineEdit
    value: ColorField | QLineEdit
    original: str  #: the value as the source wrote it; kept when a colour is left unchanged

    def value_text(self, colours: Mapping[str, str]) -> str:
        if isinstance(self.value, ColorField):
            hex_value = self.value.value()
            if self.original and resolve_colour(self.original, colours) == hex_value:
                return self.original
            return format_colour(hex_value, self.original or '"')
        return self.value.text().strip()


class DictField(QWidget):
    """Rows of ``key: value`` for a dict with string keys, with "+" to add and "x" to remove.

    With ``colour_values`` a value that is a colour gets a colour picker; any other value stays an
    expression box, so a variable or a call is never replaced by a colour.
    """

    changed = Signal()

    def __init__(
        self,
        entries: Sequence[tuple[str, str]],
        colours: Mapping[str, str],
        *,
        colour_values: bool,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._colours = colours
        self._colour_values = colour_values
        self._rows: list[_DictRow] = []
        self._column = QVBoxLayout(self)
        self._column.setContentsMargins(0, 0, 0, 0)
        self._column.setSpacing(2)
        self._add = QToolButton(self)
        self._add.setText("+")
        self._add.setToolTip("Add an entry")
        self._add.setAccessibleName("Add an entry")
        self._add.clicked.connect(self._on_add)
        self._column.addWidget(self._add, 0, Qt.AlignmentFlag.AlignLeft)
        for key, value in entries:
            self._append(key, value)

    def source(self) -> str:
        """The dict as Python source.  Rows with an empty key are left out."""
        parts = [
            f"{json.dumps(row.key.text(), ensure_ascii=False)}: {row.value_text(self._colours)}"
            for row in self._rows
            if row.key.text()
        ]
        return "{" + ", ".join(parts) + "}"

    def entry_count(self) -> int:
        return len(self._rows)

    def _append(self, key: str, value: str) -> None:
        container = QWidget(self)
        line = QHBoxLayout(container)
        line.setContentsMargins(0, 0, 0, 0)
        line.setSpacing(4)
        key_edit = QLineEdit(key, container)
        key_edit.setPlaceholderText("key")
        key_edit.setFixedWidth(96)
        key_edit.textChanged.connect(lambda _text: self.changed.emit())
        editor: ColorField | QLineEdit
        resolved = resolve_colour(value, self._colours) if value else None
        if self._colour_values and (resolved is not None or not value):
            editor = ColorField(resolved or "#FFFFFF", container)
            editor.changed.connect(lambda _hex: self.changed.emit())
        else:
            editor = QLineEdit(value, container)
            editor.setPlaceholderText("value")
            editor.textChanged.connect(lambda _text: self.changed.emit())
        remove = QToolButton(container)
        remove.setText("x")
        remove.setToolTip("Remove this entry")
        remove.setAccessibleName("Remove this entry")
        line.addWidget(key_edit)
        line.addWidget(editor, 1)
        line.addWidget(remove)
        row = _DictRow(container, key_edit, editor, value)
        remove.clicked.connect(lambda _checked=False, r=row: self._remove(r))
        self._rows.append(row)
        self._column.insertWidget(len(self._rows) - 1, container)

    def _remove(self, row: _DictRow) -> None:
        self._rows.remove(row)
        self._column.removeWidget(row.container)
        row.container.deleteLater()
        self.changed.emit()

    def _on_add(self) -> None:
        self._append("", "")
        self.changed.emit()
