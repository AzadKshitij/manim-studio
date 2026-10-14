"""Scene errors keep the scene line they came from, so the editor can offer Go to line."""

from __future__ import annotations

from pathlib import Path

import pytest
from PySide6.QtWidgets import QPushButton

from manim_studio.editor.error_banner import ErrorBanner
from manim_studio.editor.main_window import MainWindow
from manim_studio.preview_host import HostConfig, SceneHost
from manim_studio.scene_runner import SceneLoadError, format_scene_error, load_scene_module

HEADER = "from manimlib import *\n\n"


class _NoPump:
    """Stands in for the frame pump, so a host can trace without a GL window."""

    def __getattr__(self, name: str):  # type: ignore[no-untyped-def]
        return lambda *args, **kwargs: None


def _write(tmp_path: Path, name: str, text: str) -> Path:
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


def test_a_syntax_error_in_the_scene_gives_its_line_through_the_host(tmp_path: Path) -> None:
    """The host checks the file before importing it, so the check's line must survive."""
    scene = _write(
        tmp_path,
        "typo_syntax.py",
        HEADER + "class Demo(Scene):\n    def construct(self):\n        self.play(\n",
    )
    host = SceneHost(HostConfig(scene_path=scene, embedded=True, size=(320, 180)))
    with pytest.raises(SceneLoadError) as raised:
        host.load()
    assert format_scene_error(raised.value, scene).line == 5


def test_a_runtime_error_in_construct_gives_its_line_through_the_host(tmp_path: Path) -> None:
    scene = _write(
        tmp_path,
        "trace_error.py",
        HEADER
        + "class Demo(Scene):\n    def construct(self):\n"
        + "        self.wait(0.1)\n        undefined_name\n",
    )
    host = SceneHost(HostConfig(scene_path=scene, embedded=True, size=(320, 180)))
    host._create_window()
    host.frame_pump = _NoPump()
    with pytest.raises(Exception) as raised:
        host.load()
    assert format_scene_error(raised.value, scene).line == 6


def test_a_syntax_error_while_importing_the_scene_gives_its_line(tmp_path: Path) -> None:
    scene = _write(
        tmp_path,
        "typo_import.py",
        HEADER + "class Demo(Scene):\n    def construct(self):\n        self.play(\n",
    )
    with pytest.raises(Exception) as raised:
        load_scene_module(scene)
    assert format_scene_error(raised.value, scene).line == 5


def test_a_top_level_error_while_importing_the_scene_gives_its_line(tmp_path: Path) -> None:
    scene = _write(
        tmp_path,
        "typo_name.py",
        HEADER + "undefined_name\n\nclass Demo(Scene):\n    def construct(self):\n        pass\n",
    )
    with pytest.raises(Exception) as raised:
        load_scene_module(scene)
    assert format_scene_error(raised.value, scene).line == 3


def test_an_error_inside_an_imported_helper_points_at_the_import_in_the_scene(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.syspath_prepend(str(tmp_path))
    _write(tmp_path, "scene_helper_boom.py", "raise RuntimeError('boom')\n")
    scene = _write(
        tmp_path,
        "uses_helper.py",
        HEADER
        + "import scene_helper_boom\n\n"
        + "class Demo(Scene):\n    def construct(self):\n        pass\n",
    )
    with pytest.raises(Exception) as raised:
        load_scene_module(scene)
    assert format_scene_error(raised.value, scene).line == 3


def test_a_runtime_error_in_the_scene_keeps_its_line(tmp_path: Path) -> None:
    scene = _write(tmp_path, "plain.py", "x = 1\n\nraise ValueError('no')\n")
    with pytest.raises(ValueError) as raised:
        exec(compile(scene.read_text(encoding="utf-8"), str(scene), "exec"), {})
    assert format_scene_error(raised.value, scene).line == 3


def test_an_error_that_did_not_happen_in_the_scene_has_no_line(tmp_path: Path) -> None:
    scene = _write(tmp_path, "unrelated.py", "x = 1\n")
    assert format_scene_error(RuntimeError("elsewhere"), scene).line is None


def test_the_banner_offers_go_to_line_for_a_syntax_error_from_the_load_path(
    qtbot, tmp_path: Path
) -> None:  # type: ignore[no-untyped-def]
    """The reported case end to end: the payload the host sends reaches the window's banner."""
    scene = _write(
        tmp_path,
        "typo_banner.py",
        HEADER + "class Demo(Scene):\n    def construct(self):\n        self.play(\n",
    )
    with pytest.raises(Exception) as raised:
        load_scene_module(scene)
    payload = format_scene_error(raised.value, scene).to_dict()

    window = MainWindow(restore_settings=False, auto_preview=False, completion=False)
    qtbot.addWidget(window)
    window.controller.errorReceived.emit(payload)

    banner = window.preview_panel.findChild(ErrorBanner)
    assert banner is not None
    assert not banner.isHidden()
    assert banner.line == 5
    goto = [b for b in banner.findChildren(QPushButton) if b.text() == "Go to line"]
    assert len(goto) == 1
    assert not goto[0].isHidden()
