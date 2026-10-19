# Chunk 6 — documentation

Files: create `docs/adr/0013-agent-surface.md`; edit `README.md`, `CHANGELOG.md`, `AGENTS.md`,
`docs/protocol.md` is NOT touched (no IPC change).

## 6.1 ADR 0013 (mirror the layout of ADR 0012: Status/Context/Decision/Consequences)

Title: "ADR 0013 — Agent surface: frames at any time, contact sheets and an MCP server".
Status: accepted (use today's date). Context: agents were blind (final frame only, no MCP); the
product direction is "bring your own agent". Decisions (numbered, each 2-4 sentences):
1. **No AI inside the product** (explicit non-goal): no LLM client, no keys, no prompts; we ship
   tools and instructions only.
2. **Frames come from `ScrubEngine`**, the same posing the preview host uses (ADR 0002): a fresh
   headless scene is replayed to time `t`; the sidecar's patched source and scene settings are
   applied exactly as export does (ADR 0006/0007). Cost: one replay per frame (cap 48); future
   optimisation = `stream_frames` single pass.
3. **Contact-sheet geometry is pure** (`contact_sheet.py`, unit-tested); pixels are composed in
   `frames.py`.
4. **MCP server is a dispatcher, not a renderer**: GL work runs in short-lived child processes
   (`manim-studio trace|frame|contact-sheet --json`) so the long-lived server never imports
   manimlib/moderngl (AGENTS rule 3), a crashing scene cannot kill the server, manimlib's argv
   parsing and module caching cannot leak between calls, and calls are killable by timeout.
   Cost: ~2-4 s manimlib import per call.
5. **Works with the editor open or closed** because it never talks to the editor: it reads the
   same files and sidecar. Rejected: driving the live preview host over IPC (the host belongs to
   the editor process; would need a protocol bump and fails when the editor is closed).
6. **Workspace root sandbox**: every path argument must resolve inside `--root` (default cwd);
   `write_scene` only writes `.py`; images are saved to a temp dir and also returned inline.
7. **SDK pin**: `mcp>=2.3,<3` (v2 `MCPServer`; v1's `FastMCP` import path is gone). Lazy import.
8. **`new_scene` and `write_scene`** extend the requested tool list so clients with no file access
   can complete the loop.
9. **`agent-init`** writes a managed block into AGENTS.md (markers), a Claude Code skill, a Cursor
   rule and MCP config; never overwrites user-edited files without `--force`.
Consequences: new dependency (`mcp`, brings starlette/uvicorn/pywin32 on Windows), frozen build
needs the hidden imports from chunk 4, tool replies are small (events/API index capped).

## 6.2 README

* CLI block: add `frame`, `contact-sheet`, `mcp`, `agent-init` lines in the existing style.
* New section "## Use it with your AI agent" (after the CLI section): 3 steps — (1) `uv tool
  install`/`uv run` note, (2) `manim-studio agent-init .` (writes skill, rules, `.mcp.json`),
  (3) the MCP config snippet for Claude Code (`claude mcp add manim-studio -- manim-studio mcp`),
  Cursor (`.cursor/mcp.json`), Codex and opencode (only if verified in chunk 5, else the generic
  line). A tool table (10 tools, one line each). A 6-line example agent session. One sentence:
  "Manim Studio has no built-in AI; bring the agent you already use."

## 6.3 CHANGELOG

Under `## [Unreleased]` → `### Added` append bullets: `frame` and `contact-sheet` commands
(render at any time; contact sheet with timestamps and block names); MCP server (`manim-studio
mcp`, 10 tools, images returned inline, workspace-root sandbox); `agent-init`. Under
`### Changed`: `export` sidecar helpers public (internal; may be omitted).

## 6.4 AGENTS.md

Architecture map rows: `contact_sheet.py` (pure), `frames.py`, `schema_store.py` (pure),
`agent_tools.py`, `mcp_server.py` (imports `mcp` lazily; never manimlib), `agent_docs.py` +
`agent_init.py` (pure). Rule 1 pure-module list gains `contact_sheet`, `schema_store`,
`agent_docs`, `agent_init`. §8 "AI-agent workflows": add `frame`, `contact-sheet`, `mcp`,
`agent-init` examples and one sentence: "no AI runs inside the app".

## 6.5 Done when

docs render, links valid, commit `docs: ADR 0013, README, CHANGELOG and AGENTS for the agent
surface`.
