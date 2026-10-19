"""agent_tools: the logic behind the MCP tools (no MCP, no GL: child processes are faked)."""

from __future__ import annotations

import ast
import io
import json
from pathlib import Path
from typing import Any

import pytest
from PIL import Image

from manim_studio import agent_tools, schema_store
from manim_studio.agent_tools import CliRun, ToolFailure
from manim_studio.api_index import ApiSymbol
from manim_studio.schema_probe import SCHEMA_VERSION

SCENE_FILE = Path(__file__).parent / "scenes" / "feature_showcase.py"
AGENT_TOOLS_SOURCE = Path(agent_tools.__file__)


def _param(name: str, *, default: Any = None, annotation: str = "float") -> dict[str, Any]:
    has_default = default is not None
    return {
        "name": name,
        "kind": "keyword_only",
        "annotation": annotation,
        "has_default": has_default,
        "default": default,
        "default_literal": has_default,
        "default_repr": repr(default) if has_default else None,
        "owner": "m.Circle",
        "default_type": "",
    }


def _schema_payload() -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "manimlib_version": "1.7.2",
        "exported": {"Circle": "m.Circle"},
        "colours": {"BLUE": "#58C4DD"},
        "classes": {
            "m.Circle": {
                "name": "Circle",
                "module": "m",
                "mro": ["m.Circle"],
                "params": [_param("radius", default=1.0), _param("stroke_color", annotation="")],
                "methods": [
                    {
                        "name": "shift",
                        "owner": "m.Circle",
                        "params": [_param("vector", annotation="ndarray")],
                        "doc": "Move the mobject.",
                    }
                ],
            }
        },
    }


def _png_bytes() -> bytes:
    buffer = io.BytesIO()
    Image.new("RGBA", (4, 3), "#336699").save(buffer, format="PNG")
    return buffer.getvalue()


class TestResolveInRoot:
    def test_relative_paths_start_at_the_root(self, tmp_path: Path) -> None:
        resolved = agent_tools.resolve_in_root(tmp_path, "scenes/a.py")
        assert resolved == (tmp_path / "scenes" / "a.py").resolve()

    def test_absolute_path_inside_the_root_is_fine(self, tmp_path: Path) -> None:
        inside = tmp_path / "a.py"
        assert agent_tools.resolve_in_root(tmp_path, inside) == inside.resolve()

    def test_parent_escape_is_refused(self, tmp_path: Path) -> None:
        root = tmp_path / "root"
        root.mkdir()
        with pytest.raises(ToolFailure, match="outside the workspace root"):
            agent_tools.resolve_in_root(root, "..\\..\\x.py")

    def test_absolute_path_elsewhere_is_refused(self, tmp_path: Path) -> None:
        root = tmp_path / "root"
        root.mkdir()
        with pytest.raises(ToolFailure, match="outside the workspace root"):
            agent_tools.resolve_in_root(root, tmp_path / "other.py")


class TestJsonHelpers:
    def test_last_json_picks_the_final_document(self) -> None:
        text = '{\n  "first": 1\n}\nnoise\n{\n  "second": 2\n}\n'
        assert agent_tools.last_json(text) == {"second": 2}

    def test_last_json_without_a_document_fails(self) -> None:
        with pytest.raises(ToolFailure, match="no JSON"):
            agent_tools.last_json("just text")

    def test_tail_keeps_the_end(self) -> None:
        assert agent_tools.tail("abcdef", limit=3) == "def"

    def test_prune_keeps_the_newest_images(self, tmp_path: Path) -> None:
        import os

        for index in range(5):
            image = tmp_path / f"img{index}.png"
            image.write_bytes(b"x")
            os.utime(image, (index, index))
        agent_tools.prune_old_images(tmp_path, keep=2)
        assert sorted(p.name for p in tmp_path.glob("*.png")) == ["img3.png", "img4.png"]


class TestCheck:
    def test_showcase_is_ok_and_reports_latex(self) -> None:
        payload = agent_tools.check(SCENE_FILE.parent, str(SCENE_FILE))
        assert payload["ok"] is True
        assert "latex" in payload

    def test_syntax_error_is_reported_not_raised(self, tmp_path: Path) -> None:
        broken = tmp_path / "broken.py"
        broken.write_text("def broken(:\n", encoding="utf-8")
        payload = agent_tools.check(tmp_path, "broken.py")
        assert payload["ok"] is False
        assert payload["error"]


class TestWriteAndNewScene:
    def test_write_scene_creates_the_file_and_checks_it(self, tmp_path: Path) -> None:
        source = (
            "from manimlib import *\n\n"
            "class Tiny(Scene):\n"
            "    def construct(self):\n"
            "        self.wait(1.0)\n"
        )
        payload = agent_tools.write_scene(tmp_path, "tiny.py", source)
        assert (tmp_path / "tiny.py").read_text(encoding="utf-8") == source
        assert payload["bytes"] == len(source.encode("utf-8"))
        assert payload["check"]["ok"] is True

    def test_write_scene_refuses_non_python(self, tmp_path: Path) -> None:
        with pytest.raises(ToolFailure, match=r"\.py"):
            agent_tools.write_scene(tmp_path, "notes.txt", "x")

    def test_write_scene_refuses_outside_the_root(self, tmp_path: Path) -> None:
        root = tmp_path / "root"
        root.mkdir()
        with pytest.raises(ToolFailure, match="outside"):
            agent_tools.write_scene(root, str(tmp_path / "escape.py"), "x = 1\n")
        assert not (tmp_path / "escape.py").exists()

    def test_write_scene_refuses_overwrite_when_asked(self, tmp_path: Path) -> None:
        (tmp_path / "a.py").write_text("x = 1\n", encoding="utf-8")
        with pytest.raises(ToolFailure, match="already exists"):
            agent_tools.write_scene(tmp_path, "a.py", "x = 2\n", overwrite=False)

    def test_write_scene_caps_the_size(self, tmp_path: Path) -> None:
        big = "x = 1\n" * (agent_tools.MAX_SOURCE_CHARS // 6 + 1)
        with pytest.raises(ToolFailure, match="limit"):
            agent_tools.write_scene(tmp_path, "big.py", big)

    def test_new_scene_uses_a_template_and_derives_the_class_name(self, tmp_path: Path) -> None:
        payload = agent_tools.new_scene(tmp_path, "my_circle.py")
        assert payload["scene_name"] == "MyCircle"
        assert "class MyCircle" in (tmp_path / "my_circle.py").read_text(encoding="utf-8")
        assert payload["check"]["ok"] is True

    def test_new_scene_refuses_existing_files_unless_overwrite(self, tmp_path: Path) -> None:
        agent_tools.new_scene(tmp_path, "scene_a.py")
        with pytest.raises(ToolFailure, match="already exists"):
            agent_tools.new_scene(tmp_path, "scene_a.py")
        assert agent_tools.new_scene(tmp_path, "scene_a.py", overwrite=True)["check"]["ok"]

    def test_new_scene_unknown_template_is_a_tool_failure(self, tmp_path: Path) -> None:
        with pytest.raises(ToolFailure):
            agent_tools.new_scene(tmp_path, "x.py", template="no_such_template")


class TestListTemplates:
    def test_lists_scene_and_snippet_templates(self) -> None:
        payload = agent_tools.list_templates()
        assert payload["scene"]
        assert payload["snippet"]
        assert {"key", "title", "description"} <= set(payload["scene"][0])


class TestApiIndex:
    SYMBOLS = (
        ApiSymbol(name="Circle", kind="class", module="manimlib.a", lineno=1),
        ApiSymbol(name="Square", kind="class", module="manimlib.a", lineno=2),
        ApiSymbol(name="FadeIn", kind="class", module="manimlib.b", lineno=3),
    )

    def test_query_filters_case_insensitively(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(agent_tools, "build_index", lambda: self.SYMBOLS)
        payload = agent_tools.api_index("fade")
        assert payload["matched"] == 1
        assert [s["name"] for s in payload["symbols"]] == ["FadeIn"]
        assert payload["total"] == 3

    def test_limit_caps_the_reply(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(agent_tools, "build_index", lambda: self.SYMBOLS)
        payload = agent_tools.api_index(limit=2)
        assert payload["returned"] == 2
        assert payload["matched"] == 3


class TestDescribeMobject:
    @pytest.fixture
    def schema(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from manim_studio.mobject_schema import parse_schema

        monkeypatch.setattr(
            schema_store, "load_schema", lambda *a, **k: parse_schema(_schema_payload())
        )

    def test_known_class_lists_constructor_and_methods(self, schema: None) -> None:
        payload = agent_tools.describe_mobject("Circle")
        assert payload["name"] == "Circle"
        assert [p["name"] for p in payload["constructor"]] == ["radius", "stroke_color"]
        assert payload["constructor"][0]["default"] == 1.0
        assert payload["methods"] == [
            {
                "name": "shift",
                "owner": "m.Circle",
                "signature": "(vector)",
                "doc": "Move the mobject.",
            }
        ]
        assert "colours" not in payload

    def test_colours_are_included_on_request(self, schema: None) -> None:
        payload = agent_tools.describe_mobject(
            "Circle", include_methods=False, include_colours=True
        )
        assert payload["colours"] == {"BLUE": "#58C4DD"}
        assert "methods" not in payload

    def test_unknown_class_suggests_close_names(self, schema: None) -> None:
        with pytest.raises(ToolFailure, match="did you mean"):
            agent_tools.describe_mobject("Circel")


def _fake_cli(
    monkeypatch: pytest.MonkeyPatch,
    *,
    returncode: int = 0,
    stdout: str = "",
    stderr: str = "",
    timeout: bool = False,
) -> list[list[str]]:
    """Replace the child process. A fake frame/sheet writes a PNG to the ``-o`` path it is given."""
    calls: list[list[str]] = []
    png = _png_bytes()

    def fake_run(args: list[str], *, timeout: float, cwd: Path | None = None) -> CliRun:
        calls.append(list(args))
        if expire:
            raise ToolFailure(f"timed out after {timeout:.0f}s")
        if "-o" in args:
            Path(args[args.index("-o") + 1]).write_bytes(png)
        return CliRun(returncode, stdout, stderr)

    expire = timeout
    monkeypatch.setattr(agent_tools, "run_cli", fake_run)
    return calls


class TestFrameAndContactSheet:
    def test_frame_returns_metadata_and_the_png(self, tmp_path: Path, monkeypatch) -> None:
        calls = _fake_cli(monkeypatch, stdout=json.dumps({"time": 0.5, "scene": "S"}))
        result = agent_tools.frame(tmp_path, "scene.py", 0.5)
        assert result.meta == {"time": 0.5, "scene": "S"}
        assert result.png.startswith(b"\x89PNG")
        assert result.path.is_file()
        assert "--at" in calls[0]
        assert calls[0][calls[0].index("--at") + 1] == "0.5"
        assert "--json" in calls[0]

    def test_frame_passes_size_and_sidecar_flags(self, tmp_path: Path, monkeypatch) -> None:
        calls = _fake_cli(monkeypatch, stdout='{"time": 1.0}')
        agent_tools.frame(tmp_path, "scene.py", 1.0, width=160, height=90, use_sidecar=False)
        argv = calls[0]
        assert argv[argv.index("--resolution") + 1 : argv.index("--resolution") + 3] == [
            "160",
            "90",
        ]
        assert "--no-sidecar" in argv

    def test_contact_sheet_uses_times_when_given(self, tmp_path: Path, monkeypatch) -> None:
        calls = _fake_cli(monkeypatch, stdout='{"frames": []}')
        result = agent_tools.contact_sheet(tmp_path, "scene.py", times=[0.0, 2.5])
        assert result.png
        argv = calls[0]
        assert "--times" in argv and "--every" not in argv
        assert argv[argv.index("--times") + 1] == "0.0,2.5"

    def test_contact_sheet_uses_every_by_default(self, tmp_path: Path, monkeypatch) -> None:
        calls = _fake_cli(monkeypatch, stdout='{"frames": []}')
        agent_tools.contact_sheet(tmp_path, "scene.py")
        assert "--every" in calls[0] and "--times" not in calls[0]

    def test_nonzero_exit_carries_the_stderr_tail(self, tmp_path: Path, monkeypatch) -> None:
        _fake_cli(monkeypatch, returncode=1, stderr="Traceback...\nNameError: boom\n")
        with pytest.raises(ToolFailure, match="NameError: boom"):
            agent_tools.frame(tmp_path, "scene.py", 0.0)

    def test_timeout_is_a_tool_failure(self, tmp_path: Path, monkeypatch) -> None:
        _fake_cli(monkeypatch, timeout=True)
        with pytest.raises(ToolFailure, match="timed out"):
            agent_tools.frame(tmp_path, "scene.py", 0.0)

    def test_missing_output_file_is_a_tool_failure(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.setattr(agent_tools, "run_cli", lambda *a, **k: CliRun(0, '{"ok": true}', ""))
        with pytest.raises(ToolFailure, match="wrote no image"):
            agent_tools.frame(tmp_path, "scene.py", 0.0)


class TestTrace:
    def test_long_event_lists_are_replaced_with_a_count(self, tmp_path: Path, monkeypatch) -> None:
        payload = {"scene": "S", "blocks": [], "events": [{"i": i} for i in range(300)]}
        _fake_cli(monkeypatch, stdout=json.dumps(payload))
        result = agent_tools.trace(tmp_path, "scene.py")
        assert result["events"] == {"omitted": 300}
        assert result["scene"] == "S"

    def test_short_event_lists_are_kept(self, tmp_path: Path, monkeypatch) -> None:
        payload = {"scene": "S", "events": [{"i": 1}]}
        _fake_cli(monkeypatch, stdout=json.dumps(payload))
        assert agent_tools.trace(tmp_path, "scene.py")["events"] == [{"i": 1}]


class TestExport:
    def test_missing_scene_is_a_tool_failure(self, tmp_path: Path) -> None:
        with pytest.raises(ToolFailure):
            agent_tools.export(tmp_path, "missing.py")

    def test_output_outside_the_root_is_refused(self, tmp_path: Path) -> None:
        root = tmp_path / "root"
        root.mkdir()
        with pytest.raises(ToolFailure, match="outside the workspace root"):
            agent_tools.export(root, "scene.py", output=str(tmp_path / "out.mp4"))

    @pytest.mark.parametrize(
        ("kwargs", "suffix"),
        [({}, ".mp4"), ({"still": True}, ".png"), ({"gif": True}, ".gif")],
    )
    def test_default_output_sits_next_to_the_scene(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kwargs: dict[str, bool], suffix: str
    ) -> None:
        # The default must not fall back to the server's working directory (often outside root).
        seen: dict[str, Any] = {}

        def fake_plan(scene_path: Path, scene_name: Any = None, **options: Any) -> str:
            seen["output"] = options["output"]
            return "plan"

        class Finished:
            ok = True
            returncode = 0
            output_path = Path("unused")
            log_tail: tuple[str, ...] = ()

        monkeypatch.setattr(agent_tools, "build_export_plan", fake_plan)
        monkeypatch.setattr(agent_tools, "run_export", lambda plan, **options: Finished())
        agent_tools.export(tmp_path, "scene.py", **kwargs)
        assert seen["output"] == (tmp_path / f"scene{suffix}").resolve()


class TestSourceGuards:
    @pytest.mark.parametrize("module", ["agent_tools.py", "mcp_server.py"])
    def test_nothing_writes_to_stdout(self, module: str) -> None:
        # Stdout is the MCP wire: a stray print would corrupt the protocol stream.
        path = Path(agent_tools.__file__).with_name(module)
        tree = ast.parse(path.read_text(encoding="utf-8"))
        prints = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "print"
        ]
        assert prints == []

    def test_agent_tools_imports_no_mcp_and_no_gl(self) -> None:
        tree = ast.parse(AGENT_TOOLS_SOURCE.read_text(encoding="utf-8"))
        imported: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module)
        assert not {name.split(".")[0] for name in imported} & {"mcp", "manimlib", "moderngl"}
