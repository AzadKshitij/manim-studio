"""Unit tests for the Qt-free scene-file helpers."""

from __future__ import annotations

from pathlib import Path

import pytest

from manim_studio.editor import scene_files


class TestReadDiskText:
    def test_missing_file_returns_none(self, tmp_path: Path) -> None:
        assert scene_files.read_disk_text(tmp_path / "absent.py") is None

    def test_normalizes_crlf(self, tmp_path: Path) -> None:
        target = tmp_path / "scene.py"
        target.write_bytes(b"a = 1\r\nb = 2\r")
        assert scene_files.read_disk_text(target) == "a = 1\nb = 2\n"


class TestBufferMatchesDisk:
    def test_equal_ignoring_line_endings(self, tmp_path: Path) -> None:
        target = tmp_path / "scene.py"
        target.write_bytes(b"class Demo:\r\n    pass\r\n")
        assert scene_files.buffer_matches_disk(target, "class Demo:\n    pass\n") is True

    def test_different_text(self, tmp_path: Path) -> None:
        target = tmp_path / "scene.py"
        target.write_text("a = 1\n", encoding="utf-8")
        assert scene_files.buffer_matches_disk(target, "a = 2\n") is False

    def test_missing_file(self, tmp_path: Path) -> None:
        assert scene_files.buffer_matches_disk(tmp_path / "absent.py", "x") is False


class TestPreviewVideoPath:
    def test_path_next_to_scene(self, tmp_path: Path) -> None:
        expected = tmp_path / ".manim-studio" / "demo_preview.mp4"
        assert scene_files.preview_video_path(tmp_path / "demo.py") == expected


class TestSceneNaming:
    def test_name_from_path(self) -> None:
        assert scene_files.scene_name_from_path(Path("my_intro_scene.py")) == "MyIntroScene"

    def test_hyphens_become_words(self) -> None:
        assert scene_files.scene_name_from_path(Path("my-intro.py")) == "MyIntro"

    def test_unusable_stem_falls_back(self) -> None:
        assert scene_files.scene_name_from_path(Path("123.py")) == "MyScene"

    def test_snake_case(self) -> None:
        assert scene_files.snake_case("MyIntroScene") == "my_intro_scene"

    def test_valid_names(self) -> None:
        assert scene_files.is_valid_scene_name("MyScene") is True
        assert scene_files.is_valid_scene_name("class") is False
        assert scene_files.is_valid_scene_name("not a name") is False
        assert scene_files.is_valid_scene_name("") is False


class TestCreateSceneFile:
    def test_derives_class_name_from_path(self, tmp_path: Path) -> None:
        target = scene_files.create_scene_file(tmp_path / "my_intro.py", "circle_to_square")
        text = target.read_text(encoding="utf-8")
        assert "class MyIntro(Scene):" in text

    def test_explicit_scene_name(self, tmp_path: Path) -> None:
        target = scene_files.create_scene_file(
            tmp_path / "whatever.py", "circle_to_square", "CustomName"
        )
        assert "class CustomName(Scene):" in target.read_text(encoding="utf-8")


class TestFormatSeconds:
    def test_common_values(self) -> None:
        assert scene_files.format_seconds(0.5) == "0.5"
        assert scene_files.format_seconds(1.0) == "1.0"
        assert scene_files.format_seconds(0.25) == "0.25"
        assert scene_files.format_seconds(2) == "2.0"

    def test_negative_is_clamped(self) -> None:
        assert scene_files.format_seconds(-1.0) == "0.0"


class TestWaitInsertion:
    SOURCE = (
        "class Demo(Scene):\n"
        "    def construct(self):\n"
        "        circle = Circle()\n"
        "        self.play(ShowCreation(circle))\n"
    )

    def test_before_matches_indentation(self) -> None:
        line, text = scene_files.wait_insertion(self.SOURCE, 4, 0.5, before=True)
        assert line == 4
        assert text == "        self.wait(0.5)"

    def test_after_goes_on_the_next_line(self) -> None:
        line, text = scene_files.wait_insertion(self.SOURCE, 4, 1.0, before=False)
        assert line == 5
        assert text == "        self.wait(1.0)"

    def test_out_of_range_line_raises(self) -> None:
        with pytest.raises(ValueError):
            scene_files.wait_insertion(self.SOURCE, 99, 0.5)
