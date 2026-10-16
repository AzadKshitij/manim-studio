"""Gear markers beside mobject lines; a click opens the inspector (ADR 0012).

Replaces the colour swatches of ADR 0011.  Every line that binds or configures a mobject
(``circle = Circle(...)``, ``circle.shift(UP)``) gets a gear after its text.  A click re-reads the
target from the buffer and emits :attr:`InspectorGears.targetRequested`; the inspector panel does
the rest.  Gears only appear for classes the discovered schema knows, so ``helper = compute()``
never gets one.
"""

from __future__ import annotations

from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtGui import QMouseEvent, QPainter, QPaintEvent
from PySide6.QtWidgets import QWidget

from manim_studio.editor.code_editor import CodeEditor
from manim_studio.editor.glyphs import glyph
from manim_studio.mobject_schema import MobjectSchema, lookup
from manim_studio.mobject_sites import MobjectTarget, find_mobject_targets

__all__ = ["GEAR_SIZE", "GearButton", "InspectorGears"]

GEAR_SIZE = 14
GEAR_GAP = 6
GEAR_STEP = 20  #: horizontal spacing between gears on one line
RESCAN_MS = 150  #: after an edit, before the source is scanned again


class GearButton(QWidget):
    """A fixed-size gear drawn directly.

    Not a ``QPushButton``: the app-wide stylesheet gives push buttons a minimum width and
    height, which would make the gears spill over the next line.
    """

    clicked = Signal()

    def __init__(self, tooltip: str, parent: QWidget) -> None:
        super().__init__(parent)
        self._icon = glyph("gear")
        self.setFixedSize(GEAR_SIZE, GEAR_SIZE)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setToolTip(tooltip)

    def click(self) -> None:
        """Emit ``clicked`` as a real click would (used by tests)."""
        self.clicked.emit()

    def paintEvent(self, _event: QPaintEvent) -> None:
        painter = QPainter(self)
        self._icon.paint(painter, self.rect())
        painter.end()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton and self.rect().contains(
            event.position().toPoint()
        ):
            self.clicked.emit()


class InspectorGears(QObject):
    """Owns the gears for one ``CodeEditor``."""

    targetRequested = Signal(object)  #: a fresh :class:`MobjectTarget`

    def __init__(self, editor: CodeEditor) -> None:
        super().__init__(editor)
        self._editor = editor
        self._schema: MobjectSchema | None = None
        self._targets: tuple[MobjectTarget, ...] = ()
        self._entries: list[tuple[MobjectTarget, int]] = []  #: (target, 1-based line)
        self._buttons: list[GearButton] = []

        self._rescan_timer = QTimer(self)
        self._rescan_timer.setSingleShot(True)
        self._rescan_timer.setInterval(RESCAN_MS)
        self._rescan_timer.timeout.connect(self.rescan)

        editor.modified.connect(self._on_modified)
        editor.updateUi.connect(self._on_update_ui)
        self.rescan()

    @property
    def editor(self) -> CodeEditor:
        return self._editor

    @property
    def buttons(self) -> tuple[GearButton, ...]:
        return tuple(self._buttons)

    @property
    def targets(self) -> tuple[MobjectTarget, ...]:
        """The targets with a gear, in source order."""
        return self._targets

    def set_schema(self, schema: MobjectSchema | None) -> None:
        """The discovered schema decides which classes get gears.  ``None`` hides them all."""
        self._schema = schema
        self.rescan()

    def rescan(self) -> None:
        """Re-read the buffer and rebuild the gears.  Broken source shows none."""
        self._targets = self._known_targets()
        self._entries = [(target, line) for target in self._targets for line in target.lines]
        self._rebuild()

    def _known_targets(self) -> tuple[MobjectTarget, ...]:
        if self._schema is None:
            return ()
        try:
            targets = find_mobject_targets(self._editor.text())
        except SyntaxError:
            return ()
        return tuple(t for t in targets if lookup(self._schema, t.cls) is not None)

    def _rebuild(self) -> None:
        for button in self._buttons:
            button.deleteLater()
        self._buttons = []
        for target, _line in self._entries:
            tooltip = f"{target.var} · {target.cls}: open the inspector"
            button = GearButton(tooltip, self._editor)
            button.setAccessibleName(f"Inspect {target.var}")
            button.clicked.connect(lambda t=target: self._request(t))
            button.show()
            self._buttons.append(button)
        self._reposition()

    def _reposition(self) -> None:
        """Place gears after their line's text, in source order on each line."""
        editor = self._editor
        next_slot: dict[int, int] = {}
        for (_target, line_number), button in zip(self._entries, self._buttons, strict=True):
            line = line_number - 1
            slot = next_slot.get(line, 0)
            next_slot[line] = slot + 1
            line_end = editor.lineEndPosition(line)
            x = editor.pointXFromPosition(line_end) + GEAR_GAP + slot * GEAR_STEP
            top = editor.pointYFromPosition(editor.positionFromLine(line))
            height = editor.textHeight(line)
            button.move(x, top + (height - GEAR_SIZE) // 2)
            on_screen = top >= 0 and top + height <= editor.height() and x < editor.width()
            button.setVisible(on_screen)

    def _on_modified(self, *_args: object) -> None:
        self._rescan_timer.start()

    def _on_update_ui(self, *_args: object) -> None:
        # Scrolling and caret moves do not change the source, so only positions are updated.
        self._reposition()

    def _request(self, target: MobjectTarget) -> None:
        """Emit the target as it reads now; a click on a stale gear rescans instead."""
        if self._schema is None:
            return
        try:
            current = find_mobject_targets(self._editor.text())
        except SyntaxError:
            return
        fresh = next((t for t in current if t.identity == target.identity), None)
        if fresh is None:
            self.rescan()
            return
        self.targetRequested.emit(fresh)
