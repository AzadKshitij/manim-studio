"""Unit tests for the export planner and runner."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from manim_editor.export import (
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
        assert argv[:3] == [sys.executable, "-m", "manimlib"]
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

    def test_fps_flag(self, tmp_path: Path) -> None:
        plan = build_export_plan(_scene_file(tmp_path), fps=60)
        index = list(plan.argv).index("--fps")
        assert plan.argv[index + 1] == "60"

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
