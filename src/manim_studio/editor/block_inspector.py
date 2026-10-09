"""Editable details of the selected timeline block.

A slim panel under the timeline: the block's name, an editable duration, the scope of
that edit for loop bodies (all iterations or just this one), how the duration is
written in the source, and the block's animations, source line and edit state.  The
widget only emits signals; :class:`~manim_studio.editor.main_window.MainWindow` turns
them into timeline edits.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QComboBox,
    QDoubleSpinBox,
    QHBoxLayout,
    QLabel,
    QWidget,
)

from manim_studio.editor.elided_label import ElidedLabel
from manim_studio.theme import current_theme

__all__ = ["BlockInfo", "BlockInspector", "describe_clips"]

MIN_DURATION = 1.0 / 30.0


def describe_clips(clips: Iterable[Any]) -> str:
    """``"Write(Text) 2.77s"`` for a block's animations, with repeats grouped.

    A grid animating 72 squares at once reads ``Animate(Square) x72 1.00s`` instead of
    seventy-two identical entries.
    """
    groups: dict[tuple[str, str, str], int] = {}
    for clip in clips:
        key = (clip.animation, clip.target, f"{clip.duration:.2f}")
        groups[key] = groups.get(key, 0) + 1
    parts = []
    for (animation, target, duration), count in groups.items():
        repeat = f" ×{count}" if count > 1 else ""  # noqa: RUF001
        parts.append(f"{animation}({target}){repeat} {duration}s")
    return ", ".join(parts)


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

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._info: BlockInfo | None = None
        self.title = ElidedLabel("", self)
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
        self.apply_theme()
        # The animation list can be thousands of characters (a 72-square grid): it must
        # never decide how wide the window has to be.
        self.details = ElidedLabel("", self)
        self.details.setToolTip("Animations, source line and edit state of the selected block")

        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 2, 8, 2)
        layout.setSpacing(6)
        for widget in (
            self.title,
            self.duration_spin,
            self.scope,
            self.note,
        ):
            layout.addWidget(widget)
        layout.addWidget(self.details, 1)
        self.show_block(None)

    # ------------------------------------------------------------------

    def apply_theme(self) -> None:
        """Recolour the warning note from the current theme."""
        self.note.setStyleSheet(f"color: {current_theme().colors.warn};")

    @property
    def info(self) -> BlockInfo | None:
        return self._info

    def show_block(self, info: BlockInfo | None) -> None:
        """Display ``info`` (or an empty, disabled inspector for ``None``)."""
        if info == self._info and self.title.text():
            return  # unchanged: do not clobber a half-typed duration
        self._info = info
        empty = info is None
        self.scope.setEnabled(not empty)
        self.duration_spin.setEnabled(bool(info and info.editable))
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

    def _commit(self) -> None:
        info = self._info
        if info is None or not info.editable:
            return
        value = float(self.duration_spin.value())
        if abs(value - info.duration) < 1e-4:
            return  # focus left the field without a change
        self.durationCommitted.emit(info.index, value, bool(self.scope.currentData()))
