"""Unit tests for the export launcher's retiming argument handling."""

from __future__ import annotations

from pathlib import Path

from manim_studio.manimgl_launcher import _consume_timing_arg, _load_factors


class TestConsumeTimingArg:
    def test_space_separated_form_is_consumed(self, tmp_path: Path) -> None:
        config = tmp_path / "timing.json"
        config.write_text('{"0": 2.0, "3": 0.5}', encoding="utf-8")
        argv = ["prog", "scene.py", "--timing-file", str(config), "-w"]
        assert _consume_timing_arg(argv) == {0: 2.0, 3: 0.5}
        assert argv == ["prog", "scene.py", "-w"]  # both tokens removed

    def test_equals_form_is_consumed(self, tmp_path: Path) -> None:
        config = tmp_path / "timing.json"
        config.write_text('{"1": 1.5}', encoding="utf-8")
        argv = ["prog", f"--timing-file={config}", "-w"]
        assert _consume_timing_arg(argv) == {1: 1.5}
        assert argv == ["prog", "-w"]

    def test_absent_flag_returns_empty(self) -> None:
        assert _consume_timing_arg(["prog", "scene.py", "-w"]) == {}

    def test_flag_without_value_returns_empty(self) -> None:
        argv = ["prog", "--timing-file"]
        assert _consume_timing_arg(argv) == {}


class TestLoadFactors:
    def test_missing_file_returns_empty(self, tmp_path: Path) -> None:
        assert _load_factors(tmp_path / "nope.json") == {}

    def test_invalid_json_returns_empty(self, tmp_path: Path) -> None:
        config = tmp_path / "bad.json"
        config.write_text("{nope", encoding="utf-8")
        assert _load_factors(config) == {}

    def test_non_dict_returns_empty(self, tmp_path: Path) -> None:
        config = tmp_path / "list.json"
        config.write_text("[1, 2, 3]", encoding="utf-8")
        assert _load_factors(config) == {}

    def test_non_numeric_entries_are_skipped(self, tmp_path: Path) -> None:
        config = tmp_path / "mixed.json"
        config.write_text('{"0": 2.0, "1": "nope"}', encoding="utf-8")
        assert _load_factors(config) == {0: 2.0}
