"""Scene settings reach the code: every change writes the managed block (Qt, offscreen)."""

from __future__ import annotations

from pathlib import Path

import pytest

from manim_studio.editor.main_window import MainWindow
from manim_studio.scene_settings import BLOCK_BEGIN
from manim_studio.sidecar import load_scene_settings

SOURCE = (
    "from manimlib import *\n\n\n"
    "class Intro(Scene):\n"
    "    def construct(self):\n"
    "        self.wait(1)\n"
)


@pytest.fixture
def scene(tmp_path: Path) -> Path:
    path = tmp_path / "intro.py"
    path.write_text(SOURCE, encoding="utf-8")
    return path


@pytest.fixture
def window(qtbot, scene: Path) -> MainWindow:
    win = MainWindow(restore_settings=False, auto_preview=False, completion=False)
    qtbot.addWidget(win)
    win.show()
    assert win.open_path(scene)
    return win


def test_a_resolution_change_is_written_into_the_saved_code(
    window: MainWindow, scene: Path
) -> None:
    window.set_scene_resolution("portrait-1080p")
    text = scene.read_text(encoding="utf-8")
    assert BLOCK_BEGIN in text
    assert "(1080, 1920)" in text


def test_a_palette_change_updates_the_block(window: MainWindow, scene: Path) -> None:
    window.set_scene_palette("ocean")
    assert "STUDIO_PALETTE" in scene.read_text(encoding="utf-8")


def test_a_later_change_replaces_the_block(window: MainWindow, scene: Path) -> None:
    window.set_scene_resolution("portrait-1080p")
    window.set_scene_resolution("1080p")
    text = scene.read_text(encoding="utf-8")
    assert text.count(BLOCK_BEGIN) == 1
    assert "(1920, 1080)" in text


def test_unsaved_edits_keep_the_saved_file_untouched(window: MainWindow, scene: Path) -> None:
    window.editor.replace_text(SOURCE + "# unsaved\n")
    window.set_scene_resolution("portrait-1080p")
    assert BLOCK_BEGIN not in scene.read_text(encoding="utf-8")
    assert BLOCK_BEGIN in window.editor.text()
    assert window.editor.is_modified()


def test_reset_removes_the_block_from_the_code(window: MainWindow, scene: Path) -> None:
    window.set_scene_resolution("portrait-1080p")
    window.reset_scene_settings()
    assert BLOCK_BEGIN not in scene.read_text(encoding="utf-8")


def test_a_class_with_its_own_init_keeps_its_code(window: MainWindow, tmp_path: Path) -> None:
    clashing = tmp_path / "clash.py"
    source = (
        "from manimlib import *\n\n\n"
        "class Intro(Scene):\n"
        "    def __init__(self, **kwargs):\n        super().__init__(**kwargs)\n"
        "    def construct(self):\n"
        "        self.wait(1)\n"
    )
    clashing.write_text(source, encoding="utf-8")
    assert window.open_path(clashing)
    window.set_scene_resolution("portrait-1080p")
    assert clashing.read_text(encoding="utf-8") == source
    assert load_scene_settings(clashing)["Intro"].width == 1080
