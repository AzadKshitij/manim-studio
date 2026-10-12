"""A change on disk follows the chosen policy, and the chooser above the code does its job."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from PySide6.QtCore import QSettings

from manim_studio.editor.main_window import MainWindow
from manim_studio.editor.session_state import SessionState
from manim_studio.external_changes import ALWAYS, ASK, AUTO, IGNORE

SCENE_FILE = Path(__file__).parent / "scenes" / "feature_showcase.py"


@pytest.fixture
def window(qtbot, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> MainWindow:  # type: ignore[no-untyped-def]
    scene = tmp_path / "demo.py"
    shutil.copy(SCENE_FILE, scene)
    win = MainWindow(restore_settings=False, auto_preview=False, completion=False)
    qtbot.addWidget(win)
    monkeypatch.setattr(win, "reload_preview", lambda: None)  # no host in these tests
    assert win.open_path(scene)
    return win


def _change_on_disk(window: MainWindow) -> str:
    """Another tool saves a new version of the scene; returns what is now on disk."""
    path = window.current_path
    assert path is not None
    text = path.read_text(encoding="utf-8") + "\n# saved by another editor\n"
    path.write_text(text, encoding="utf-8")
    window._on_file_changed(str(path))
    return text


def test_auto_takes_a_change_silently_when_nothing_is_unsaved(window: MainWindow) -> None:
    window._set_external_policy(AUTO)
    disk = _change_on_disk(window)
    assert window.editor.text() == disk
    assert window._external_bar.isHidden()


def test_auto_asks_when_there_are_unsaved_edits_and_take_theirs_replaces_them(
    window: MainWindow,
) -> None:
    window._set_external_policy(AUTO)
    window.editor.insert_line_at(1, "# my edit")
    disk = _change_on_disk(window)
    assert not window._external_bar.isHidden()
    assert "# my edit" in window.editor.text()  # nothing taken yet
    window._external_bar.takeRequested.emit()
    assert window.editor.text() == disk
    assert window._external_bar.isHidden()


def test_keep_mine_keeps_the_edits_and_closes_the_chooser(window: MainWindow) -> None:
    window._set_external_policy(AUTO)
    window.editor.insert_line_at(1, "# my edit")
    _change_on_disk(window)
    window._external_bar.keepRequested.emit()
    assert "# my edit" in window.editor.text()
    assert window._external_bar.isHidden()


def test_taking_the_file_keeps_my_edits_on_the_undo_stack(window: MainWindow) -> None:
    window._set_external_policy(AUTO)
    window.editor.insert_line_at(1, "# my edit")
    _change_on_disk(window)
    window._external_bar.takeRequested.emit()
    window.editor.undo()
    assert "# my edit" in window.editor.text()


def test_always_takes_the_change_over_unsaved_edits_and_the_edits_come_back_with_undo(
    window: MainWindow,
) -> None:
    window._set_external_policy(ALWAYS)
    window.editor.insert_line_at(1, "# my edit")
    disk = _change_on_disk(window)
    assert window.editor.text() == disk
    assert window._external_bar.isHidden()
    window.editor.undo()
    assert "# my edit" in window.editor.text()


def test_ignore_leaves_the_editor_alone(window: MainWindow) -> None:
    window._set_external_policy(IGNORE)
    before = window.editor.text()
    _change_on_disk(window)
    assert window.editor.text() == before
    assert window._external_bar.isHidden()


def test_ask_shows_the_chooser_even_without_unsaved_edits(window: MainWindow) -> None:
    window._set_external_policy(ASK)
    before = window.editor.text()
    _change_on_disk(window)
    assert window.editor.text() == before
    assert not window._external_bar.isHidden()


def test_the_file_menu_offers_the_policies_with_the_current_one_checked(
    window: MainWindow,
) -> None:
    window._set_external_policy(ALWAYS)
    file_menu = next(a.menu() for a in window.menuBar().actions() if a.text() == "&File")
    external = next(a.menu() for a in file_menu.actions() if a.text() == "External changes")
    checked = [action.text() for action in external.actions() if action.isChecked()]
    assert checked == [external_label(ALWAYS)]


def test_the_chosen_policy_is_remembered_between_sessions(tmp_path: Path) -> None:
    settings = QSettings(str(tmp_path / "window.ini"), QSettings.Format.IniFormat)
    SessionState(settings, enabled=True).remember_external_policy(IGNORE)
    assert SessionState(settings, enabled=True).external_policy() == IGNORE
    assert SessionState(settings, enabled=False).external_policy() == AUTO


def external_label(policy: str) -> str:
    from manim_studio.external_changes import POLICY_LABELS

    return POLICY_LABELS[policy]
