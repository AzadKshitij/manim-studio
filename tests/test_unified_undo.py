"""One undo stack for the code and the timeline: the latest change of either kind is undone."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QSignalSpy, QTest
from PySide6.QtWidgets import QApplication, QWidget

from manim_studio.editor.code_editor import CodeEditor
from manim_studio.editor.main_window import MainWindow
from manim_studio.editor.patch_controller import PatchController
from manim_studio.site_index import index_source

CTRL = Qt.KeyboardModifier.ControlModifier

SCENE = """from manimlib import *


class Demo(InteractiveScene):
    def construct(self) -> None:
        self.play(FadeIn(Dot()), run_time=1.0)
        self.wait(0.5)
"""


def _trace(source: str) -> dict[str, Any]:
    """The part of the host's trace the timeline needs: one call site per block, in order."""
    sites = index_source(source).sites
    return {
        "event": "log",
        "blocks": [],
        "sites": [{"id": site.site_id, "occ": 0} for site in sites],
        "stats": {},
        "total_duration": 0.0,
    }


@pytest.fixture
def window(qtbot, tmp_path: Path) -> MainWindow:  # type: ignore[no-untyped-def]
    scene = tmp_path / "demo.py"
    scene.write_text(SCENE, encoding="utf-8")
    win = MainWindow(restore_settings=False, auto_preview=False, completion=False)
    qtbot.addWidget(win)
    win.show()
    win.activateWindow()
    assert win.open_path(scene)
    win.patches.on_log(_trace(SCENE))
    return win


def _focus(widget: QWidget) -> None:
    QWidget.setFocus(widget, Qt.FocusReason.OtherFocusReason)
    QApplication.processEvents()


def test_undo_reverts_a_timeline_retime(window: MainWindow) -> None:
    window.patches.set_block_duration(0, 2.0)
    assert window.patches.edit_count == 1
    window._undo_edit()
    assert window.patches.edit_count == 0
    window._redo_edit()
    assert window.patches.edit_count == 1


def test_ctrl_z_in_the_code_editor_undoes_a_timeline_retime(window: MainWindow) -> None:
    window.patches.set_block_duration(0, 2.0)
    _focus(window.editor)
    QTest.keyClick(window.editor, Qt.Key.Key_Z, CTRL)
    assert window.patches.edit_count == 0


def test_ctrl_z_on_the_timeline_undoes_a_timeline_retime(window: MainWindow) -> None:
    window.patches.set_block_duration(0, 2.0)
    _focus(window.timeline)
    QTest.keyClick(window.timeline, Qt.Key.Key_Z, CTRL)
    assert window.patches.edit_count == 0


def test_undo_takes_the_latest_change_of_either_kind(window: MainWindow) -> None:
    original = window.editor.text()
    window.patches.set_block_duration(0, 2.0)  # timeline change 1
    window.editor.insertText(0, "# note\n")  # a typed change
    window.patches.set_block_duration(0, 3.0)  # timeline change 2

    window._undo_edit()  # change 2 comes back off
    assert window.patches.patches[0].seconds == 2.0
    window._undo_edit()  # then the typed change
    assert window.editor.text() == original
    assert window.patches.patches[0].seconds == 2.0
    window._undo_edit()  # and finally change 1
    assert window.patches.edit_count == 0


def test_apply_is_one_undo_step_that_brings_the_timeline_edit_back(window: MainWindow) -> None:
    original = window.editor.text()
    window.patches.set_block_duration(0, 2.0)
    window.apply_edits_to_source()
    assert window.patches.edit_count == 0
    applied = window.editor.text()
    assert applied != original

    window._undo_edit()
    assert window.editor.text() == original
    assert window.patches.edit_count == 1
    window._redo_edit()
    assert window.editor.text() == applied
    assert window.patches.edit_count == 0


def test_a_timeline_step_undone_leaves_a_saved_file_clean(window: MainWindow) -> None:
    window.patches.set_block_duration(0, 2.0)
    window.editor.save_file(window.current_path)
    assert not window.editor.is_modified()

    window._undo_edit()  # the text does not change, so the file is still what was saved
    assert not window.editor.is_modified()
    window._redo_edit()
    assert not window.editor.is_modified()


def test_the_editor_reports_timeline_steps_undone_and_redone(qtbot) -> None:  # type: ignore[no-untyped-def]
    editor = CodeEditor()
    qtbot.addWidget(editor)
    editor.set_text("base")
    editor.emptyUndoBuffer()
    undone = QSignalSpy(editor.containerUndone)
    redone = QSignalSpy(editor.containerRedone)

    editor.addUndoAction(0, 0)  # a timeline step
    editor.insertText(0, "x")  # a typed change after it
    editor.undo()  # the typed change: not a timeline step
    assert undone.count() == 0
    editor.undo()  # the timeline step
    assert undone.count() == 1
    editor.redo()
    assert redone.count() == 1


def test_only_new_timeline_edits_are_recorded(qtbot) -> None:  # type: ignore[no-untyped-def]
    controller = PatchController()
    controller.on_log({"sites": [{"id": "S.construct:play:aa:0", "occ": 0}]})
    recorded = QSignalSpy(controller.recorded)

    assert controller.set_block_duration(0, 2.0)
    assert recorded.count() == 1
    assert controller.undo()
    assert recorded.count() == 1
    assert controller.redo()
    assert recorded.count() == 1
    assert not controller.set_block_duration(0, 2.0)  # nothing changed: nothing recorded
    assert recorded.count() == 1


def test_discard_all_can_be_undone_from_the_timeline(window: MainWindow) -> None:
    window.patches.set_block_duration(0, 2.0)
    window.patches.clear()
    assert window.patches.edit_count == 0
    _focus(window.timeline)
    QTest.keyClick(window.timeline, Qt.Key.Key_Z, CTRL)
    assert window.patches.edit_count == 1
