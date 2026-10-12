"""Timeline shortcuts: a key whose operation is off says why, and the code editor keeps its keys."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QContextMenuEvent
from PySide6.QtTest import QSignalSpy, QTest
from PySide6.QtWidgets import QApplication, QMenu, QWidget

from manim_studio.editor.main_window import MainWindow
from manim_studio.timeline_ops import OPS, OpState

SCENE_FILE = Path(__file__).parent / "scenes" / "feature_showcase.py"
CTRL = Qt.KeyboardModifier.ControlModifier
SHIFT = Qt.KeyboardModifier.ShiftModifier


@pytest.fixture
def window(qtbot, tmp_path: Path) -> MainWindow:  # type: ignore[no-untyped-def]
    scene = tmp_path / "demo.py"
    shutil.copy(SCENE_FILE, scene)
    win = MainWindow(restore_settings=False, auto_preview=False, completion=False)
    qtbot.addWidget(win)
    win.resize(1400, 900)
    win.show()
    win.activateWindow()
    assert win.open_path(scene)
    return win


def _focus(widget: QWidget) -> None:
    QWidget.setFocus(widget, Qt.FocusReason.OtherFocusReason)
    QApplication.processEvents()


def _status(window: MainWindow) -> str:
    return window.statusBar().currentMessage()


def _fired(spy: QSignalSpy) -> list[str]:
    return [spy.at(i)[0] for i in range(spy.count())]


def test_a_disabled_window_shortcut_says_why_even_from_the_code_editor(window: MainWindow) -> None:
    _focus(window.editor)
    QTest.keyClick(window.editor, Qt.Key.Key_A, CTRL | SHIFT)
    assert _status(window) == "Apply timeline edits to source: no timeline edits"


def test_a_disabled_timeline_shortcut_says_why_on_the_timeline(window: MainWindow) -> None:
    _focus(window.timeline)
    QTest.keyClick(window.timeline, Qt.Key.Key_Delete)
    assert _status(window) == "Delete selected block's call: start the preview first"


def test_the_code_editor_keeps_its_own_editing_keys(window: MainWindow) -> None:
    _focus(window.editor)
    QTest.keyClick(window.editor, Qt.Key.Key_Backspace, CTRL)
    assert "Reset selected block" not in _status(window)


def test_an_enabled_operation_runs_from_the_code_editor(window: MainWindow) -> None:
    ops = window._timeline_ops
    ops.apply({op.key: OpState(op.key == "apply") for op in OPS})
    ops.requested.disconnect()  # run nothing: only the key's routing is under test
    spy = QSignalSpy(ops.requested)
    _focus(window.editor)
    QTest.keyClick(window.editor, Qt.Key.Key_A, CTRL | SHIFT)
    assert _fired(spy) == ["apply"]


def test_a_toolbar_click_keeps_the_timeline_keys_in_reach(window: MainWindow) -> None:
    ops = window._timeline_ops
    ops.apply({op.key: OpState(op.key == "delete") for op in OPS})
    ops.requested.disconnect()
    spy = QSignalSpy(ops.requested)
    button = window.transport_bar._fit_button
    QTest.mouseClick(button, Qt.MouseButton.LeftButton)
    QApplication.processEvents()
    assert QApplication.focusWidget() is window.timeline  # the bar handed the focus on
    QTest.keyClick(QApplication.focusWidget(), Qt.Key.Key_Delete)
    assert _fired(spy) == ["delete"]


def test_a_right_click_on_the_timeline_gives_it_the_keys(
    window: MainWindow, monkeypatch: pytest.MonkeyPatch
) -> None:
    class SilentMenu(QMenu):
        def exec(self, *args: object, **kwargs: object) -> None:  # the real popup blocks
            return None

    timeline = window.timeline
    monkeypatch.setattr(timeline, "context_menu", lambda index: SilentMenu(timeline))
    _focus(window.editor)
    event = QContextMenuEvent(QContextMenuEvent.Reason.Mouse, QPoint(20, 20), QPoint(20, 20))
    QApplication.sendEvent(timeline, event)
    assert QApplication.focusWidget() is timeline
