"""The unsaved-changes guard: opening or closing must not silently lose edits."""

from __future__ import annotations

import shutil
from collections.abc import Iterator
from pathlib import Path

import pytest

from manim_studio.editor import main_window as main_window_module
from manim_studio.editor import scene_files
from manim_studio.editor.main_window import MainWindow

SCENE_FILE = Path(__file__).parent / "scenes" / "feature_showcase.py"


@pytest.fixture
def scene(tmp_path: Path) -> Path:
    path = tmp_path / "demo.py"
    shutil.copy(SCENE_FILE, path)
    return path


@pytest.fixture
def other(tmp_path: Path) -> Path:
    path = tmp_path / "other.py"
    shutil.copy(SCENE_FILE, path)
    return path


@pytest.fixture
def window(qtbot, scene: Path) -> Iterator[MainWindow]:  # type: ignore[no-untyped-def]
    win = MainWindow(restore_settings=False, auto_preview=False, completion=False)
    qtbot.addWidget(win)
    assert win.open_path(scene)
    yield win
    # Leave nothing unsaved, so closing the window at teardown never asks the user.
    win.editor.load_file(scene)


def _edit(window: MainWindow) -> None:
    window.editor.insert_line_at(1, "# edited")


def _answer(monkeypatch: pytest.MonkeyPatch, choice: str) -> list[str]:
    asked: list[str] = []

    def fake(parent: object, name: str, action: str) -> str:
        asked.append(action)
        return choice

    monkeypatch.setattr(main_window_module, "ask_discard", fake)
    return asked


class TestRule:
    def test_untitled_buffer_counts_only_once_it_has_text(self) -> None:
        assert not scene_files.has_unsaved_changes(None, "", False)
        assert not scene_files.has_unsaved_changes(None, "   \n", False)
        assert scene_files.has_unsaved_changes(None, "x = 1", False)

    def test_saved_scene_counts_only_when_edited_and_different(self, scene: Path) -> None:
        saved = scene.read_text(encoding="utf-8")
        assert not scene_files.has_unsaved_changes(scene, saved, False)
        assert not scene_files.has_unsaved_changes(scene, saved + "\n# x", False)
        assert not scene_files.has_unsaved_changes(scene, saved, True)  # retyped, same text
        assert scene_files.has_unsaved_changes(scene, saved + "\n# x", True)


class TestOpening:
    def test_clean_buffer_opens_without_asking(
        self, monkeypatch: pytest.MonkeyPatch, window: MainWindow, other: Path
    ) -> None:
        asked = _answer(monkeypatch, "cancel")
        assert window.open_path(other)
        assert asked == []
        assert window.current_path == other.resolve()

    def test_cancel_keeps_the_edit_and_the_scene(
        self, monkeypatch: pytest.MonkeyPatch, window: MainWindow, scene: Path, other: Path
    ) -> None:
        _edit(window)
        asked = _answer(monkeypatch, "cancel")
        assert not window.open_path(other)
        assert asked == ["open another scene"]
        assert window.current_path == scene.resolve()
        assert "# edited" in window.editor.text()

    def test_discard_opens_the_new_scene(
        self, monkeypatch: pytest.MonkeyPatch, window: MainWindow, scene: Path, other: Path
    ) -> None:
        _edit(window)
        _answer(monkeypatch, "discard")
        assert window.open_path(other)
        assert window.current_path == other.resolve()
        assert "# edited" not in scene.read_text(encoding="utf-8")

    def test_save_writes_the_edit_then_opens(
        self, monkeypatch: pytest.MonkeyPatch, window: MainWindow, scene: Path, other: Path
    ) -> None:
        _edit(window)
        _answer(monkeypatch, "save")
        assert window.open_path(other)
        assert "# edited" in scene.read_text(encoding="utf-8")
        assert window.current_path == other.resolve()

    def test_untitled_buffer_stays_when_save_as_is_cancelled(
        self, monkeypatch: pytest.MonkeyPatch, qtbot, other: Path
    ) -> None:  # type: ignore[no-untyped-def]
        win = MainWindow(restore_settings=False, auto_preview=False, completion=False)
        qtbot.addWidget(win)
        win.editor.set_text("x = 1\n")
        _answer(monkeypatch, "save")
        monkeypatch.setattr(win, "save_buffer_as", lambda: None)  # the user cancels Save As
        assert not win.open_path(other)
        assert win.editor.text() == "x = 1\n"
        win.editor.set_text("")  # nothing left unsaved for teardown


class TestClosing:
    def test_cancel_keeps_the_window_open(
        self, monkeypatch: pytest.MonkeyPatch, window: MainWindow
    ) -> None:
        _edit(window)
        asked = _answer(monkeypatch, "cancel")
        assert not window.close()
        assert asked == ["close the editor"]

    def test_discard_closes(self, monkeypatch: pytest.MonkeyPatch, window: MainWindow) -> None:
        _edit(window)
        _answer(monkeypatch, "discard")
        assert window.close()

    def test_clean_window_closes_without_asking(
        self, monkeypatch: pytest.MonkeyPatch, window: MainWindow
    ) -> None:
        asked = _answer(monkeypatch, "cancel")
        assert window.close()
        assert asked == []
