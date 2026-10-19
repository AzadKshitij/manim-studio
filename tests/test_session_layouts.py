"""SessionState: each layout keeps its own sizes, and the layout mode and defaults persist."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QLabel, QSplitter

from manim_studio.editor.session_state import SessionState


def _settings(tmp_path: Path) -> QSettings:
    return QSettings(str(tmp_path / "session.ini"), QSettings.Format.IniFormat)


def _splitter(qtbot, sizes: list[int]) -> QSplitter:  # type: ignore[no-untyped-def]
    splitter = QSplitter()
    qtbot.addWidget(splitter)
    for _ in sizes:
        splitter.addWidget(QLabel())
    splitter.resize(900, 500)
    splitter.show()
    splitter.setSizes(sizes)
    return splitter


def test_each_layout_restores_its_own_sizes(tmp_path: Path, qtbot) -> None:  # type: ignore[no-untyped-def]
    state = SessionState(_settings(tmp_path), enabled=True)
    landscape_h = _splitter(qtbot, [300, 700])
    landscape_v = _splitter(qtbot, [800, 200])
    portrait_h = _splitter(qtbot, [400, 600])
    portrait_v = _splitter(qtbot, [700, 300])
    state.remember_layout(horizontal=landscape_h, vertical=landscape_v, shell_visible=False)
    state.remember_layout(
        horizontal=portrait_h, vertical=portrait_v, shell_visible=False, preset="portrait"
    )

    restored_h = _splitter(qtbot, [1, 1])
    restored_v = _splitter(qtbot, [1, 1])
    state.restore_layout(horizontal=restored_h, vertical=restored_v, preset="portrait")
    assert restored_h.sizes() == portrait_h.sizes()
    assert restored_v.sizes() == portrait_v.sizes()

    state.restore_layout(horizontal=restored_h, vertical=restored_v, preset="landscape")
    assert restored_h.sizes() == landscape_h.sizes()
    assert restored_v.sizes() == landscape_v.sizes()


def test_layout_mode_and_scene_defaults_persist(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    state = SessionState(settings, enabled=True)
    assert state.layout_mode() is None
    state.remember_layout_mode("portrait")
    state.remember_scene_defaults_json('{"width": 1080, "height": 1920}')

    again = SessionState(settings, enabled=True)
    assert again.layout_mode() == "portrait"
    assert again.scene_defaults_json() == '{"width": 1080, "height": 1920}'
    again.remember_scene_defaults_json(None)
    assert SessionState(settings, enabled=True).scene_defaults_json() is None


def test_disabled_state_stores_no_layout_choices(tmp_path: Path) -> None:
    state = SessionState(_settings(tmp_path), enabled=False)
    state.remember_layout_mode("portrait")
    assert state.layout_mode() is None
    assert state.scene_defaults_json() is None
