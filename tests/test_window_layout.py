"""Fixed-height bars, toolbar buttons and header strips of the main window."""

from __future__ import annotations

from pathlib import Path

import pytest
from PySide6.QtWidgets import QSizePolicy, QToolBar, QToolButton

from manim_studio.editor.main_window import MainWindow

SCENE = Path(__file__).parent / "scenes" / "feature_showcase.py"


@pytest.fixture
def window(qtbot) -> MainWindow:
    win = MainWindow(restore_settings=False, auto_preview=False, completion=False)
    qtbot.addWidget(win)
    win.show()
    return win


def _bar_heights(win: MainWindow) -> tuple[int, int, int]:
    toolbar = win.findChild(QToolBar)
    assert toolbar is not None
    return toolbar.height(), win.transport_bar.height(), win.inspector.height()


def test_bars_keep_their_height_when_the_window_grows(window: MainWindow, qtbot) -> None:
    window.resize(1200, 700)
    qtbot.wait(20)
    small = _bar_heights(window)
    window.resize(1200, 1100)
    qtbot.wait(20)
    assert _bar_heights(window) == small


def test_bars_are_fixed_height_and_the_timeline_expands(window: MainWindow) -> None:
    for bar in (window.transport_bar, window.inspector):
        assert bar.sizePolicy().verticalPolicy() == QSizePolicy.Policy.Fixed
    toolbar = window.findChild(QToolBar)
    assert toolbar is not None
    assert toolbar.sizePolicy().verticalPolicy() == QSizePolicy.Policy.Fixed
    assert window.timeline.sizePolicy().verticalPolicy() == QSizePolicy.Policy.Expanding


def test_run_button_is_the_primary_labelled_button(window: MainWindow) -> None:
    toolbar = window.findChild(QToolBar)
    assert toolbar is not None
    play = toolbar.widgetForAction(window._toolbar_actions["Play"])
    assert isinstance(play, QToolButton)
    assert play.property("primary") is True
    assert play.property("labeled") is True
    reload_button = toolbar.widgetForAction(window._toolbar_actions["Reload"])
    assert reload_button is not None
    assert reload_button.property("primary") is None


def test_export_button_sits_on_the_toolbar(window: MainWindow) -> None:
    assert "Export…" in window._toolbar_actions
    assert not window._toolbar_actions["Export…"].icon().isNull()


def test_editor_header_shows_the_open_file(window: MainWindow) -> None:
    assert window.editor_pane.file_text == "untitled"
    window.open_path(SCENE)
    assert window.editor_pane.file_text == SCENE.name
