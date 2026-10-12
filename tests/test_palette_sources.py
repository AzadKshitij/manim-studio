"""The palette's commands for a real editor window, plus its shortcut wiring (offscreen)."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from PySide6.QtCore import QSettings
from PySide6.QtGui import QAction

from manim_studio.command_palette import ACTIONS, BLOCKS, RECENT, SCENES, TIMELINE, PaletteEntry
from manim_studio.editor.main_window import MainWindow
from manim_studio.editor.palette_dialog import PaletteItem
from manim_studio.editor.palette_sources import build_items
from manim_studio.timeline_model import parse_log

SCENE_FILE = Path(__file__).parent / "scenes" / "feature_showcase.py"
BLOCKS_PAYLOAD = [
    {"name": "ShowCreation", "start": 0.0, "duration": 1.0, "index": 0},
    {"name": "Wait", "start": 1.0, "duration": 0.5, "index": 1},
]


@pytest.fixture
def window(qtbot) -> MainWindow:  # type: ignore[no-untyped-def]
    win = MainWindow(restore_settings=False, auto_preview=False, completion=False)
    qtbot.addWidget(win)
    return win


def _entries(window: MainWindow) -> dict[str, list[PaletteEntry]]:
    grouped: dict[str, list[PaletteEntry]] = {}
    for item in build_items(window):
        grouped.setdefault(item.entry.group, []).append(item.entry)
    return grouped


def _find(window: MainWindow, title: str) -> PaletteItem:
    return next(item for item in build_items(window) if item.entry.title == title)


class TestActions:
    def test_menu_and_toolbar_duplicates_are_listed_once(self, window: MainWindow) -> None:
        titles = [item.entry.title for item in build_items(window)]
        assert titles.count("Export") == 1
        assert titles.count("Reload preview") == 1

    def test_shortcuts_come_from_the_live_action(self, window: MainWindow) -> None:
        assert _find(window, "Reload preview").entry.shortcut == "F5"

    def test_disabled_scene_actions_say_why(self, window: MainWindow) -> None:
        export = _find(window, "Export").entry
        assert not export.enabled
        assert export.reason == "open a scene first"

    def test_checkable_view_actions_report_on_or_off(self, window: MainWindow) -> None:
        embedded = _find(window, "Embedded preview").entry
        assert embedded.detail in ("on", "off")

    def test_palette_does_not_offer_itself(self, window: MainWindow) -> None:
        assert all(item.entry.title != "Command palette" for item in build_items(window))

    def test_actions_group_is_populated(self, window: MainWindow) -> None:
        assert _entries(window)[ACTIONS]


class TestTimeline:
    def test_transport_commands_need_a_scene(self, window: MainWindow) -> None:
        play = _find(window, "Play / pause").entry
        assert play.group == TIMELINE
        assert not play.enabled
        assert play.reason == "open a scene first"

    def test_selected_block_operations_need_a_selection(self, window: MainWindow) -> None:
        delete = _find(window, "Delete selected block's call").entry
        assert not delete.enabled
        assert delete.reason == "select a block first"


class TestScenesRecentsAndBlocks:
    def test_open_scenes_are_listed_and_the_current_one_is_marked(
        self, window: MainWindow, tmp_path: Path
    ) -> None:
        scene = tmp_path / "scene.py"
        shutil.copy(SCENE_FILE, scene)
        assert window.open_path(scene)
        current = window.current_scene_name()
        assert current
        entry = _find(window, f"Preview scene: {current}").entry
        assert entry.group == SCENES
        assert entry.detail == "current"

    def test_recent_files_are_offered_and_open(self, qtbot, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
        settings = QSettings(str(tmp_path / "palette.ini"), QSettings.Format.IniFormat)
        first = MainWindow(settings=settings, auto_preview=False, completion=False)
        qtbot.addWidget(first)
        scene = tmp_path / "scene.py"
        shutil.copy(SCENE_FILE, scene)
        assert first.open_path(scene)
        second = MainWindow(settings=settings, auto_preview=False, completion=False)
        qtbot.addWidget(second)
        item = _find(second, "Open scene.py")
        assert item.entry.group == RECENT
        item.run()
        assert second.current_path == scene.resolve()

    def test_blocks_are_listed_and_jump_to_the_block(self, window: MainWindow) -> None:
        window.timeline.set_log(parse_log(BLOCKS_PAYLOAD))
        blocks = _entries(window)[BLOCKS]
        assert [entry.title for entry in blocks] == ["#0  ShowCreation", "#1  Wait"]
        _find(window, "#1  Wait").run()
        assert window.timeline.selected_block == 1


class TestPaletteWindow:
    def _action(self, window: MainWindow, text: str) -> QAction:
        return next(action for action in window.findChildren(QAction) if action.text() == text)

    def test_command_palette_is_bound_to_ctrl_shift_p(self, window: MainWindow) -> None:
        action = self._action(window, "Command palette…")
        assert action.shortcut().toString() == "Ctrl+Shift+P"

    def test_the_action_opens_the_palette(self, window: MainWindow) -> None:
        self._action(window, "Command palette…").trigger()
        assert window._palette.isVisible()
        window._palette.hide()

    def test_keyboard_shortcuts_entry_opens_the_shortcut_scope(self, window: MainWindow) -> None:
        self._action(window, "Keyboard shortcuts…").trigger()
        assert window._palette.input.text() == "?"
        assert all(entry.shortcut for entry in window._palette.shown)
        window._palette.hide()
