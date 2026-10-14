"""The timeline operations as Qt actions: one set shared by the toolbar, menus, shortcuts and
the command palette.

Each operation is a single :class:`QAction`, so its name, icon, shortcut and enabled state
are the same wherever it appears.  Enabled states come from
:func:`manim_studio.timeline_ops.op_states`; this module only applies them.
"""

from __future__ import annotations

from collections.abc import Mapping

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtGui import QAction, QKeyEvent, QKeySequence
from PySide6.QtWidgets import QWidget

from manim_studio.editor.glyphs import glyph
from manim_studio.timeline_ops import OPS, OPS_BY_KEY, OpState, tooltip_for

__all__ = ["PALETTE_GROUP", "TimelineOpActions"]

#: The command palette lists these under this group.
PALETTE_GROUP = "Timeline"


class TimelineOpActions(QObject):
    """One action per timeline operation.

    ``requested`` carries the operation's key when it runs.  ``unavailable`` carries a
    sentence saying why a shortcut was pressed for an operation that is off.
    """

    requested = Signal(str)
    unavailable = Signal(str)

    def __init__(self, window: QWidget) -> None:
        super().__init__(window)
        self._window = window
        self._actions: dict[str, QAction] = {}
        self._reasons: dict[str, str] = {}  # why each operation is off (empty when on)
        self._areas: tuple[QWidget, ...] = ()  # the widgets whose focus takes the timeline keys
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

    def attach_to_timeline(self, *widgets: QWidget) -> None:
        """Bind the operations whose keys work only while one of ``widgets`` has focus.

        Pass the timeline and its toolbar.  The code editor is not among them: it keeps
        Delete and Backspace for typing while it has focus.
        """
        self._areas = widgets
        for key, action in self._actions.items():
            if OPS_BY_KEY[key].scope == "timeline":
                for widget in widgets:
                    widget.addAction(action)

    def apply(self, states: Mapping[str, OpState]) -> None:
        """Enable or disable each operation, and say why in its tooltip when it is off."""
        for key, action in self._actions.items():
            state = states.get(key, OpState(False, "not available"))
            action.setEnabled(state.enabled)
            action.setToolTip(tooltip_for(OPS_BY_KEY[key], state))
            self._reasons[key] = state.reason

    def reason(self, key: str) -> str:
        """Why the operation is unavailable right now; empty when it is available."""
        return self._reasons.get(key, "")

    def explain_unavailable(self, focus: QWidget | None, event: QKeyEvent) -> None:
        """Say why a shortcut did nothing: its key reached the window but its operation is off.

        The window calls this for keys that no widget took, so a key the code editor
        handles never gets here.  Timeline keys count only while the timeline (or its
        toolbar) has focus, because the code editor owns Delete and Backspace.
        """
        if focus is None or focus.window() is not self._window or not self._window.isActiveWindow():
            return
        pressed = QKeySequence(event.keyCombination())
        for key, action in self._actions.items():
            op = OPS_BY_KEY[key]
            if action.isEnabled() or not op.shortcut or op.scope == "display":
                continue
            if op.scope == "timeline" and not self._in_timeline_area(focus):
                continue
            if action.shortcut() == pressed:
                reason = self._reasons.get(key) or "not available right now"
                self.unavailable.emit(f"{op.title}: {reason}")
                return

    def _in_timeline_area(self, widget: QWidget) -> bool:
        return any(area is widget or area.isAncestorOf(widget) for area in self._areas)
