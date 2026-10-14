"""The status chip follows the window: scene, host, load stages, unsaved edits and errors."""

from __future__ import annotations

import shutil
from pathlib import Path

from PySide6.QtGui import QImage

from manim_studio.editor.main_window import MainWindow

SCENE_FILE = Path(__file__).parent / "scenes" / "feature_showcase.py"
ONE_BLOCK = {"blocks": [{"name": "ShowCreation", "start": 0.0, "duration": 1.0, "index": 0}]}


def _window(qtbot, tmp_path: Path, *, with_scene: bool = True) -> MainWindow:  # type: ignore[no-untyped-def]
    win = MainWindow(restore_settings=False, auto_preview=False, completion=False)
    qtbot.addWidget(win)
    if with_scene:
        scene = tmp_path / "demo.py"
        shutil.copy(SCENE_FILE, scene)
        assert win.open_path(scene)
    return win


def _chip(win: MainWindow) -> str:
    return win._status_chip.text()


def _first_frame(win: MainWindow) -> None:
    win.controller.frameReceived.emit(QImage(8, 8, QImage.Format.Format_RGB32))


def test_no_scene_says_so(qtbot) -> None:  # type: ignore[no-untyped-def]
    win = _window(qtbot, Path("."), with_scene=False)
    assert _chip(win) == "● No scene"


def test_an_open_scene_whose_host_is_not_running_says_so(qtbot, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    assert _chip(_window(qtbot, tmp_path)) == "● Not running"


def test_starting_the_host_then_importing_the_scene(qtbot, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    win = _window(qtbot, tmp_path)
    win.controller.stateChanged.emit("starting")
    assert _chip(win) == "● Starting"
    win.controller.helloReceived.emit({"channel": "control"})
    assert _chip(win) == "● Importing"


def test_the_first_frame_ends_the_load_and_the_preview_is_ready(qtbot, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    win = _window(qtbot, tmp_path)
    win.controller.stateChanged.emit("starting")
    _first_frame(win)
    assert _chip(win) == "● Ready"


def test_unsaved_edits_are_stale_until_the_code_matches_the_file(qtbot, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    win = _window(qtbot, tmp_path)
    win.controller.stateChanged.emit("starting")
    _first_frame(win)
    win.editor.insert_line_at(1, "# edited")
    assert _chip(win) == "● Stale"
    win.editor.undo()
    assert _chip(win) == "● Ready"


def test_an_error_is_named_in_the_chip_tooltip(qtbot, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    win = _window(qtbot, tmp_path)
    win.controller.stateChanged.emit("starting")
    _first_frame(win)
    win.controller.errorReceived.emit({"message": "NameError: x", "line": 2})
    assert _chip(win) == "● Error"
    assert win._status_chip.toolTip() == "NameError: x"


def test_a_successful_trace_clears_the_error(qtbot, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    win = _window(qtbot, tmp_path)
    win.controller.stateChanged.emit("starting")
    _first_frame(win)
    win.controller.errorReceived.emit({"message": "boom", "line": 2})
    win.controller.logReceived.emit(ONE_BLOCK)
    assert _chip(win) == "● Ready"


def test_a_crashed_host_is_an_error_that_says_how_to_recover(qtbot, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    win = _window(qtbot, tmp_path)
    win.controller.stateChanged.emit("starting")
    win.controller.stateChanged.emit("crashed")
    assert _chip(win) == "● Error"
    assert "Reload" in win._status_chip.toolTip()
