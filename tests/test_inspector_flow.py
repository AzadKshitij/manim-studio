"""The inspector end to end (ADR 0012): gear -> edit -> Apply writes the file, one undo reverts.

The real window, the real schema probe (or its cache) and a real file.  No preview host runs, so
no OpenGL is needed.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from pytestqt.qtbot import QtBot

from manim_studio.editor.main_window import MainWindow

SCENE = """from manimlib import *


class Demo(Scene):
    def construct(self):
        circle = Circle(radius=1.5)
        circle.shift(UP)
"""


@pytest.fixture
def window(qtbot: QtBot, tmp_path: Path) -> MainWindow:
    path = tmp_path / "demo.py"
    path.write_text(SCENE, encoding="utf-8")
    main = MainWindow(restore_settings=False, embedded=True, auto_preview=False, completion=False)
    qtbot.addWidget(main)
    main.show()
    assert main.open_path(path) is True
    return main


def test_gear_opens_the_inspector_and_apply_writes_the_file(
    qtbot: QtBot, window: MainWindow
) -> None:
    gears = window.editor_pane.inspector_gears
    # The schema comes from the cache or a probe process; gears appear once it is ready.
    qtbot.waitUntil(lambda: len(gears.buttons) == 2, timeout=60000)

    gears.buttons[0].click()
    dock = window.inspector_dock
    assert dock.target is not None
    assert dock.target.var == "circle"

    radius = next(row for row in dock._rows if row.param.name == "radius")
    radius.editor.setValue(2.5)
    dock._apply.click()

    path = window._path
    assert path is not None
    assert "Circle(radius=2.5)" in path.read_text(encoding="utf-8")
    assert not window.editor.is_modified()
    assert "Applied and saved" in dock._status.text()


def test_one_undo_reverts_an_inspector_apply(qtbot: QtBot, window: MainWindow) -> None:
    gears = window.editor_pane.inspector_gears
    qtbot.waitUntil(lambda: len(gears.buttons) == 2, timeout=60000)
    gears.buttons[0].click()
    dock = window.inspector_dock
    next(row for row in dock._rows if row.param.name == "radius").editor.setValue(2.5)
    dock._apply.click()
    assert "radius=2.5" in window.editor.text()

    window.editor.undo()
    assert "Circle(radius=1.5)" in window.editor.text()


def test_an_inspector_apply_on_an_unsaved_buffer_only_changes_the_editor(
    qtbot: QtBot, window: MainWindow
) -> None:
    gears = window.editor_pane.inspector_gears
    qtbot.waitUntil(lambda: len(gears.buttons) == 2, timeout=60000)
    window.editor.set_text(window.editor.text() + "\n# unsaved\n")
    gears.rescan()
    gears.buttons[0].click()
    dock = window.inspector_dock
    next(row for row in dock._rows if row.param.name == "radius").editor.setValue(3.0)
    dock._apply.click()

    assert "Circle(radius=3.0)" in window.editor.text()
    assert window._path is not None
    assert "radius=1.5" in window._path.read_text(encoding="utf-8")
    assert "Save (Ctrl+S)" in dock._status.text()


def test_opening_another_file_moves_the_gears_to_its_mobjects(
    qtbot: QtBot, window: MainWindow, tmp_path: Path
) -> None:
    gears = window.editor_pane.inspector_gears
    qtbot.waitUntil(lambda: len(gears.buttons) == 2, timeout=60000)
    other = tmp_path / "other.py"
    other.write_text(
        "from manimlib import *\n\n\nclass Other(Scene):\n    def construct(self):\n"
        "        sq = Square()\n",
        encoding="utf-8",
    )
    assert window.open_path(other) is True
    qtbot.waitUntil(
        lambda: [g.toolTip().split(" · ")[0] for g in gears.buttons] == ["sq"], timeout=5000
    )
