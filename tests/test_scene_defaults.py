"""New scenes start with the saved default settings (Qt, offscreen)."""

from __future__ import annotations

from pathlib import Path

import pytest

from manim_studio.editor.main_window import MainWindow
from manim_studio.scene_settings import BLOCK_BEGIN, SceneSettings
from manim_studio.sidecar import load_scene_settings

SOURCE = (
    "from manimlib import *\n\n\n"
    "class Intro(Scene):\n"
    "    def construct(self):\n"
    "        self.wait(1)\n"
)


@pytest.fixture
def window(qtbot) -> MainWindow:
    win = MainWindow(restore_settings=False, auto_preview=False, completion=False)
    qtbot.addWidget(win)
    return win


def test_a_new_scene_gets_the_saved_defaults(
    window: MainWindow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "fresh.py"
    target.write_text(SOURCE, encoding="utf-8")
    defaults = SceneSettings(width=1080, height=1920, background_color="#1E1E2E")
    monkeypatch.setattr(window, "_stored_scene_defaults", lambda: defaults)

    window._apply_defaults_to_new_scene(target, "Intro")

    assert BLOCK_BEGIN in target.read_text(encoding="utf-8")
    assert load_scene_settings(target) == {"Intro": defaults}


def test_without_saved_defaults_the_new_scene_is_untouched(
    window: MainWindow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "fresh.py"
    target.write_text(SOURCE, encoding="utf-8")
    monkeypatch.setattr(window, "_stored_scene_defaults", lambda: None)

    window._apply_defaults_to_new_scene(target, "Intro")

    assert target.read_text(encoding="utf-8") == SOURCE
    assert load_scene_settings(target) == {}
