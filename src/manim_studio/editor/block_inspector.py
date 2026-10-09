"""Editable details of the selected timeline block.

A slim panel under the timeline: the block's name, an editable duration, the scope of
that edit for loop bodies (all iterations or just this one), how the duration is
written in the source, and the block's animations, source line and edit state.  The
widget only emits signals; :class:`~manim_studio.editor.main_window.MainWindow` turns
them into timeline edits.
"""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import Signal, SignalInstance
from PySide6.QtWidgets import (
    QComboBox,
    QDoubleSpinBox,
    QHBoxLayout,
    QLabel,
    QToolButton,
    QWidget,
)

from manim_studio.editor.glyphs import icon_button

__all__ = ["BlockInfo", "BlockInspector"]

MIN_DURATION = 1.0 / 30.0

#: How the call spells its duration in the source -> the note shown to the user.
_KIND_NOTES = {
    "literal": "",
    "absent": "no explicit duration in the source: an edit adds one",
    "expr": "duration is an expression: an edit replaces it with a number",
}


@dataclass(frozen=True, slots=True)
class BlockInfo:
    """Everything the inspector shows for one block."""

    index: int
    name: str
    duration: float
    line: int | None = None
    animations: str = ""
    loops: int = 1
    edited: bool = False
    duration_kind: str | None = None  #: ``"literal"``, ``"absent"``, ``"expr"`` or unknown
    editable: bool = False  #: the block has a known call site


class BlockInspector(QWidget):
    """Slim, editable inspector for the selected block."""

    #: ``(block index, seconds, this iteration only)``
    durationCommitted = Signal(int, float, bool)
    jumpRequested = Signal(int)
    resetRequested = Signal(int)
    deleteRequested = Signal(int)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._info: BlockInfo | None = None
        self.title = QLabel("", self)
        self.title.setMinimumWidth(120)

        self.duration_spin = QDoubleSpinBox(self)
        self.duration_spin.setDecimals(3)
        self.duration_spin.setRange(MIN_DURATION, 3600.0)
        self.duration_spin.setSingleStep(0.05)
        self.duration_spin.setSuffix(" s")
        self.duration_spin.setKeyboardTracking(False)
        self.duration_spin.setToolTip("Duration of this block (Enter to apply)")
        self.duration_spin.editingFinished.connect(self._commit)

        self.scope = QComboBox(self)
        self.scope.addItem("All iterations", False)
        self.scope.addItem("This iteration", True)
        self.scope.setToolTip(
            "A loop body runs several times: edit every iteration or just this one"
        )

        self.note = QLabel("", self)
        self.note.setStyleSheet("color: #b8860b;")
        self.details = QLabel("", self)
        self.details.setToolTip("Animations, source line and edit state of the selected block")

        self.jump_button = icon_button("jump_to_code", "Jump to the block's source line", self)
        self.reset_button = icon_button("reset", "Reset this block's timeline edits", self)
        self.delete_button = icon_button(
            "delete", "Delete this block's call (a timeline edit)", self
        )
        self.jump_button.clicked.connect(lambda: self._emit(self.jumpRequested))
        self.reset_button.clicked.connect(lambda: self._emit(self.resetRequested))
        self.delete_button.clicked.connect(lambda: self._emit(self.deleteRequested))

        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 2, 8, 2)
        layout.setSpacing(6)
        for widget in (
            self.title,
            self.duration_spin,
            self.scope,
            self.jump_button,
            self.reset_button,
            self.delete_button,
            self.note,
        ):
            layout.addWidget(widget)
        layout.addWidget(self.details, 1)
        self.show_block(None)

    # ------------------------------------------------------------------

    @property
    def info(self) -> BlockInfo | None:
        return self._info

    def show_block(self, info: BlockInfo | None) -> None:
        """Display ``info`` (or an empty, disabled inspector for ``None``)."""
        if info == self._info and self.title.text():
            return  # unchanged: do not clobber a half-typed duration
        self._info = info
        empty = info is None
        for widget in (self.duration_spin, self.scope, self.jump_button):
            widget.setEnabled(not empty)
        editable = bool(info and info.editable)
        self.duration_spin.setEnabled(editable)
        self.delete_button.setEnabled(editable)
        self.reset_button.setEnabled(bool(info and info.edited))
        self.jump_button.setEnabled(bool(info and info.line is not None))
        if info is None:
            self.title.setText("")
            self.details.setText("")
            self.note.setText("")
            self.scope.setVisible(False)
            return
        self.title.setText(f"#{info.index}  {info.name}")
        self.duration_spin.blockSignals(True)
        self.duration_spin.setValue(max(info.duration, MIN_DURATION))
        self.duration_spin.blockSignals(False)
        self.scope.setVisible(info.loops > 1)
        self.scope.setCurrentIndex(0)
        self.note.setText(_KIND_NOTES.get(info.duration_kind or "", "") if info.editable else "")
        parts: list[str] = []
        if info.line is not None:
            parts.append(f"line {info.line}")
        if info.animations:
            parts.append(info.animations)
        if info.loops > 1:
            parts.append(f"loop ×{info.loops}")  # noqa: RUF001
        if info.edited:
            parts.append("edited (not in the source)")
        self.details.setText("  ·  ".join(parts))

    # ------------------------------------------------------------------

    def _emit(self, signal: SignalInstance) -> None:
        if self._info is not None:
            signal.emit(self._info.index)

    def _commit(self) -> None:
        info = self._info
        if info is None or not info.editable:
            return
        value = float(self.duration_spin.value())
        if abs(value - info.duration) < 1e-4:
            return  # focus left the field without a change
        self.durationCommitted.emit(info.index, value, bool(self.scope.currentData()))

    def buttons(self) -> tuple[QToolButton, ...]:
        return (self.jump_button, self.reset_button, self.delete_button)
