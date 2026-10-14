"""SessionState: layout, preview mode, last file and scene choices survive a restart."""

from __future__ import annotations

from pathlib import Path

import pytest
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QLabel, QSplitter

from manim_studio.editor.session_state import SessionState, scene_key


def _settings(tmp_path: Path) -> QSettings:
    return QSettings(str(tmp_path / "session.ini"), QSettings.Format.IniFormat)


def _splitter(qtbot, sizes: list[int]) -> QSplitter:  # type: ignore[no-untyped-def]
    """A shown splitter, so ``setSizes`` takes effect the way it does in the app."""
    splitter = QSplitter()
    qtbot.addWidget(splitter)
    for _ in sizes:
        splitter.addWidget(QLabel())
    splitter.resize(900, 500)
    splitter.show()
    splitter.setSizes(sizes)
    return splitter


class TestDisabled:
    def test_nothing_is_written_and_every_read_is_a_default(self, tmp_path: Path, qtbot) -> None:  # type: ignore[no-untyped-def]
        settings = _settings(tmp_path)
        state = SessionState(settings, enabled=False)
        horizontal, vertical = _splitter(qtbot, [300, 700]), _splitter(qtbot, [800, 200])
        state.remember_layout(horizontal=horizontal, vertical=vertical, shell_visible=True)
        state.remember_embedded(False)
        state.remember_last_file(tmp_path / "demo.py")
        state.remember_scene(tmp_path / "demo.py", "Demo")
        assert settings.allKeys() == []
        assert state.embedded() is None
        assert state.last_file() is None
        assert state.scene_for(tmp_path / "demo.py") is None
        assert state.reopen_last_file is False


class TestEnabled:
    def test_splitter_sizes_and_shell_visibility_round_trip(self, tmp_path: Path, qtbot) -> None:  # type: ignore[no-untyped-def]
        settings = _settings(tmp_path)
        source_h, source_v = _splitter(qtbot, [300, 700]), _splitter(qtbot, [800, 200])
        SessionState(settings, enabled=True).remember_layout(
            horizontal=source_h, vertical=source_v, shell_visible=True
        )
        expected_h, expected_v = source_h.sizes(), source_v.sizes()
        horizontal, vertical = _splitter(qtbot, [500, 500]), _splitter(qtbot, [500, 500])
        shell_visible = SessionState(settings, enabled=True).restore_layout(
            horizontal=horizontal, vertical=vertical
        )
        assert shell_visible is True
        assert horizontal.sizes() == expected_h
        assert vertical.sizes() == expected_v

    def test_nothing_remembered_restores_nothing(self, tmp_path: Path, qtbot) -> None:  # type: ignore[no-untyped-def]
        state = SessionState(_settings(tmp_path), enabled=True)
        horizontal, vertical = _splitter(qtbot, [400, 600]), _splitter(qtbot, [700, 300])
        before = horizontal.sizes()
        assert state.restore_layout(horizontal=horizontal, vertical=vertical) is None
        assert horizontal.sizes() == before

    def test_preview_mode_round_trips(self, tmp_path: Path) -> None:
        settings = _settings(tmp_path)
        SessionState(settings, enabled=True).remember_embedded(False)
        assert SessionState(settings, enabled=True).embedded() is False
        SessionState(settings, enabled=True).remember_embedded(True)
        assert SessionState(settings, enabled=True).embedded() is True

    def test_last_file_round_trips(self, tmp_path: Path) -> None:
        settings = _settings(tmp_path)
        scene = tmp_path / "demo.py"
        SessionState(settings, enabled=True).remember_last_file(scene)
        assert SessionState(settings, enabled=True).last_file() == scene.resolve()

    def test_reopen_last_file_is_on_by_default_and_can_be_turned_off(self, tmp_path: Path) -> None:
        settings = _settings(tmp_path)
        assert SessionState(settings, enabled=True).reopen_last_file is True
        SessionState(settings, enabled=True).set_reopen_last_file(False)
        assert SessionState(settings, enabled=True).reopen_last_file is False

    def test_each_file_remembers_its_own_scene(self, tmp_path: Path) -> None:
        settings = _settings(tmp_path)
        state = SessionState(settings, enabled=True)
        first, second = tmp_path / "one.py", tmp_path / "two.py"
        state.remember_scene(first, "Intro")
        state.remember_scene(second, "Outro")
        assert state.scene_for(first) == "Intro"
        assert state.scene_for(second) == "Outro"

    def test_an_empty_scene_name_is_not_remembered(self, tmp_path: Path) -> None:
        state = SessionState(_settings(tmp_path), enabled=True)
        state.remember_scene(tmp_path / "demo.py", "")
        assert state.scene_for(tmp_path / "demo.py") is None

    @pytest.mark.parametrize("spelling", ["demo.py", "./demo.py"])
    def test_scene_keys_do_not_depend_on_how_the_path_is_spelled(
        self, tmp_path: Path, spelling: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:  # type: ignore[no-untyped-def]
        monkeypatch.chdir(tmp_path)
        assert scene_key(Path(spelling)) == scene_key(tmp_path / "demo.py")
