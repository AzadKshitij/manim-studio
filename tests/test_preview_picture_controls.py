"""The preview picture takes pointer input: a click plays or pauses, the wheel steps frames."""

from __future__ import annotations

from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtGui import QWheelEvent
from PySide6.QtTest import QSignalSpy
from PySide6.QtWidgets import QApplication

from manim_studio.editor.preview_panel import PreviewPanel


def _wheel(delta: int) -> QWheelEvent:
    return QWheelEvent(
        QPointF(10, 10),
        QPointF(10, 10),
        QPoint(0, 0),
        QPoint(0, delta),
        Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.NoModifier,
        Qt.ScrollPhase.NoScrollPhase,
        False,
    )


def _shown_panel(qtbot) -> PreviewPanel:  # type: ignore[no-untyped-def]
    panel = PreviewPanel()
    qtbot.addWidget(panel)
    panel.resize(400, 300)
    panel.show()
    return panel


def test_a_click_on_the_picture_toggles_play(qtbot) -> None:  # type: ignore[no-untyped-def]
    panel = _shown_panel(qtbot)
    spy = QSignalSpy(panel.playToggleRequested)
    qtbot.mouseClick(panel._viewport, Qt.MouseButton.LeftButton)
    assert spy.count() == 1


def test_other_buttons_do_not_toggle_play(qtbot) -> None:  # type: ignore[no-untyped-def]
    panel = _shown_panel(qtbot)
    spy = QSignalSpy(panel.playToggleRequested)
    qtbot.mouseClick(panel._viewport, Qt.MouseButton.RightButton)
    assert spy.count() == 0


def test_each_wheel_notch_steps_one_frame(qtbot) -> None:  # type: ignore[no-untyped-def]
    panel = _shown_panel(qtbot)
    spy = QSignalSpy(panel.frameStepRequested)
    QApplication.sendEvent(panel._viewport, _wheel(120))  # wheel up: back one frame
    QApplication.sendEvent(panel._viewport, _wheel(-240))  # wheel down two notches: forward two
    assert [spy.at(i)[0] for i in range(spy.count())] == [-1, 2]


def test_small_trackpad_deltas_add_up_to_one_frame(qtbot) -> None:  # type: ignore[no-untyped-def]
    panel = _shown_panel(qtbot)
    spy = QSignalSpy(panel.frameStepRequested)
    for _ in range(2):
        QApplication.sendEvent(panel._viewport, _wheel(40))
    assert spy.count() == 0
    QApplication.sendEvent(panel._viewport, _wheel(40))
    assert spy.count() == 1
    assert spy.at(0)[0] == -1
