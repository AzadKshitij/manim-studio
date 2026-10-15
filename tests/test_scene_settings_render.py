"""Scene settings reach the render: the export plan, and a real portrait still (GL)."""

from __future__ import annotations

import struct
import subprocess
from pathlib import Path

import pytest

from manim_studio.export import build_export_plan
from manim_studio.scene_settings import SceneSettings
from manim_studio.sidecar import save_scene_settings

SCENE = (
    "from manimlib import *\n\n\n"
    "class Portrait(Scene):\n"
    "    def construct(self):\n"
    "        self.add(Square(side_length=2, color=BLUE))\n"
    "        self.wait()\n"
)


@pytest.fixture
def scene(tmp_path: Path) -> Path:
    path = tmp_path / "portrait.py"
    path.write_text(SCENE, encoding="utf-8")
    save_scene_settings(path, {"Portrait": SceneSettings(width=1080, height=1920)})
    return path


def test_the_export_plan_hands_the_settings_to_the_launcher(scene: Path) -> None:
    plan = build_export_plan(scene, "Portrait", still=True)
    argv = list(plan.argv)
    assert "--settings-file" in argv
    assert "-r" not in argv


def test_an_explicit_resolution_still_wins_over_the_settings(scene: Path) -> None:
    plan = build_export_plan(scene, "Portrait", still=True, resolution=(640, 480))
    assert "--settings-file" in list(plan.argv)
    assert plan.resolution == (640, 480)


def _png_size(path: Path) -> tuple[int, int]:
    width, height = struct.unpack(">II", path.read_bytes()[16:24])
    return width, height


@pytest.mark.gl
def test_a_portrait_still_renders_at_portrait_size(scene: Path, tmp_path: Path) -> None:
    output = tmp_path / "portrait.png"
    plan = build_export_plan(scene, "Portrait", output=output, still=True)
    completed = subprocess.run(
        list(plan.argv), capture_output=True, text=True, timeout=240, check=False
    )
    assert completed.returncode == 0, completed.stderr[-2000:]
    assert _png_size(output) == (1080, 1920)


def test_the_host_adopts_the_size_and_fps_of_its_settings(scene: Path) -> None:
    from manim_studio.preview_host import HostConfig, SceneHost

    portrait = SceneSettings(width=1080, height=1920, fps=24)
    host = SceneHost(HostConfig(scene_path=scene, embedded=True, settings=portrait))
    assert host.config.size == (1080, 1920)
    assert host.config.fps == 24


def test_the_host_command_line_reads_a_settings_file(scene: Path) -> None:
    from manim_studio.preview_host import _read_settings, build_parser
    from manim_studio.sidecar import write_settings_file

    portrait = SceneSettings(width=1080, height=1920, background_color="#1E1E2E")
    path = write_settings_file(portrait)
    args = build_parser().parse_args([str(scene), "--settings-file", str(path)])
    assert _read_settings(args.settings_file) == portrait
    assert _read_settings(None) is None
