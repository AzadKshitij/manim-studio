"""Phase 1 UX: recents rules, welcome page, honest action states, quiet shell."""

from __future__ import annotations

import shutil
from pathlib import Path

from PySide6.QtCore import QSettings

from manim_studio.editor.main_window import MainWindow
from manim_studio.editor.shell_panel import ShellPanel
from manim_studio.recent import prune_recent, push_recent

SCENE_FILE = Path(__file__).parent / "scenes" / "feature_showcase.py"


def test_push_recent_moves_to_front_dedupes_and_caps(tmp_path: Path) -> None:
    files = [str(tmp_path / f"s{i}.py") for i in range(10)]
    entries: list[str] = []
    for f in files:
        entries = push_recent(entries, f)
    assert len(entries) == 8
    assert entries[0] == str(Path(files[-1]).resolve())
    again = push_recent(entries, files[5])
    assert again[0] == str(Path(files[5]).resolve())
    assert len(again) == len({e.casefold() for e in again})


def test_prune_recent_drops_missing() -> None:
    assert prune_recent(["a", "b", "c"], exists=lambda p: p != "b") == ["a", "c"]


def test_shell_append_is_quiet_until_revealed(qtbot) -> None:
    shell = ShellPanel()
    qtbot.addWidget(shell)
    counts: list[int] = []
    shell.unreadChanged.connect(counts.append)
    shell.append("routine host log")
    assert not shell.isVisible()
    assert shell.unread == 1
    shell.append("boom", reveal=True)
    assert shell.isVisible()
    assert shell.unread == 0
    assert counts == [1, 0]


def test_cold_start_shows_welcome_and_disables_scene_actions(qtbot) -> None:
    window = MainWindow(restore_settings=False, auto_preview=False, completion=False)
    qtbot.addWidget(window)
    assert window._right_stack.currentWidget() is window.welcome_panel
    assert not window._toolbar_actions["Play"].isEnabled()
    assert not window._toolbar_actions["Export…"].isEnabled()
    assert "open a scene first" in window._toolbar_actions["Play"].toolTip()


def test_opening_scene_switches_page_and_enables_actions(qtbot, tmp_path: Path) -> None:
    scene = tmp_path / "scene.py"
    shutil.copy(SCENE_FILE, scene)
    window = MainWindow(restore_settings=False, auto_preview=False, completion=False)
    qtbot.addWidget(window)
    assert window.open_path(scene)
    assert window._right_stack.currentWidget() is window.preview_panel
    assert window._toolbar_actions["Play"].isEnabled()


def test_recents_persist_across_windows(qtbot, tmp_path: Path) -> None:
    scene = tmp_path / "scene.py"
    shutil.copy(SCENE_FILE, scene)
    settings = QSettings(str(tmp_path / "s.ini"), QSettings.Format.IniFormat)
    first = MainWindow(settings=settings, auto_preview=False, completion=False)
    qtbot.addWidget(first)
    assert first.open_path(scene)
    second = MainWindow(settings=settings, auto_preview=False, completion=False)
    qtbot.addWidget(second)
    assert second.recent_files() == [str(scene.resolve())]
    assert second.welcome_panel.recent_list.count() == 1
