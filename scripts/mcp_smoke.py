"""End-to-end smoke test of the agent surface over real stdio (ADR 0013).

Starts ``python -m manim_studio mcp`` as a child process, the way an agent client does, and runs
the loop. Two layers of validation are checked on the way:

* ``check`` is static: it rejects a syntax error but accepts a misspelt class name;
* ``trace`` runs the scene: it rejects the misspelt name.

Then the fixed scene is traced, sampled as a contact sheet, framed and exported. Prints one line
per step and exits 0 only if every step holds.

Run from the repository root:  uv run python scripts/mcp_smoke.py
"""

from __future__ import annotations

import asyncio
import base64
import io
import json
import sys
import tempfile
from pathlib import Path
from typing import Any

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from PIL import Image

FILE = "smoke.py"


def _scene(constructor: str, header_colon: str = ":") -> str:
    return (
        "from manimlib import *\n\n\n"
        f"class Smoke(Scene){header_colon}\n"
        "    def construct(self):\n"
        f'        shape = {constructor}(color="#58C4DD")\n'
        "        self.play(FadeIn(shape), run_time=0.5)\n"
        "        self.wait(0.5)\n"
    )


SYNTAX_ERROR = _scene("Square", header_colon="")  # no colon after the class header
MISSPELT = _scene("Squar")  # parses, but NameError when construct runs
FIXED = _scene("Square")


class SmokeFailure(RuntimeError):
    """A step did not hold; the message says which."""


def _expect(condition: bool, message: str) -> None:
    if not condition:
        raise SmokeFailure(message)


def _payload(result: Any) -> dict[str, Any]:
    if result.is_error:
        raise SmokeFailure(f"tool call failed: {result.content[0].text}")
    if result.structured_content is not None:
        return result.structured_content
    return json.loads(result.content[0].text)


def _image_blocks(result: Any) -> list[Any]:
    return [block for block in result.content if getattr(block, "type", "") == "image"]


async def _loop(root: Path) -> None:
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "manim_studio", "mcp", "--root", str(root)],
    )
    async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
        await session.initialize()

        broken = _payload(
            await session.call_tool("write_scene", {"file": FILE, "code": SYNTAX_ERROR})
        )
        _expect(broken["check"]["ok"] is False, "check accepted a syntax error")
        print("ok    check rejects a syntax error")

        misspelt = _payload(
            await session.call_tool("write_scene", {"file": FILE, "code": MISSPELT})
        )
        _expect(misspelt["check"]["ok"] is True, "check rejected a name it cannot judge")
        print("ok    check passes a misspelt name (it is static)")

        run = await session.call_tool("trace", {"file": FILE})
        _expect(run.is_error is True, "trace accepted a misspelt class name")
        _expect("Squar" in run.content[0].text, "trace did not name the misspelt class")
        print("ok    trace rejects the misspelt name")

        fixed = _payload(await session.call_tool("write_scene", {"file": FILE, "code": FIXED}))
        _expect(fixed["check"]["ok"] is True, f"fixed scene fails check: {fixed['check']}")

        traced = _payload(await session.call_tool("trace", {"file": FILE}))
        _expect(len(traced["blocks"]) == 2, f"expected 2 blocks, got {len(traced['blocks'])}")
        print("ok    fixed scene traces to two blocks")

        sheet = await session.call_tool("contact_sheet", {"file": FILE, "every": 0.25})
        _expect(not sheet.is_error, "contact_sheet failed")
        images = _image_blocks(sheet)
        _expect(len(images) == 1, "contact_sheet returned no image")
        with Image.open(io.BytesIO(base64.b64decode(images[0].data))) as image:
            _expect(image.format == "PNG", "contact sheet is not a PNG")
        print("ok    contact sheet is an image")

        frame = await session.call_tool("frame", {"file": FILE, "at": 0.5})
        _expect(not frame.is_error, "frame failed")
        _expect(len(_image_blocks(frame)) == 1, "frame returned no image")
        print("ok    frame at 0.5s is an image")

        exported = _payload(await session.call_tool("export", {"file": FILE, "quality": "low"}))
        _expect(exported["ok"], f"export failed: {exported['log_tail']}")
        output = Path(exported["output"])
        _expect(output.is_file() and output.stat().st_size > 0, "export wrote no video")
        _expect(output.resolve().is_relative_to(root.resolve()), "export left the root")
        print(f"ok    export wrote {output.name} inside the root")


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="manim-studio-smoke-") as tmp:
        try:
            asyncio.run(_loop(Path(tmp)))
        except SmokeFailure as exc:
            print(f"FAIL  {exc}")
            return 1
    print("OK    agent loop over stdio holds")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
