# ADR 0013 — Agent surface: frames at any time, contact sheets and an MCP server

- Status: accepted (2026-10-19).
- Context: an AI agent could write a scene but could not check its result. `still` renders
  only the final frame, nothing renders an arbitrary time, and there was no MCP server, so
  an agent was blind between writing code and exporting a video. The product direction is
  "bring the agent you already use": Claude Code, Cursor, Codex, opencode. Manim Studio
  should therefore expose a tool surface, not carry its own model.

## Decision

1. **No AI inside the product.** There is no LLM client, no API key, no prompt sent
   anywhere. Manim Studio ships tools, a server and instructions. This is an explicit
   non-goal; adding an in-app assistant needs a new ADR.
2. **Frames come from `ScrubEngine`** (ADR 0002), the posing the preview host already uses.
   A fresh headless scene is replayed to time `t`, and the frame is read back with
   `read_upright_rgba`. The sidecar's timeline patches and the scene settings are applied the
   same way export applies them (ADR 0007, ADR 0010). Each pose replays from the start, so
   cost grows with the time reached; contact sheets are capped at 48 frames. A single-pass
   stream (`stream_frames`) is the optimisation if this becomes slow. It is not needed yet.
3. **Contact-sheet geometry is pure.** `contact_sheet.py` chooses times, finds the block at
   each time, formats labels and lays out the grid, all unit-tested without pixels. Pillow
   composes the image in `frames.py`.
4. **The MCP server dispatches; it does not render.** Scene work runs in short-lived child
   processes (`manim-studio trace|frame|contact-sheet --json`). The long-lived server never
   imports manimlib or moderngl (AGENTS rule 3). A crashing scene cannot kill the server,
   manimlib's argv parsing and module caching cannot leak between calls, and every call can
   be killed by a timeout. The cost is a few seconds of manimlib import per call.
5. **It works with the editor open or closed.** The tools read the same files and sidecar the
   editor uses. They never talk to the editor. Driving the live preview host over IPC was
   rejected: that host belongs to the editor process, it would need a protocol bump
   (`PROTOCOL_VERSION`), and it fails when the editor is closed.
6. **Workspace root sandbox.** Every path argument must resolve inside `--root` (default: the
   `MANIM_STUDIO_ROOT` variable, else the working directory). `write_scene` and `new_scene`
   write `.py` files only and cap source size. Export output defaults to a path next to the
   scene; `build_export_plan` would otherwise use the working directory, which need not be
   inside the root.
7. **SDK pin: `mcp>=2.3,<3`.** The v2 `MCPServer` API is used. The v1 `FastMCP` import path is
   gone. The import is lazy, so `manim-studio --help` does not need the SDK.
8. **Two authoring tools were added to the requested set**, `new_scene` and `write_scene`.
   Without them, a client with no file access could not complete the loop. Both stay inside
   the root (decision 6).
9. **`agent-init` writes a managed block, not a takeover.** It writes an AGENTS.md block
   between markers, a Claude Code skill, a Cursor rule and MCP config (`.mcp.json` and
   `.cursor/mcp.json`). Its own files are replaced only with `--force`. MCP config keeps other
   servers, and an invalid file is skipped, never overwritten. Codex and opencode are not
   written: their configuration lives outside the project, and the command prints the generic
   registration instead.

## Consequences

- New dependencies: `mcp` (which brings starlette, uvicorn, pydantic and, on Windows,
  pywin32) and `pillow` declared directly. Both are already in the lock file.
- The frozen build needs the MCP hidden imports (`packaging/manim_studio.spec`). The installer
  was not rebuilt for this change.
- Tool replies stay small: trace events are capped at 200, and the API index is filtered and
  capped. Images return inline and are also kept in a temp folder, the newest 100 retained.
- Scenes are assumed to be deterministic. Frames replayed from the start should match a full
  run, and a GL test checks that they do.
- The MCP server has no authentication. It is a stdio server started by the user's own agent,
  and it is confined to its root. It is not a network service.
- `render_still` returns a fully transparent PNG in a headless run, because it reads the
  frame without drawing it first. This predates the agent surface and is not changed here.
  The agent tools draw before reading.
