"""Toolbar above the timeline, in labelled groups.

Transport, zoom and the snap indicator, then the timeline operations: edit, and apply
(to the source, diff, and the discards).  The operations are shared actions (see
:mod:`manim_studio.editor.timeline_ops`); the buttons here follow their enabled state and
show their shortcut in the tooltip.  Wide windows label the key buttons and the groups;
narrow ones drop the labels so every operation stays one click away.  The widget only
emits signals; :class:`~manim_studio.editor.main_window.MainWindow` acts on them.
"""

from __future__ import annotations

from collections.abc import Mapping

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QAction, QResizeEvent, QShowEvent
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QToolButton,
    QWidget,
)

from manim_studio.editor.glyphs import glyph, icon_button
from manim_studio.timeline_ops import OPS, OPS_BY_KEY, OpState, tooltip_for

__all__ = ["EDIT_ACTIONS", "SPEEDS", "TimelineTransport"]

#: Playback-speed choices offered in the selector (1.0 = scene speed).
SPEEDS: tuple[float, ...] = (0.25, 0.5, 0.75, 1.0, 1.5, 2.0)

#: Timeline operations, in toolbar order: ``(id, glyph, tooltip)``.
EDIT_ACTIONS: tuple[tuple[str, str, str], ...] = tuple(
    (op.key, op.glyph, tooltip_for(op)) for op in OPS
)

_CAPTIONS = (
    ("transport", "Transport"),
    ("zoom", "Zoom"),
    ("edit", "Edit"),
    ("apply", "Apply"),
)


def _separator(parent: QWidget) -> QFrame:
    line = QFrame(parent)
    line.setFrameShape(QFrame.Shape.VLine)
    line.setFrameShadow(QFrame.Shadow.Sunken)
    return line


class TimelineTransport(QWidget):
    """Timeline toolbar: transport, zoom, snap and timeline operations."""

    playPauseRequested = Signal()
    stepRequested = Signal(int)  # -1 or +1 frame
    jumpRequested = Signal(str)  # "start" | "end"
    loopToggled = Signal(bool)
    speedChanged = Signal(float)
    zoomRequested = Signal(str)  # "fit" | "selection" | "in" | "out"
    editRequested = Signal(str)  # one of the ids in EDIT_ACTIONS

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._playing = False
        self._compact: bool | None = None
        self._labelled_min = 0  # the width the labelled bar needs (see _measure)
        self.setObjectName("dockBar")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setFixedHeight(40)
        self._start_button = icon_button("start", "Jump to the beginning  (Home)", self)
        self._back_button = icon_button("step_back", "Step back one frame  (Left)", self)
        self._play_button = icon_button("play", "Play  (Space)", self)
        self._forward_button = icon_button("step_forward", "Step forward one frame  (Right)", self)
        self._end_button = icon_button("end", "Jump to the end  (End)", self)
        self._loop_check = icon_button(
            "loop", "Loop the selected range (or the whole scene)", self, checkable=True
        )

        self._start_button.clicked.connect(lambda: self.jumpRequested.emit("start"))
        self._back_button.clicked.connect(lambda: self.stepRequested.emit(-1))
        self._play_button.clicked.connect(self.playPauseRequested)
        self._forward_button.clicked.connect(lambda: self.stepRequested.emit(1))
        self._end_button.clicked.connect(lambda: self.jumpRequested.emit("end"))
        self._loop_check.toggled.connect(self.loopToggled)

        self._speed_combo = QComboBox(self)
        for speed in SPEEDS:
            self._speed_combo.addItem(f"{speed:g}x", speed)
        self._speed_combo.setCurrentIndex(SPEEDS.index(1.0))
        self._speed_combo.setToolTip("Playback speed (1x = the scene's own timing)")
        self._speed_combo.currentIndexChanged.connect(self._emit_speed)

        self._fit_button = icon_button("fit", "Fit the whole timeline  (Ctrl+0)", self)
        self._selection_button = icon_button(
            "fit_selection", "Zoom to the selected range (or the selected block)", self
        )
        self._zoom_out_button = icon_button("zoom_out", "Zoom out  (-)", self)
        self._zoom_in_button = icon_button("zoom_in", "Zoom in  (+)", self)
        self._fit_button.clicked.connect(lambda: self.zoomRequested.emit("fit"))
        self._selection_button.clicked.connect(lambda: self.zoomRequested.emit("selection"))
        self._zoom_out_button.clicked.connect(lambda: self.zoomRequested.emit("out"))
        self._zoom_in_button.clicked.connect(lambda: self.zoomRequested.emit("in"))

        #: The snap indicator; :meth:`bind_snap_action` ties it to View > Snap timeline.
        self._snap_button = icon_button(
            "snap",
            "Snap to frames, 0.1 s and block edges  (View > Snap timeline)",
            self,
            checkable=True,
        )

        self._edit_buttons: dict[str, QToolButton] = {}
        self._labelled: list[QToolButton] = []
        for op in OPS:
            button = icon_button(op.glyph, tooltip_for(op), self)
            if op.label:
                button.setText(op.label)
                self._labelled.append(button)
            button.setEnabled(False)
            button.clicked.connect(lambda _checked=False, a=op.key: self.editRequested.emit(a))
            self._edit_buttons[op.key] = button

        self._captions: dict[str, QLabel] = {}
        for name, text in _CAPTIONS:
            caption = QLabel(text, self)
            caption.setObjectName("toolbarCaption")
            self._captions[name] = caption

        #: "3 timeline edits (1 orphaned)"; hidden while there are none.
        self.edits_label = QLabel("", self)
        self.edits_label.setToolTip(
            "Timeline edits live in a sidecar file next to the scene, not in the source.\n"
            "Apply them to the source with the check button."
        )
        self.edits_label.setVisible(False)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 2, 4, 2)
        layout.setSpacing(2)
        layout.addWidget(self._captions["transport"])
        for widget in (
            self._start_button,
            self._back_button,
            self._play_button,
            self._forward_button,
            self._end_button,
            self._loop_check,
        ):
            layout.addWidget(widget)
        layout.addWidget(self._speed_combo)
        layout.addSpacing(6)
        layout.addWidget(_separator(self))
        layout.addWidget(self._captions["zoom"])
        for widget in (
            self._fit_button,
            self._selection_button,
            self._zoom_out_button,
            self._zoom_in_button,
            self._snap_button,
        ):
            layout.addWidget(widget)
        layout.addSpacing(6)
        layout.addWidget(_separator(self))
        for group in ("edit", "apply"):
            layout.addWidget(self._captions[group])
            for op in OPS:
                if op.group == group:
                    layout.addWidget(self._edit_buttons[op.key])
            if group == "edit":
                layout.addSpacing(6)
                layout.addWidget(_separator(self))
        layout.addSpacing(6)
        layout.addWidget(self.edits_label)
        layout.addStretch(1)
        # A click on the toolbar gives focus to the bar, which hands it to the timeline (see
        # MainWindow); the buttons stay reachable with Tab but do not take a click themselves.
        self.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
        for button in self.findChildren(QToolButton):
            button.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        self._speed_combo.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        self._measure()

    # ------------------------------------------------------------------
    # Responsive labels

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        self._set_compact(self.width() < self._labelled_min)

    def showEvent(self, event: QShowEvent) -> None:
        self._measure()  # the fonts may have changed since construction
        super().showEvent(event)

    def _measure(self) -> None:
        """Measure the icon-only and the labelled widths for the fonts in use.

        The window can always shrink to the icon-only bar; labels and captions appear
        once the bar is wide enough to hold them.  The labelled width must not become
        the window's minimum, or a wide bar would stop the window from narrowing.
        """
        self._set_compact(True)
        compact_min = self.minimumSizeHint().width()
        self._set_compact(False)
        self._labelled_min = self.minimumSizeHint().width()
        self.setMinimumWidth(compact_min)
        self._set_compact(self.width() < self._labelled_min)

    def _set_compact(self, compact: bool) -> None:
        if compact == self._compact:
            return
        self._compact = compact
        style = (
            Qt.ToolButtonStyle.ToolButtonIconOnly
            if compact
            else (Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        )
        for button in self._labelled:
            button.setToolButtonStyle(style)
        for caption in self._captions.values():
            caption.setVisible(not compact)

    # ------------------------------------------------------------------

    def set_playing(self, playing: bool) -> None:
        """Flip the play/pause button to match the host's state."""
        self._playing = bool(playing)
        name = "pause" if self._playing else "play"
        self._play_button.setIcon(glyph(name))
        self._play_button.setProperty("glyph", name)
        text = "Pause  (Space)" if self._playing else "Play  (Space)"
        self._play_button.setToolTip(text)
        self._play_button.setAccessibleName(text.split("  (")[0])

    def playing(self) -> bool:
        return self._playing

    def loop_enabled(self) -> bool:
        return self._loop_check.isChecked()

    def set_loop(self, enabled: bool) -> None:
        self._loop_check.setChecked(bool(enabled))

    def speed(self) -> float:
        return float(self._speed_combo.currentData())

    def set_speed(self, speed: float) -> None:
        index = self._speed_combo.findData(speed)
        if index >= 0:
            self._speed_combo.setCurrentIndex(index)

    def edit_button(self, action_id: str) -> QToolButton:
        """The button for one timeline operation (see :data:`EDIT_ACTIONS`)."""
        return self._edit_buttons[action_id]

    def set_edit_state(self, enabled: Mapping[str, bool]) -> None:
        """Enable the operations that make sense right now; absent ids are disabled."""
        self.set_operation_states(
            {key: OpState(bool(enabled.get(key, False))) for key in self._edit_buttons}
        )

    def set_operation_states(self, states: Mapping[str, OpState]) -> None:
        """Enable each operation's button, and say why in its tooltip when it is off."""
        for key, button in self._edit_buttons.items():
            state = states.get(key, OpState(False, "not available"))
            button.setEnabled(state.enabled)
            button.setToolTip(tooltip_for(OPS_BY_KEY[key], state))

    def bind_snap_action(self, action: QAction) -> None:
        """Show ``action`` (View > Snap timeline) as the snap indicator; the two stay in step."""
        self._snap_button.setChecked(action.isChecked())
        action.toggled.connect(self._snap_button.setChecked)
        self._snap_button.toggled.connect(action.setChecked)

    def set_edits_summary(self, text: str) -> None:
        self.edits_label.setText(text)
        self.edits_label.setVisible(bool(text))

    def _emit_speed(self, *_args: object) -> None:
        self.speedChanged.emit(self.speed())
