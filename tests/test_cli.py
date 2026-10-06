"""CLI tests: every agent-facing command."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from manim_editor.cli import _ensure_utf8_console, main

SCENE_FILE = Path(__file__).parent / "scenes" / "feature_showcase.py"


class TestConsole:
    def test_reconfigures_streams_to_utf8(self) -> None:
        class FakeStream:
            def __init__(self) -> None:
                self.calls: list[dict] = []

            def reconfigure(self, **kwargs: object) -> None:
                self.calls.append(kwargs)

        streams = (FakeStream(), FakeStream())
        original = (sys.stdout, sys.stderr)
        sys.stdout, sys.stderr = streams  # type: ignore[assignment]
        try:
            _ensure_utf8_console()
        finally:
            sys.stdout, sys.stderr = original  # type: ignore[assignment]
        assert all(call.get("encoding") == "utf-8" for stream in streams for call in stream.calls)

    def test_plain_templates_output(self, capsys) -> None:
        assert main(["templates"]) == 0
        assert "Circle → Square" in capsys.readouterr().out


class TestCheck:
    def test_ok(self, capsys) -> None:
        assert main(["check", str(SCENE_FILE)]) == 0
        out = capsys.readouterr().out
        assert "OK" in out and "FeatureShowcase" in out

    def test_json(self, capsys) -> None:
        assert main(["check", str(SCENE_FILE), "--json"]) == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["ok"] is True
        assert payload["scenes"][0]["name"] == "FeatureShowcase"

    def test_syntax_error(self, capsys, tmp_path: Path) -> None:
        broken = tmp_path / "broken.py"
        broken.write_text("def f(:\n", encoding="utf-8")
        assert main(["check", str(broken)]) == 1
        assert "error" in capsys.readouterr().out.lower()

    def test_no_scenes(self, tmp_path: Path) -> None:
        empty = tmp_path / "empty.py"
        empty.write_text("x = 1\n", encoding="utf-8")
        assert main(["check", str(empty)]) == 1


class TestNew:
    def test_list_templates(self, capsys) -> None:
        assert main(["new", "--list", "unused.py"]) == 0
        assert "circle_to_square" in capsys.readouterr().out

    def test_create_and_check(self, capsys, tmp_path: Path) -> None:
        target = tmp_path / "my_scene.py"
        assert main(["new", str(target), "--template", "blank"]) == 0
        assert target.is_file()
        capsys.readouterr()  # discard the "created ..." line
        assert main(["check", str(target), "--json"]) == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["scenes"][0]["name"] == "MyScene"

    def test_explicit_scene_name(self, tmp_path: Path) -> None:
        target = tmp_path / "whatever.py"
        assert main(["new", str(target), "--scene-name", "CustomScene"]) == 0
        assert "class CustomScene(Scene):" in target.read_text(encoding="utf-8")

    def test_refuses_overwrite(self, tmp_path: Path) -> None:
        target = tmp_path / "exists.py"
        target.write_text("x = 1\n", encoding="utf-8")
        assert main(["new", str(target)]) == 1
        assert main(["new", str(target), "--force"]) == 0

    def test_unknown_template_is_usage_error(self, tmp_path: Path) -> None:
        with pytest.raises(SystemExit) as excinfo:
            main(["new", str(tmp_path / "x.py"), "--template", "nope"])
        assert excinfo.value.code == 2


class TestTemplates:
    def test_json_payload(self, capsys) -> None:
        assert main(["templates", "--json"]) == 0
        payload = json.loads(capsys.readouterr().out)
        keys = [item["key"] for item in payload["scene"]]
        assert "circle_to_square" in keys
        assert any(item["key"] == "wait" for item in payload["snippet"])


class TestApiIndex:
    def test_json_contains_circle(self, capsys) -> None:
        assert main(["api-index", "--json"]) == 0
        payload = json.loads(capsys.readouterr().out)
        names = {item["name"] for item in payload}
        assert {"Circle", "ShowCreation", "Scene"} <= names

    def test_plain_list(self, capsys) -> None:
        assert main(["api-index"]) == 0
        assert "Circle" in capsys.readouterr().out


class TestExportErrors:
    def test_missing_file(self, tmp_path: Path) -> None:
        assert main(["export", str(tmp_path / "nope.py")]) == 1

    def test_bad_quality_is_usage_error(self, tmp_path: Path) -> None:
        target = tmp_path / "scene.py"
        target.write_text("from manimlib import Scene\n")
        with pytest.raises(SystemExit) as excinfo:
            main(["export", str(target), "--quality", "ultra"])
        assert excinfo.value.code == 2


@pytest.mark.manim
class TestTraceCommand:
    def test_trace_json(self, capsys) -> None:
        assert main(["trace", str(SCENE_FILE), "--json"]) == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["scene"] == "FeatureShowcase"
        assert len(payload["blocks"]) >= 12
        assert payload["stats"]["play"] >= 10

    def test_trace_writes_log(self, tmp_path: Path) -> None:
        log_path = tmp_path / "log.json"
        assert main(["trace", str(SCENE_FILE), "--log", str(log_path)]) == 0
        blocks = json.loads(log_path.read_text(encoding="utf-8"))
        assert isinstance(blocks, list) and blocks[0]["name"]

    def test_trace_unknown_scene(self, capsys) -> None:
        assert main(["trace", str(SCENE_FILE), "--scene", "Nope"]) == 1

    def test_trace_syntax_error(self, tmp_path: Path) -> None:
        broken = tmp_path / "broken.py"
        broken.write_text("def f(:\n", encoding="utf-8")
        assert main(["trace", str(broken)]) == 1


@pytest.mark.manim
class TestStillCommand:
    def test_still_written(self, capsys, tmp_path: Path) -> None:
        output = tmp_path / "frame.png"
        code = main(["still", str(SCENE_FILE), "-o", str(output), "--resolution", "160", "90"])
        assert code == 0
        assert output.is_file()
        assert str(output) in capsys.readouterr().out
