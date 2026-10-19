"""The Scene and View menus: per-scene settings, the source block and the panel layout (Qt)."""

from __future__ import annotations

from pathlib import Path

import pytest
from PySide6.QtGui import QAction

from manim_studio.editor.export_dialog import ExportDialog
from manim_studio.editor.main_window import MainWindow
from manim_studio.layout_presets import LANDSCAPE, PORTRAIT
from manim_studio.scene_settings import BLOCK_BEGIN
from manim_studio.sidecar import load_scene_settings, sidecar_path

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


def _action(window: MainWindow, text: str) -> QAction:
    for action in window.findChildren(QAction):
        if action.text() == text:
            return action
    raise AssertionError(f"no action named {text!r}")


def test_scene_menu_sits_between_run_and_timeline(window: MainWindow) -> None:
    titles = [action.text() for action in window.menuBar().actions()]
    assert titles.index("&Scene") == titles.index("&Run") + 1
    assert titles.index("&Timeline") == titles.index("&Scene") + 1


def test_scene_actions_need_an_open_file(qtbot) -> None:
    empty = MainWindow(restore_settings=False, auto_preview=False, completion=False)
    qtbot.addWidget(empty)
    assert not _action(empty, "Scene settings…").isEnabled()
    assert not _action(empty, "Apply settings to source").isEnabled()


class TestSettingsFromTheMenu:
    def test_a_portrait_resolution_is_saved_beside_the_scene(
        self, window: MainWindow, scene: Path
    ) -> None:
        window.set_scene_resolution("portrait-1080p")
        settings = load_scene_settings(scene)["Intro"]
        assert (settings.width, settings.height) == (1080, 1920)
        assert window._resolution_actions["portrait-1080p"].isChecked()
        assert not window._resolution_actions["1080p"].isChecked()

    def test_rotate_swaps_the_saved_size(self, window: MainWindow, scene: Path) -> None:
        window.set_scene_resolution("1080p")
        window.rotate_scene()
        settings = load_scene_settings(scene)["Intro"]
        assert (settings.width, settings.height) == (1080, 1920)

    def test_palette_sets_background_and_swatches(self, window: MainWindow, scene: Path) -> None:
        window.set_scene_palette("ocean")
        settings = load_scene_settings(scene)["Intro"]
        assert settings.palette == "ocean"
        assert settings.background_color == "#0B1F33"
        assert len(settings.swatches) == 5
        window.set_scene_palette("")
        assert load_scene_settings(scene)["Intro"].swatches == ()

    def test_reset_clears_the_stored_settings(self, window: MainWindow, scene: Path) -> None:
        window.set_scene_resolution("portrait-1080p")
        window.reset_scene_settings()
        assert not sidecar_path(scene).exists()
        assert window._resolution_actions["1080p"].isChecked()


class TestSourceBlock:
    def test_apply_writes_the_block_once(self, window: MainWindow, scene: Path) -> None:
        window.set_scene_resolution("portrait-1080p")
        window.apply_scene_settings_to_source()
        assert BLOCK_BEGIN in window.editor.text()
        assert scene.read_text(encoding="utf-8").count(BLOCK_BEGIN) == 1
        window.apply_scene_settings_to_source()
        assert scene.read_text(encoding="utf-8").count(BLOCK_BEGIN) == 1

    def test_apply_refuses_while_the_buffer_has_unsaved_edits(
        self, window: MainWindow, scene: Path
    ) -> None:
        window.set_scene_resolution("portrait-1080p")
        saved = scene.read_text(encoding="utf-8")
        window.editor.replace_text(SOURCE + "# unsaved\n")
        window.apply_scene_settings_to_source()
        assert scene.read_text(encoding="utf-8") == saved


class TestLayout:
    def test_a_portrait_scene_moves_the_timeline_under_the_code(self, window: MainWindow) -> None:
        window.set_scene_resolution("portrait-1080p")
        assert window._layout_key == PORTRAIT
        assert window._vertical.indexOf(window.editor_pane) == 0
        assert window._vertical.indexOf(window._bottom_panel) == 1
        assert window._horizontal.indexOf(window._right_stack) == 1

    def test_a_landscape_scene_uses_the_landscape_arrangement(self, window: MainWindow) -> None:
        window.set_scene_resolution("portrait-1080p")
        window.set_scene_resolution("1080p")
        assert window._layout_key == LANDSCAPE
        assert window._horizontal.indexOf(window.editor_pane) == 0
        assert window._vertical.indexOf(window._horizontal) == 0

    def test_a_pinned_layout_is_kept_when_the_scene_changes(self, window: MainWindow) -> None:
        window.set_layout_mode(LANDSCAPE)
        window.set_scene_resolution("portrait-1080p")
        assert window._layout_key == LANDSCAPE
        assert window._layout_actions[LANDSCAPE].isChecked()

    def test_the_panels_keep_their_widgets_across_arrangements(self, window: MainWindow) -> None:
        timeline = window.timeline
        window.set_layout_mode(PORTRAIT)
        window.set_layout_mode(LANDSCAPE)
        assert window.timeline is timeline
        assert window._bottom_panel.isAncestorOf(timeline)
        assert window.editor_pane.isAncestorOf(window.editor)


class TestExportDialogSceneChoice:
    def test_the_scene_settings_choice_only_appears_for_a_scene_with_settings(
        self, qtbot, window: MainWindow, scene: Path
    ) -> None:
        plain = ExportDialog(
            window,
            scene_path=scene,
            scene_names=("Intro",),
            current_scene="Intro",
            settings=window._settings,
        )
        qtbot.addWidget(plain)
        assert plain._resolution_combo.findData("scene") == -1

        with_settings = ExportDialog(
            window,
            scene_path=scene,
            scene_names=("Intro",),
            current_scene="Intro",
            settings=window._settings,
            scene_has_settings=True,
        )
        qtbot.addWidget(with_settings)
        assert with_settings._resolution_combo.currentData() == "scene"
        assert with_settings._resolution() is None
        with_settings._resolution_combo.setCurrentIndex(
            with_settings._resolution_combo.findData("high")
        )
        assert with_settings._resolution() == (1920, 1080)
