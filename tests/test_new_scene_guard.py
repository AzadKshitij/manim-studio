"""Creating a scene asks about unsaved edits before it writes any file, and asks only once."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from PySide6.QtWidgets import QFileDialog, QInputDialog

from manim_studio.editor import main_window as main_window_module
from manim_studio.editor.main_window import MainWindow
from manim_studio.editor.unsaved import CANCEL, DISCARD
from manim_studio.templates import SCENE_TEMPLATES

SCENE_FILE = Path(__file__).parent / "scenes" / "feature_showcase.py"


@pytest.fixture
def window(qtbot, tmp_path: Path) -> MainWindow:  # type: ignore[no-untyped-def]
    scene = tmp_path / "demo.py"
    shutil.copy(SCENE_FILE, scene)
    win = MainWindow(restore_settings=False, auto_preview=False, completion=False)
    qtbot.addWidget(win)
    assert win.open_path(scene)
    return win


def _answer(monkeypatch: pytest.MonkeyPatch, choice: str) -> list[str]:
    asked: list[str] = []

    def fake(parent: object, name: str, action: str) -> str:
        asked.append(action)
        return choice

    monkeypatch.setattr(main_window_module, "ask_discard", fake)
    return asked


def _pick_new_scene(monkeypatch: pytest.MonkeyPatch, target: Path) -> None:
    template = SCENE_TEMPLATES[0]
    monkeypatch.setattr(
        QInputDialog,
        "getItem",
        lambda *_args, **_kwargs: (f"{template.key} — {template.title}", True),
    )
    monkeypatch.setattr(QInputDialog, "getText", lambda *_args, **_kwargs: ("NewScene", True))
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *_args, **_kwargs: (str(target), ""))


def test_cancelling_the_question_writes_no_file(
    monkeypatch: pytest.MonkeyPatch, window: MainWindow, tmp_path: Path
) -> None:
    window.editor.insert_line_at(1, "# edited")
    target = tmp_path / "new_scene.py"
    _pick_new_scene(monkeypatch, target)
    asked = _answer(monkeypatch, CANCEL)
    window.new_scene_dialog()
    assert len(asked) == 1
    assert not target.exists()
    assert window.current_path is not None
    assert window.current_path.name == "demo.py"
    assert window.editor.is_modified()


def test_discarding_asks_once_and_opens_the_new_scene(
    monkeypatch: pytest.MonkeyPatch, window: MainWindow, tmp_path: Path
) -> None:
    window.editor.insert_line_at(1, "# edited")
    target = tmp_path / "new_scene.py"
    _pick_new_scene(monkeypatch, target)
    asked = _answer(monkeypatch, DISCARD)
    window.new_scene_dialog()
    assert len(asked) == 1
    assert target.exists()
    assert window.current_path == target.resolve()


def test_a_clean_editor_creates_and_opens_without_asking(
    monkeypatch: pytest.MonkeyPatch, window: MainWindow, tmp_path: Path
) -> None:
    target = tmp_path / "new_scene.py"
    _pick_new_scene(monkeypatch, target)
    asked = _answer(monkeypatch, CANCEL)
    window.new_scene_dialog()
    assert asked == []
    assert target.exists()
    assert window.current_path == target.resolve()
