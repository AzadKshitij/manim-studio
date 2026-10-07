"""Unit tests for the export planner and runner."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from manim_studio.export import (
    ExportError,
    ExportPlan,
    build_export_plan,
    run_export,
)


def _scene_file(tmp_path: Path) -> Path:
    path = tmp_path / "scene.py"
    path.write_text("from manimlib import Scene\n\n\nclass DemoScene(Scene):\n    pass\n")
    return path


class TestBuildExportPlan:
    def test_default_plan(self, tmp_path: Path) -> None:
        scene = _scene_file(tmp_path)
        plan = build_export_plan(scene, "DemoScene")
        argv = list(plan.argv)
        assert argv[:3] == [sys.executable, "-m", "manim_studio.manimgl_launcher"]
        assert str(scene) in argv
        assert "DemoScene" in argv
        assert "-w" in argv and "--quiet" in argv
        assert "--hd" in argv
        assert plan.output_path == Path.cwd() / "videos" / "DemoScene.mp4"

    @pytest.mark.parametrize(
        ("quality", "flag"),
        [("preview", "-l"), ("low", "-l"), ("medium", "-m"), ("high", "--hd"), ("4k", "--uhd")],
    )
    def test_quality_presets(self, tmp_path: Path, quality: str, flag: str) -> None:
        plan = build_export_plan(_scene_file(tmp_path), quality=quality)
        assert flag in plan.argv

    def test_fps_uses_generated_config_file(self, tmp_path: Path) -> None:
        plan = build_export_plan(_scene_file(tmp_path), fps=60)
        argv = list(plan.argv)
        assert "--fps" not in argv  # manimgl 1.7.2 writes a string otherwise
        index = argv.index("--config_file")
        config_path = Path(argv[index + 1])
        assert config_path.is_file()
        assert "fps: 60" in config_path.read_text(encoding="utf-8")

    def test_fps_out_of_range_rejected(self, tmp_path: Path) -> None:
        with pytest.raises(ExportError, match="out of range"):
            build_export_plan(_scene_file(tmp_path), fps=1000)

    def test_explicit_output_derives_dir_and_name(self, tmp_path: Path) -> None:
        scene = _scene_file(tmp_path)
        target = tmp_path / "renders" / "my_video.mp4"
        plan = build_export_plan(scene, "DemoScene", output=target)
        argv = list(plan.argv)
        assert plan.output_path == target
        assert argv[argv.index("--video_dir") + 1] == str(target.parent)
        assert argv[argv.index("--file_name") + 1] == "my_video"

    def test_output_without_extension_gets_mp4(self, tmp_path: Path) -> None:
        plan = build_export_plan(_scene_file(tmp_path), output=tmp_path / "out")
        assert plan.output_path.suffix == ".mp4"

    def test_still_plan(self, tmp_path: Path) -> None:
        plan = build_export_plan(_scene_file(tmp_path), output=tmp_path / "frame.png", still=True)
        assert "-s" in plan.argv
        assert plan.output_path.suffix == ".png"
        assert plan.still

    def test_still_rejects_fps(self, tmp_path: Path) -> None:
        with pytest.raises(ExportError, match="fps"):
            build_export_plan(_scene_file(tmp_path), fps=30, still=True)

    def test_gif_plan(self, tmp_path: Path) -> None:
        plan = build_export_plan(_scene_file(tmp_path), output=tmp_path / "loop", gif=True)
        assert "-i" in plan.argv
        assert plan.gif
        assert plan.output_path.suffix == ".gif"

    def test_gif_rejects_still(self, tmp_path: Path) -> None:
        with pytest.raises(ExportError, match="mutually exclusive"):
            build_export_plan(_scene_file(tmp_path), still=True, gif=True)

    def test_gif_rejects_non_gif_output(self, tmp_path: Path) -> None:
        with pytest.raises(ExportError, match="gif"):
            build_export_plan(_scene_file(tmp_path), output=tmp_path / "mix.mp4", gif=True)

    def test_custom_resolution_skips_preset_flag(self, tmp_path: Path) -> None:
        plan = build_export_plan(_scene_file(tmp_path), resolution=(360, 240))
        assert "-r" in plan.argv and "360x240" in plan.argv
        assert "--hd" not in plan.argv
        assert plan.resolution == (360, 240)

    def test_resolution_out_of_range_rejected(self, tmp_path: Path) -> None:
        with pytest.raises(ExportError, match="out of range"):
            build_export_plan(_scene_file(tmp_path), resolution=(8192, 1080))

    def test_animation_range_flag(self, tmp_path: Path) -> None:
        plan = build_export_plan(_scene_file(tmp_path), animation_range=(2, 5))
        assert "-n" in plan.argv and "2,5" in plan.argv
        assert plan.animation_range == (2, 5)

    def test_animation_range_backwards_rejected(self, tmp_path: Path) -> None:
        with pytest.raises(ExportError, match="range"):
            build_export_plan(_scene_file(tmp_path), animation_range=(5, 2))

    def test_still_with_animation_range(self, tmp_path: Path) -> None:
        plan = build_export_plan(
            _scene_file(tmp_path),
            output=tmp_path / "frame.png",
            still=True,
            animation_range=(0, 2),
        )
        assert plan.still
        assert "-s" in plan.argv
        assert "-n" in plan.argv and "0,2" in plan.argv

    def test_unknown_quality_rejected(self, tmp_path: Path) -> None:
        with pytest.raises(ExportError, match="unknown quality"):
            build_export_plan(_scene_file(tmp_path), quality="insane")

    def test_missing_scene_file_rejected(self, tmp_path: Path) -> None:
        with pytest.raises(ExportError, match="does not exist"):
            build_export_plan(tmp_path / "nope.py")

    def test_bad_extension_rejected(self, tmp_path: Path) -> None:
        with pytest.raises(ExportError, match="extension"):
            build_export_plan(_scene_file(tmp_path), output=tmp_path / "out.xyz")


class TestRunExport:
    def _plan(self, *code: str) -> ExportPlan:
        return ExportPlan(
            argv=(sys.executable, "-c", ";".join(code)),
            output_path=Path("unused.mp4"),
            scene_name=None,
            quality="low",
        )

    def test_success_streams_lines(self) -> None:
        seen: list[str] = []
        result = run_export(self._plan("print('hello export')"), on_line=seen.append)
        assert result.ok and result.returncode == 0
        assert "hello export" in seen
        assert any("hello export" in line for line in result.log_tail)

    def test_nonzero_exit_is_reported(self) -> None:
        result = run_export(self._plan("import sys; sys.exit(3)"))
        assert not result.ok and result.returncode == 3

    def test_timeout_kills_process(self) -> None:
        result = run_export(self._plan("import time; time.sleep(30)"), timeout=0.5)
        assert result.returncode == -1
        assert any("timed out" in line for line in result.log_tail)

    def test_log_tail_is_bounded(self) -> None:
        result = run_export(
            self._plan("print('\\n'.join(str(i) for i in range(100)))"), log_tail_size=5
        )
        assert len(result.log_tail) == 5


@pytest.mark.slow
@pytest.mark.manim
class TestRealExport:
    """Actually renders a tiny mp4 through manimgl + ffmpeg."""

    SCENE_SOURCE = (
        "from manimlib import *\n\n\n"
        "class TinyScene(Scene):\n"
        "    def construct(self):\n"
        "        dot = Dot()\n"
        "        self.play(ShowCreation(dot), run_time=0.2)\n"
        "        self.wait(0.2)\n"
    )

    def test_mp4_written(self, tmp_path: Path) -> None:
        scene = tmp_path / "tiny_scene.py"
        scene.write_text(self.SCENE_SOURCE, encoding="utf-8")
        plan = build_export_plan(
            scene,
            "TinyScene",
            output=tmp_path / "tiny.mp4",
            quality="preview",
            fps=24,
            extra_args=("--resolution", "160x90"),
        )
        result = run_export(plan, timeout=300)
        assert result.ok, result.log_tail
        assert plan.output_path.is_file()
        assert plan.output_path.stat().st_size > 0
