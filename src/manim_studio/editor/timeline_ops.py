"""The timeline operations as Qt actions: one set shared by the toolbar, menus, shortcuts and
the command palette.

Each operation is a single :class:`QAction`, so its name, icon, shortcut and enabled state
are the same wherever it appears.  Enabled states come from
:func:`manim_studio.timeline_ops.op_states`; this module only applies them.
"""

from __future__ import annotations

from collections.abc import Mapping

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import QWidget

from manim_studio.editor.glyphs import glyph
from manim_studio.timeline_ops import OPS, OPS_BY_KEY, OpState, tooltip_for

__all__ = ["PALETTE_GROUP", "TimelineOpActions"]

#: The command palette lists these under this group.
PALETTE_GROUP = "Timeline"


class TimelineOpActions(QObject):
    """One action per timeline operation; ``requested`` carries the operation's key."""

    requested = Signal(str)

    def __init__(self, window: QWidget) -> None:
        super().__init__(window)
        self._window = window
        self._actions: dict[str, QAction] = {}
        for op in OPS:
            action = QAction(op.title, window)
            action.setIcon(glyph(op.glyph))
            action.setToolTip(tooltip_for(op))
            action.setEnabled(False)  # the window applies the real states (see apply)
            action.setProperty("palette_group", PALETTE_GROUP)
            action.setProperty("operation", op.key)
            if op.scope == "display":
                # The timeline handles this key itself; the action only shows it in menus.
                action.setProperty("shortcut_text", op.shortcut)
                action.setText(f"{op.title}\t{op.shortcut}")
            elif op.shortcut:
                action.setShortcut(QKeySequence(op.shortcut))
            action.setShortcutContext(
                Qt.ShortcutContext.WidgetWithChildrenShortcut
                if op.scope == "timeline"
                else Qt.ShortcutContext.WindowShortcut
            )
            action.triggered.connect(lambda _checked=False, key=op.key: self.requested.emit(key))
            self._actions[op.key] = action

    @property
    def actions(self) -> dict[str, QAction]:
        """Every operation's action, keyed by operation."""
        return dict(self._actions)

    def action(self, key: str) -> QAction:
        return self._actions[key]

    def attach_to_window(self) -> None:
        """Bind the operations whose shortcuts work anywhere in the editor."""
        for key, action in self._actions.items():
            if OPS_BY_KEY[key].scope == "window":
                self._window.addAction(action)

    def attach_to_timeline(self, timeline: QWidget) -> None:
        """Bind the operations whose shortcuts work only while the timeline has focus."""
        for key, action in self._actions.items():
            if OPS_BY_KEY[key].scope == "timeline":
                timeline.addAction(action)

    def apply(self, states: Mapping[str, OpState]) -> None:
        """Enable or disable each operation, and say why in its tooltip when it is off."""
        for key, action in self._actions.items():
            state = states.get(key, OpState(False, "not available"))
            action.setEnabled(state.enabled)
            action.setToolTip(tooltip_for(OPS_BY_KEY[key], state))
