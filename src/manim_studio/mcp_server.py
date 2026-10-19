"""Stdio MCP server that exposes :mod:`manim_studio.agent_tools` to AI agents (ADR 0013).

This module only dispatches. Scene work runs in child processes, so the server never imports
manimlib or moderngl. Stdout is the protocol wire: nothing here may write to it, and logs go to
stderr. The MCP SDK is imported lazily so ``manim-studio --help`` stays fast without it.
"""

from __future__ import annotations

import asyncio
import json
import logging
import sys
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

from manim_studio import agent_tools
from manim_studio.agent_tools import ImageResult, ToolFailure

if TYPE_CHECKING:
    from mcp.server import MCPServer

__all__ = ["INSTRUCTIONS", "build_server", "serve"]

log = logging.getLogger(__name__)

INSTRUCTIONS = """\
Manim Studio drives ManimGL, not Manim Community: use ShowCreation and `from manimlib import *`.
Loop: new_scene or write_scene, then check, then trace (verify blocks and durations), then
contact_sheet (see the whole animation), then frame (inspect a suspicious moment), then fix, then
export. Write colours as hex strings such as "#58C4DD"; never use manim colour constant names.
All paths are relative to the workspace root. The editor being open or closed makes no difference.
Timeline edits saved in <scene>.studio.json are honoured; pass use_sidecar=false to ignore them.
"""


def build_server(root: Path) -> MCPServer:
    """A server whose tools are confined to ``root``."""
    from mcp.server import MCPServer
    from mcp.server.mcpserver import Image
    from mcp.server.mcpserver.exceptions import ToolError

    server = MCPServer("manim-studio", instructions=INSTRUCTIONS)

    def run[T](fn: Callable[..., T], *args: Any, **kwargs: Any) -> T:
        try:
            return fn(*args, **kwargs)
        except ToolFailure as exc:
            raise ToolError(str(exc)) from exc

    def image_reply(result: ImageResult) -> list[Any]:
        # The metadata (including the saved path) comes first, so a client without image
        # rendering can still open the file.
        return [json.dumps(result.meta, indent=2), Image(data=result.png, format="png")]

    @server.tool()
    def check(file: str) -> dict[str, Any]:
        """Syntax and scene discovery for a scene file, plus the LaTeX status. Renders nothing.

        file: path of the .py scene, relative to the workspace root.
        """
        return run(agent_tools.check, root, file)

    @server.tool()
    def trace(
        file: str,
        scene: str | None = None,
        width: int | None = None,
        height: int | None = None,
    ) -> dict[str, Any]:
        """Blocks (play and wait calls) with their start times and durations, from a headless run.

        file: scene path. scene: class name when the file has several. width and height: frame
        size, given together.
        """
        resolution = (width, height) if width and height else None
        return run(agent_tools.trace, root, file, scene=scene, resolution=resolution)

    @server.tool()
    def frame(
        file: str,
        at: float,
        scene: str | None = None,
        width: int | None = None,
        height: int | None = None,
        use_sidecar: bool = True,
    ) -> list:
        """One frame of the scene at a time, as an image plus its metadata.

        file: scene path. at: seconds into the scene; past the end shows the final state.
        scene: class name when the file has several. width and height: frame size, given together.
        use_sidecar: honour timeline edits in <scene>.studio.json (default true).
        """
        result: ImageResult = run(
            agent_tools.frame,
            root,
            file,
            at,
            scene=scene,
            width=width,
            height=height,
            use_sidecar=use_sidecar,
        )
        return image_reply(result)

    @server.tool()
    def contact_sheet(
        file: str,
        every: float = 0.5,
        times: list[float] | None = None,
        start: float = 0.0,
        end: float | None = None,
        columns: int | None = None,
        max_frames: int = 48,
        scene: str | None = None,
        use_sidecar: bool = True,
    ) -> list:
        """One image tiling the scene's frames, each labelled with its time and block name.

        file: scene path. every: seconds between samples (default 0.5). times: explicit seconds,
        instead of every. start and end: restrict the range. columns: grid width (default is
        near-square). max_frames: cap on frames; longer runs are thinned evenly.
        """
        result: ImageResult = run(
            agent_tools.contact_sheet,
            root,
            file,
            every=every,
            times=times,
            start=start,
            end=end,
            columns=columns,
            max_frames=max_frames,
            scene=scene,
            use_sidecar=use_sidecar,
        )
        return image_reply(result)

    @server.tool()
    async def export(
        file: str,
        output: str | None = None,
        scene: str | None = None,
        quality: str = "low",
        fps: int | None = None,
        still: bool = False,
        gif: bool = False,
        width: int | None = None,
        height: int | None = None,
        animation_range: tuple[int, int] | None = None,
        use_sidecar: bool = True,
    ) -> dict[str, Any]:
        """Render the scene to a video (or a still or GIF). Slow: it can take minutes.

        file: scene path. output: destination inside the workspace (default: next to the scene).
        quality: low, medium or high (default low). still and gif: alternative outputs.
        animation_range: first and last animation index to render. use_sidecar: as for frame.
        """
        resolution = (width, height) if width and height else None
        return await asyncio.to_thread(
            run,
            agent_tools.export,
            root,
            file,
            output=output,
            scene=scene,
            quality=quality,
            fps=fps,
            still=still,
            gif=gif,
            resolution=resolution,
            animation_range=animation_range,
            use_sidecar=use_sidecar,
        )

    @server.tool()
    def list_templates() -> dict[str, Any]:
        """The scene templates (for new_scene) and the snippet templates."""
        return agent_tools.list_templates()

    @server.tool()
    def api_index(query: str | None = None, limit: int = 200) -> dict[str, Any]:
        """Public manimlib names. query: case-insensitive substring. limit: most names returned."""
        return run(agent_tools.api_index, query=query, limit=limit)

    @server.tool()
    def describe_mobject(
        name: str,
        include_methods: bool = True,
        include_colours: bool = False,
    ) -> dict[str, Any]:
        """Constructor parameters (with defaults and editor widget kinds) of a manimlib class.

        name: class name such as Circle. include_methods: list its chainable methods.
        include_colours: list the colour constants with their hex values.
        """
        return run(
            agent_tools.describe_mobject,
            name,
            include_methods=include_methods,
            include_colours=include_colours,
        )

    @server.tool()
    def new_scene(
        file: str,
        template: str = "circle_to_square",
        scene_name: str | None = None,
        overwrite: bool = False,
    ) -> dict[str, Any]:
        """Create a scene file from a template, then check it.

        file: new .py path inside the workspace. template: a key from list_templates.
        scene_name: class name (default derived from the file name). overwrite: replace an
        existing file.
        """
        return run(
            agent_tools.new_scene,
            root,
            file,
            template=template,
            scene_name=scene_name,
            overwrite=overwrite,
        )

    @server.tool()
    def write_scene(file: str, code: str, overwrite: bool = True) -> dict[str, Any]:
        """Write Python source for a scene, then check it so mistakes come back at once.

        file: .py path inside the workspace. code: the complete file contents.
        overwrite: replace an existing file (default true).
        """
        return run(agent_tools.write_scene, root, file, code, overwrite=overwrite)

    return server


def serve(root: Path) -> int:
    """Serve the tools over stdio until the client disconnects."""
    logging.basicConfig(stream=sys.stderr, level=logging.INFO)
    build_server(root).run()
    return 0
