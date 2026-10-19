"""CLI tests: every agent-facing command."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from manim_studio.cli import _ensure_utf8_console, _parse_range, _parse_resolution, main

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

    def test_bad_resolution_is_usage_error(self, tmp_path: Path, capsys) -> None:
        target = tmp_path / "scene.py"
        target.write_text("from manimlib import Scene\n")
        assert main(["export", str(target), "--resolution", "wide"]) == 2
        assert "invalid --resolution" in capsys.readouterr().err

    def test_bad_range_is_usage_error(self, tmp_path: Path, capsys) -> None:
        target = tmp_path / "scene.py"
        target.write_text("from manimlib import Scene\n")
        assert main(["export", str(target), "--range", "5"]) == 2
        assert "invalid --range" in capsys.readouterr().err


class TestExportOptionParsing:
    def test_resolution_forms(self) -> None:
        assert _parse_resolution("360x240") == (360, 240)
        assert _parse_resolution(" 360X240 ") == (360, 240)
        assert _parse_resolution(None) is None
        assert _parse_resolution("360") is None
        assert _parse_resolution("axb") is None

    def test_range_forms(self) -> None:
        assert _parse_range("2,5") == (2, 5)
        assert _parse_range(" 2 , 5 ") == (2, 5)
        assert _parse_range(None) is None
        assert _parse_range("2") is None
        assert _parse_range("a,b") is None


class TestFrameCommands:
    """``frame`` and ``contact-sheet``: argument handling and output shape, with fake frames."""

    @pytest.fixture
    def fake_frames(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from PIL import Image

        from manim_studio import frames
        from manim_studio.frames import PreparedScene
        from manim_studio.timeline_model import TimelineBlock

        prepared = PreparedScene(
            scene_class=type("Fake", (), {}),
            scene_name="Fake",
            scene_path=Path("fake.py"),
            blocks=(
                TimelineBlock(index=0, name="Create", start=0.0, duration=1.0),
                TimelineBlock(index=1, name="Wait", start=1.0, duration=2.0),
            ),
            total_duration=3.0,
            fps=30,
            resolution=(64, 36),
            settings=None,
            patches_applied=0,
            patches_orphaned=0,
        )
        monkeypatch.setattr(frames, "prepare_scene", lambda *args, **kwargs: prepared)
        monkeypatch.setattr(
            frames,
            "render_frames",
            lambda prep, times: [Image.new("RGBA", prep.resolution, "#336699") for _ in times],
        )

    def test_frame_needs_a_time(self) -> None:
        with pytest.raises(SystemExit) as info:
            main(["frame", str(SCENE_FILE)])
        assert info.value.code == 2

    def test_frame_negative_time_is_usage_error(self, fake_frames, capsys) -> None:
        assert main(["frame", str(SCENE_FILE), "--at", "-1"]) == 2
        assert "non-negative" in capsys.readouterr().err

    def test_frame_writes_png_and_json(self, fake_frames, tmp_path: Path, capsys) -> None:
        from PIL import Image

        target = tmp_path / "out" / "frame.png"
        assert main(["frame", str(SCENE_FILE), "--at", "1.5", "-o", str(target), "--json"]) == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["output"] == str(target.resolve())
        assert payload["block_name"] == "Wait"
        assert payload["clamped"] is False
        with Image.open(target) as image:
            assert image.size == (64, 36)

    def test_frame_plain_output_is_only_the_path(self, fake_frames, tmp_path: Path, capsys) -> None:
        target = tmp_path / "frame.png"
        assert main(["frame", str(SCENE_FILE), "--at", "99", "-o", str(target)]) == 0
        assert capsys.readouterr().out.strip() == str(target.resolve())

    def test_frame_default_output_is_a_named_temp_png(self, fake_frames, capsys) -> None:
        assert main(["frame", str(SCENE_FILE), "--at", "0.5", "--json"]) == 0
        payload = json.loads(capsys.readouterr().out)
        assert Path(payload["output"]).is_file()
        assert Path(payload["output"]).name == "Fake_t0.50.png"

    def test_broken_scene_is_a_scene_error(self, tmp_path: Path, capsys) -> None:
        broken = tmp_path / "broken.py"
        broken.write_text("def broken(:\n", encoding="utf-8")
        assert main(["frame", str(broken), "--at", "1"]) == 1
        assert "error" in capsys.readouterr().err

    def test_contact_sheet_json_lists_every_tile(self, fake_frames, tmp_path: Path, capsys) -> None:
        target = tmp_path / "sheet.png"
        argv = ["contact-sheet", str(SCENE_FILE), "--every", "1", "-o", str(target), "--json"]
        assert main(argv) == 0
        payload = json.loads(capsys.readouterr().out)
        assert [frame["time"] for frame in payload["frames"]] == [0.0, 1.0, 2.0, 3.0]
        assert payload["output"] == str(target.resolve())
        assert target.is_file()

    def test_contact_sheet_explicit_times(self, fake_frames, capsys) -> None:
        assert main(["contact-sheet", str(SCENE_FILE), "--times", "2.5,0", "--json"]) == 0
        payload = json.loads(capsys.readouterr().out)
        assert [frame["time"] for frame in payload["frames"]] == [0.0, 2.5]
        assert payload["step"] is None

    @pytest.mark.parametrize(
        "argv",
        [
            ["contact-sheet", "x.py", "--every", "0"],
            ["contact-sheet", "x.py", "--times", "abc"],
            ["contact-sheet", "x.py", "--times", "1,,2"],
        ],
    )
    def test_bad_sampling_is_usage_error(self, argv: list[str]) -> None:
        assert main(argv) == 2

    def test_every_and_times_are_exclusive(self) -> None:
        with pytest.raises(SystemExit) as info:
            main(["contact-sheet", str(SCENE_FILE), "--every", "1", "--times", "0"])
        assert info.value.code == 2


@pytest.mark.manim
class TestFrameCommandsGL:
    @pytest.mark.gl
    def test_frame_renders_the_real_scene_at_the_requested_size(self, tmp_path: Path) -> None:
        from PIL import Image

        target = tmp_path / "frame.png"
        argv = ["frame", str(SCENE_FILE), "--at", "0.5", "-o", str(target)]
        assert main([*argv, "--resolution", "160", "90"]) == 0
        with Image.open(target) as image:
            assert image.size == (160, 90)

    @pytest.mark.gl
    def test_contact_sheet_json_on_the_real_scene(self, capsys) -> None:
        argv = ["contact-sheet", str(SCENE_FILE), "--every", "1", "--json"]
        assert main([*argv, "--resolution", "160", "90"]) == 0
        payload = json.loads(capsys.readouterr().out)
        assert len(payload["frames"]) >= 2


@pytest.mark.manim
class TestTraceCommand:
    @pytest.mark.gl
    def test_trace_json(self, capsys) -> None:
        assert main(["trace", str(SCENE_FILE), "--json"]) == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["scene"] == "FeatureShowcase"
        assert len(payload["blocks"]) >= 12
        assert payload["stats"]["play"] >= 10

    @pytest.mark.gl
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
    @pytest.mark.gl
    def test_still_written(self, capsys, tmp_path: Path) -> None:
        output = tmp_path / "frame.png"
        code = main(["still", str(SCENE_FILE), "-o", str(output), "--resolution", "160", "90"])
        assert code == 0
        assert output.is_file()
        assert str(output) in capsys.readouterr().out
