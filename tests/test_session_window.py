"""The window comes back the way it was left: layout, preview mode, last file, scene choice."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QSettings

from manim_studio.editor.main_window import MainWindow

SCENE_FILE = Path(__file__).parent / "scenes" / "feature_showcase.py"
SECOND_SCENE = (
    "\n\nclass SecondScene(InteractiveScene):\n"
    "    def construct(self) -> None:\n"
    "        self.wait(0.1)\n"
)


def _settings(tmp_path: Path) -> QSettings:
    return QSettings(str(tmp_path / "window.ini"), QSettings.Format.IniFormat)


def _window(qtbot, settings: QSettings) -> MainWindow:  # type: ignore[no-untyped-def]
    win = MainWindow(
        settings=settings,
        restore_settings=True,
        auto_preview=False,
        completion=False,
    )
    qtbot.addWidget(win)
    return win


def _scene_copy(tmp_path: Path, name: str, extra: str = "") -> Path:
    path = tmp_path / name
    path.write_text(SCENE_FILE.read_text(encoding="utf-8") + extra, encoding="utf-8")
    return path


class TestRestart:
    def test_layout_mode_and_last_file_come_back(self, qtbot, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
        settings = _settings(tmp_path)
        scene = _scene_copy(tmp_path, "demo.py")
        first = _window(qtbot, settings)
        first.resize(1200, 800)
        first.show()
        assert first.open_path(scene)
        first._horizontal.setSizes([300, 900])
        first.shell.show()
        first.set_embedded(False)
        expected_sizes = first._horizontal.sizes()  # what closing is about to remember
        first.close()  # closing remembers the layout and the mode

        second = _window(qtbot, settings)
        assert second.current_path == scene.resolve()
        assert second._embedded is False
        assert second.preview_panel.is_embedded is False
        # The splitters and the Live shell are restored on the first show.
        second.resize(1200, 800)
        second.show()
        assert not second.shell.isHidden()
        assert second._horizontal.sizes() == expected_sizes

    def test_the_scene_chosen_for_a_file_comes_back(self, qtbot, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
        settings = _settings(tmp_path)
        scene = _scene_copy(tmp_path, "two.py", SECOND_SCENE)
        first = _window(qtbot, settings)
        assert first.open_path(scene)
        assert "SecondScene" in first.scene_names()
        first.select_scene("SecondScene")
        first.close()

        second = _window(qtbot, settings)
        assert second.current_path == scene.resolve()
        assert second.current_scene_name() == "SecondScene"

    def test_turning_off_reopen_starts_on_the_welcome_page(self, qtbot, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
        settings = _settings(tmp_path)
        scene = _scene_copy(tmp_path, "demo.py")
        first = _window(qtbot, settings)
        assert first.open_path(scene)
        first._reopen_action.setChecked(False)
        first.close()

        second = _window(qtbot, settings)
        assert second.current_path is None
        assert not second._reopen_action.isChecked()
