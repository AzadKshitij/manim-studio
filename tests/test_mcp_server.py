"""mcp_server: the MCP tool surface, exercised in memory through the SDK client.

Needs the ``mcp`` package; the module is skipped when it is absent. Child processes are faked
except in the ``gl`` end-to-end test, which runs the real scene pipeline.
"""

from __future__ import annotations

import asyncio
import base64
import io
import json
from pathlib import Path
from typing import Any

import pytest
from mcp import Client
from PIL import Image

from manim_studio import agent_tools, mcp_server, schema_store
from manim_studio.agent_tools import CliRun

EXPECTED_TOOLS = {
    "check",
    "trace",
    "frame",
    "contact_sheet",
    "export",
    "list_templates",
    "api_index",
    "describe_mobject",
    "new_scene",
    "write_scene",
}

TWO_STEP = (
    "from manimlib import *\n\n\n"
    "class TwoStep(Scene):\n"
    "    def construct(self):\n"
    '        dot = Dot(color="#58C4DD")\n'
    "        self.play(FadeIn(dot), run_time=1.0)\n"
    "        self.wait(0.5)\n"
)


def _png() -> bytes:
    buffer = io.BytesIO()
    Image.new("RGBA", (8, 6), "#336699").save(buffer, format="PNG")
    return buffer.getvalue()


def _call(server: Any, name: str, arguments: dict[str, Any]) -> Any:
    async def go() -> Any:
        async with Client(server) as client:
            return await client.call_tool(name, arguments)

    return asyncio.run(go())


def _text_payload(result: Any) -> dict[str, Any]:
    if result.structured_content is not None:
        return result.structured_content
    return json.loads(result.content[0].text)


def _fake_cli(
    monkeypatch: pytest.MonkeyPatch, *, returncode: int = 0, stdout: str = "", stderr: str = ""
) -> None:
    png = _png()

    def fake_run(args: list[str], *, timeout: float, cwd: Path | None = None) -> CliRun:
        if "-o" in args:
            Path(args[args.index("-o") + 1]).write_bytes(png)
        return CliRun(returncode, stdout, stderr)

    monkeypatch.setattr(agent_tools, "run_cli", fake_run)


class TestSurface:
    def test_registers_the_ten_tools(self, tmp_path: Path) -> None:
        async def go() -> set[str]:
            async with Client(mcp_server.build_server(tmp_path)) as client:
                listing = await client.list_tools()
                return {tool.name for tool in listing.tools}

        assert asyncio.run(go()) == EXPECTED_TOOLS

    def test_list_templates_round_trips(self, tmp_path: Path) -> None:
        result = _call(mcp_server.build_server(tmp_path), "list_templates", {})
        assert result.is_error is False
        payload = _text_payload(result)
        assert payload["scene"] and payload["snippet"]

    def test_write_then_check_is_ok(self, tmp_path: Path) -> None:
        server = mcp_server.build_server(tmp_path)
        written = _call(server, "write_scene", {"file": "two.py", "code": TWO_STEP})
        assert written.is_error is False
        checked = _call(server, "check", {"file": "two.py"})
        assert _text_payload(checked)["ok"] is True

    def test_paths_outside_the_root_are_tool_errors(self, tmp_path: Path) -> None:
        root = tmp_path / "root"
        root.mkdir()
        result = _call(mcp_server.build_server(root), "check", {"file": "../outside.py"})
        assert result.is_error is True
        assert "outside" in result.content[0].text


class TestImageTools:
    def test_frame_returns_text_then_png(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _fake_cli(monkeypatch, stdout=json.dumps({"time": 0.5, "scene": "TwoStep"}))
        result = _call(mcp_server.build_server(tmp_path), "frame", {"file": "two.py", "at": 0.5})
        assert result.is_error is False
        kinds = [type(block).__name__ for block in result.content]
        assert kinds == ["TextContent", "ImageContent"]
        assert json.loads(result.content[0].text)["scene"] == "TwoStep"
        image = result.content[1]
        assert image.mime_type == "image/png"
        assert base64.b64decode(image.data).startswith(b"\x89PNG")

    def test_contact_sheet_returns_an_image(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _fake_cli(monkeypatch, stdout='{"frames": []}')
        result = _call(
            mcp_server.build_server(tmp_path), "contact_sheet", {"file": "two.py", "every": 1.0}
        )
        assert [type(block).__name__ for block in result.content][-1] == "ImageContent"

    def test_failing_child_reports_its_stderr(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _fake_cli(monkeypatch, returncode=1, stderr="Traceback\nNameError: boom\n")
        result = _call(mcp_server.build_server(tmp_path), "frame", {"file": "two.py", "at": 0.0})
        assert result.is_error is True
        assert "NameError: boom" in result.content[0].text


class TestLookup:
    def test_describe_mobject_is_served(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from manim_studio.mobject_schema import parse_schema
        from tests.test_agent_tools import _schema_payload

        monkeypatch.setattr(
            schema_store, "load_schema", lambda *a, **k: parse_schema(_schema_payload())
        )
        result = _call(mcp_server.build_server(tmp_path), "describe_mobject", {"name": "Circle"})
        assert _text_payload(result)["name"] == "Circle"


@pytest.mark.manim
@pytest.mark.gl
@pytest.mark.slow
class TestEndToEnd:
    """The agent loop against the real pipeline: write, trace, look, export."""

    def test_agent_loop_produces_blocks_frames_and_a_video(self, tmp_path: Path) -> None:
        server = mcp_server.build_server(tmp_path)
        written = _call(server, "write_scene", {"file": "two.py", "code": TWO_STEP})
        assert written.is_error is False

        traced = _text_payload(_call(server, "trace", {"file": "two.py"}))
        assert len(traced["blocks"]) == 2

        frame = _call(server, "frame", {"file": "two.py", "at": 0.5})
        assert frame.is_error is False
        meta = json.loads(frame.content[0].text)
        with Image.open(io.BytesIO(base64.b64decode(frame.content[1].data))) as image:
            assert image.size == tuple(meta["size"])

        sheet = _call(server, "contact_sheet", {"file": "two.py", "every": 0.5})
        assert sheet.content[-1].type == "image"

        exported = _text_payload(_call(server, "export", {"file": "two.py", "quality": "low"}))
        assert exported["ok"], exported["log_tail"]
        assert Path(exported["output"]).is_file()
        assert Path(exported["output"]).resolve().is_relative_to(tmp_path.resolve())
