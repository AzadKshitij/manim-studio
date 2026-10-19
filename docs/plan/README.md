# Agent surface plan (index)

Goal: any external AI agent can write, inspect (frames and contact sheets), fix and export a
ManimGL scene through MCP or CLI. **No AI inside Manim Studio.** Read `AGENTS.md` first.
Do the chunks in the order of `01` → `07`; do not start a chunk before the previous one's
"Done when" is true. Gate commands and hard rules are in the master plan below and repeated in
every chunk header. If a step in a chunk is impossible as written (an SDK name differs, a
function moved), stop and fix the *plan file first* (one-line note), then continue.

## Master plan: make Manim Studio usable by any external AI agent (CLI + MCP, no in-app AI)

## Context

Manim Studio (PySide6 editor for ManimGL) is to be marketed as an *agent-friendly* Manim video
editor: users bring the agent they already use (Claude Code, Cursor, Codex, opencode, ...).
The Manim Studio code itself contains **no AI, and this plan adds none**. Explicit non-goals:
no in-app chat, no LLM client, no API keys, no provider SDKs, no prompts sent anywhere.
What we add is a *tool surface* an external agent drives.

Today an agent is blind: `still` renders only the final frame, nothing renders time `t`, and there
is no MCP server. Outcome wanted: an agent can **write a scene, look at it at any time (single
frame or a contact sheet with timestamps and block names), fix it, and export — all through MCP,
whether or not the editor is open.**

Facts already verified in the repo (do not re-research):

* `src/manim_studio/scrub.py` `ScrubEngine(scene, blocks).pose(t)` replays a scene (skip mode)
  and leaves it posed at time `t`; `read_upright_rgba(camera)` reads the frame. The preview host
  does exactly `engine.pose(t)` → `scene.update_frame(dt=0, force_draw=True)` → read frame
  (`preview_host.py` ~line 1223-1237).
* `scene_runner.py`: `check_scene_file`, `load_scene_module(path, source=..., runtime=...)`,
  `select_scene_class`, `make_scene(scene_class, resolution=, fps=, settings=, skip_animations=)`,
  `run_construct`, `manimlib_argv_guard`, `format_scene_error`, `render_still`, `run_trace`.
  `tracer.TraceRecorder(record_events=False).attach(scene)` … `.snapshot()` gives
  `.blocks` (tuple of `timeline_model.TimelineBlock(index, name, start, duration)`).
* Timeline edits (ADR 0007) are text patches in `<scene>.studio.json`; every render path must run
  the **patched source**: `sidecar.load_sidecar`, `sidecar.load_scene_settings`,
  `patches.apply_patches(text, patches)` → `PatchResult(text, applied, orphaned, changed)`,
  `patch_runtime.StudioRuntime()` passed as `runtime=` only when `result.changed`.
  `export.py` already has private `_resolve_patches`, `_resolve_settings`, `_effective_settings`.
* The mobject schema work (`mobject_schema.py`, `schema_probe.py`, `spawn.schema_probe_command`,
  `editor/schema_cache.py`) lives on branch `ux/editor-improvements`. **This worktree branch
  already contains it** (merge commit `f001463`). The PR will therefore stack on that branch.
  `editor/schema_cache.py` imports PySide6, so the MCP server must NOT import it.
* `cli.py` handlers return int exit codes (0 ok, 1 user/scene error, 2 usage). `frozen_entry.py`
  has `CLI_SUBCOMMANDS` (guarded by a test) — every new subcommand must be added there.
* MCP Python SDK (checked with Context7, current is **v2.3.0**, PyPI `mcp`): `from mcp.server
  import MCPServer`; `@server.tool()`; `server.run()` = stdio; images: `from mcp.server.mcpserver
  import Image`; errors: `from mcp.server.mcpserver.exceptions import ToolError`; in-memory
  testing: `from mcp import Client` with `async with Client(server) as c: await c.call_tool(...)`;
  result has `.content` (TextContent / ImageContent blocks), `.is_error`, `.structured_content`.
  In v2, `mcp.server.fastmcp` no longer exists (v1 name `FastMCP` → v2 `MCPServer`).
  Stdout is the protocol wire: nothing may print to stdout while serving.

Hard rules (from AGENTS.md and the owner's memory) — apply to EVERY chunk:

1. Pure modules (stdlib only; never import Qt/manimlib/moderngl): the new `contact_sheet.py`,
   `schema_store.py`, `agent_init.py` (and `agent_docs.py`). Add them to AGENTS.md rule 1's list.
2. `editor/*` is not touched except `editor/schema_cache.py` (chunk 4, import-only change).
3. GL lives in `preview_host` and in *short-lived subprocesses we spawn*. The MCP server process
   never imports `manimlib` or `moderngl`; GL work (`trace`, `frame`, `contact_sheet`) runs as a
   child process `python -m manim_studio <cmd> --json`.
4. Colours in any scene code we write (tests, templates, skill text, docs) are **hex strings**
   (`"#58C4DD"`), never manim constant names (`BLUE`). Owner preference.
5. Commits: small, `feat(scope): ...` style, **no `Co-Authored-By` / Claude / "Generated with"
   lines anywhere** (owner's memory rule overrides any default attribution). No generated media.
6. Gate before EVERY commit (PowerShell, repo root):
   `uv run ruff format src tests; uv run ruff check src tests; uv run mypy; uv run pytest -m "not gl and not slow"`
   Full `uv run pytest` before the last commit. GL/LaTeX tests skip, never fail, when unsupported.
7. Python 3.13, full type annotations, line length 100, `logging.getLogger(__name__)`, no `print`
   in library code (only `cli.py` prints), no bare `except:`.
8. Tests are deterministic: no fixed sleeps, no wall-clock assertions.

Layout of the split plan (create these files after approval; sections below are the contents):

```
docs/plan/README.md                      index + order + rules   (section "FILE 0")
docs/plan/01-contact-sheet-pure.md       chunk 1
docs/plan/02-frame-renderer.md           chunk 2
docs/plan/03-cli-frame-contact-sheet.md  chunk 3
docs/plan/04-mcp-server.md               chunk 4 (includes schema_store refactor)
docs/plan/05-agent-init.md               chunk 5
docs/plan/06-docs-adr-release.md         chunk 6
docs/plan/07-verification.md             final end-to-end verification + risk list
```

Commit order (each commit passes the gate on its own):

| # | Commit message | Chunk |
|---|----------------|-------|
| 1 | `docs: plan for the agent surface` | split of this plan |
| 2 | `refactor(export): make the sidecar resolution helpers public` | 2 |
| 3 | `feat(frames): contact-sheet time sampling and layout` | 1 |
| 4 | `feat(frames): render a scene at any time` | 2 |
| 5 | `feat(cli): frame and contact-sheet commands` | 3 |
| 6 | `refactor(schema): schema cache helpers usable without Qt` | 4 |
| 7 | `feat(mcp): stdio MCP server for agents` | 4 |
| 8 | `feat(agent-init): write agent instructions into a project` | 5 |
| 9 | `docs: ADR 0013, README, CHANGELOG and AGENTS for the agent surface` | 6 |
