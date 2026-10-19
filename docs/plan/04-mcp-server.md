# Chunk 4 — Qt-free schema store, then `manim-studio mcp` (stdio MCP server)

Files: create `src/manim_studio/schema_store.py`, `src/manim_studio/agent_tools.py`,
`src/manim_studio/mcp_server.py`, `tests/test_schema_store.py`, `tests/test_agent_tools.py`,
`tests/test_mcp_server.py`; edit `src/manim_studio/editor/schema_cache.py`, `cli.py`,
`frozen_entry.py`, `pyproject.toml`, `packaging/manim_studio.spec`, AGENTS.md.

## 4.0 Dependency + SDK spike (before coding)

1. `uv add "mcp>=2.3,<3"` (updates `pyproject.toml` and `uv.lock`). Pillow: already added in chunk 2.
2. Spike in the scratchpad (do not commit) to confirm the exact v2 API; adjust names below if
   they differ and note the difference in the commit body:
   ```python
   from mcp.server import MCPServer
   from mcp.server.mcpserver import Image
   from mcp.server.mcpserver.exceptions import ToolError
   from mcp import Client
   import asyncio
   s = MCPServer("t", instructions="hello")
   @s.tool()
   def one() -> list: return ["meta", Image(data=b"\x89PNG...", format="png")]
   async def go():
       async with Client(s) as c:
           r = await c.call_tool("one", {}); print(r.content, r.is_error)
   asyncio.run(go())
   ```
   Check: `instructions=` kwarg exists; a list of `[str, Image]` yields `TextContent` +
   `ImageContent`. If mixed lists are not supported, return
   `mcp.types.CallToolResult(content=[TextContent(...), ImageContent(type="image", data=<base64>,
   mime_type="image/png")])` (v2 field is snake_case `mime_type`). Also confirm the stdio client:
   `from mcp.client.stdio import stdio_client, StdioServerParameters` + `ClientSession` (needed for
   the smoke script in chunk 7).

## 4.1 `schema_store.py` (pure, stdlib only) — move, do not copy

Move from `editor/schema_cache.py` into `schema_store.py`: `CACHE_NAME`,
`installed_manimlib_version`, `read_cache`, `write_cache` (bodies unchanged). Add:

```python
def default_cache_path() -> Path:   # headless location, no Qt
    # Windows: %LOCALAPPDATA%\manim-studio\cache\mobject_schema.json
    # macOS:   ~/Library/Caches/manim-studio/mobject_schema.json
    # other:   $XDG_CACHE_HOME or ~/.cache  /manim-studio/mobject_schema.json
def load_schema(cache: Path | None = None, *, timeout: float = 120.0) -> MobjectSchema:
    """cache hit (read_cache, version match) -> parse; miss -> run the probe subprocess
    (spawn.schema_probe_command(), subprocess.run, capture, stdin DEVNULL, timeout),
    json.loads(stdout), parse_schema, write_cache, return. Raises SchemaUnavailable(str)."""
class SchemaUnavailable(RuntimeError): ...
```
`editor/schema_cache.py`: replace the moved definitions with
`from manim_studio.schema_store import CACHE_NAME, installed_manimlib_version, read_cache,
write_cache` (keep `import importlib.metadata` so `tests/test_schema_cache.py`'s monkeypatching
of `schema_cache.importlib.metadata.version` still works), keep `schema_cache_path()` (Qt) and
`SchemaLoader` unchanged, keep all names in `__all__`. Run `tests/test_schema_cache.py` — it must
pass untouched. Tests (`test_schema_store.py`): read/write round-trip; version mismatch → None;
`load_schema` uses cache without spawning (monkeypatch `subprocess.run` to raise); miss spawns,
parses a minimal valid schema document (copy the helper used in `tests/test_mobject_schema.py`),
writes the cache; probe failure → `SchemaUnavailable`; `default_cache_path` honours
`LOCALAPPDATA`/`XDG_CACHE_HOME` (monkeypatch env + `sys.platform` via a parameter if needed).
Commit: `refactor(schema): schema cache helpers usable without Qt`.

## 4.2 `agent_tools.py` — all tool logic, no MCP imports (so it is unit-testable)

Constants/helpers:
```python
class ToolFailure(RuntimeError): ...            # message goes to the agent verbatim

def resolve_in_root(root: Path, file: str | Path) -> Path
    # p = Path(file); if not absolute: root / p; resolved = p.resolve();
    # require resolved.is_relative_to(root.resolve()) else ToolFailure("path is outside the
    # workspace root <root>"); return resolved.   (absolute paths inside root are fine)
def run_cli(args: Sequence[str], *, timeout: float, cwd: Path | None = None) -> CliRun
    # exe, rest = spawn.cli_command(args); subprocess.run([exe, *rest], capture_output=True,
    # text=True, encoding="utf-8", errors="replace", stdin=DEVNULL, timeout=timeout, cwd=cwd,
    # creationflags=CREATE_NO_WINDOW on Windows). TimeoutExpired -> ToolFailure("timed out after Ns").
    # returns CliRun(returncode, stdout, stderr).
def last_json(text: str) -> dict       # parse the last complete JSON object printed in stdout
def tail(text: str, limit: int = 4000) -> str
def prune_old_images(directory: Path, keep: int = 100) -> None
```
Tool functions (each takes `root: Path` first; return JSON-serialisable dicts; image tools return
`ImageResult(meta: dict, png: bytes, path: Path)`):

| function | behaviour |
|----------|-----------|
| `check(root, file)` | in-process `check_scene_file(resolve_in_root(...)).to_dict()` + `latex_status().to_dict()` under key `latex` (same as `cmd_check --json`). No GL. |
| `trace(root, file, scene=None, resolution=None)` | `run_cli(["trace", path, "--json", ...], timeout=180)`; non-zero → `ToolFailure(tail(stderr))`; parse stdout JSON; drop `events` list if > 200 entries to keep replies small; keep `blocks`, `stats`, `total_duration`, `scene_output`, `warnings`. |
| `frame(root, file, at, scene=None, width=None, height=None, use_sidecar=True)` | `out = <tmp>/manim-studio-agent/<stem>_t{at:.2f}_<8 hex>.png`; `run_cli(["frame", path, "--at", str(at), "-o", out, "--json", ...], timeout=180)`; returns `ImageResult(meta=<json from stdout>, png=out.read_bytes(), path=out)`. |
| `contact_sheet(root, file, every=0.5, times=None, start=0.0, end=None, columns=None, max_frames=48, scene=None, use_sidecar=True)` | same pattern with `contact-sheet`, `timeout=300`. |
| `export(root, file, output=None, scene=None, quality="low", fps=None, still=False, gif=False, resolution=None, animation_range=None, use_sidecar=True)` | in-process: `build_export_plan(...)` (output resolved inside root; default quality is `low` for agents) + `run_export(plan)`; return `{"ok","returncode","output","log_tail"}`; `ExportError` → `ToolFailure`. Run in a worker thread by the server (`asyncio.to_thread`) because it blocks for a long time. |
| `list_templates()` | same payload as `cmd_templates --json`. |
| `api_index(query=None, limit=200)` | `build_index()` (AST scan, no manimlib import); filter by case-insensitive substring in `name` when `query`; return `{"total": n, "returned": m, "symbols": [...dict...]}` (the full index is thousands of names — never return it unfiltered above `limit`). |
| `describe_mobject(name, include_methods=True, include_colours=False)` | `schema = schema_store.load_schema()`; `cls = mobject_schema.lookup(schema, name)`; unknown → `ToolFailure(f"unknown mobject class {name!r}; did you mean: {difflib.get_close_matches(name, list(schema.exported), n=5)}")`. Return `{"name","key","module","lineage":[...],"manimlib_version","constructor":[{"name","annotation","has_default","default","default_repr","owner","ui"} for p in fields_for(schema, cls)],"methods":[{"name","owner","signature":"(<param names with defaults>)","doc"} for m in methods_for(schema, cls)] if include_methods else [], "colours": schema.colours if include_colours else omitted}`. `ui` = `mobject_schema.ui_kind(p)`. Methods list capped at 80 (report `methods_truncated`). Colours are hex values so agents can write `"#58C4DD"` literals. |
| `new_scene(root, file, template="circle_to_square", scene_name=None, overwrite=False)` | `render_scene_template(...)`, write inside root, refuse existing file unless `overwrite`; returns `{"path","scene_name","check": check(...)}`. |
| `write_scene(root, file, code, overwrite=True)` | require `.py` suffix; write UTF-8 inside root; cap `len(code) <= 500_000`; returns `{"path","bytes","check": check(...)}` so a write is validated immediately. |

Notes: scene paths for `trace/frame/contact_sheet/export` pass the *resolved absolute* path to the
child. Child `cwd` = the scene's folder (so relative asset paths behave like the editor). The
child inherits the environment. NEVER `shell=True`.

Tests (`test_agent_tools.py`, no GL, no MCP): `resolve_in_root` accepts relative and absolute-inside
paths, rejects `..\..\x.py` and an absolute path elsewhere (use `tmp_path`); `last_json` with
two JSON docs; `check` on the showcase (`ok` true) and on a syntax-error file; `api_index`
filtering with monkeypatched `build_index`; `describe_mobject` with a monkeypatched
`schema_store.load_schema` returning a parsed minimal schema (reuse the fixture builder from
`tests/test_mobject_schema.py`: look for its helper at the top of the file and import it or copy
the dict) — known class, unknown class with suggestions, colours flag; `new_scene`/`write_scene`
write files, refuse outside root, refuse overwrite, reject non-`.py`; `frame`/`contact_sheet` with
`agent_tools.run_cli` monkeypatched to write a tiny PNG into the requested `-o` path and print JSON
→ returns `ImageResult` with PNG bytes, builds the right argv (assert `"--at" in argv`); non-zero
exit → `ToolFailure` containing stderr text; timeout → `ToolFailure`.

## 4.3 `mcp_server.py`

```python
def build_server(root: Path) -> "MCPServer":
    from mcp.server import MCPServer
    from mcp.server.mcpserver import Image
    from mcp.server.mcpserver.exceptions import ToolError   # raise ToolError(str(failure))
    server = MCPServer("manim-studio", instructions=INSTRUCTIONS)
    ... register tools below ...
    return server

def serve(root: Path) -> int:
    build_server(root).run()      # stdio
    return 0
```
`INSTRUCTIONS` (short, ≤ 12 lines): "Manim Studio drives ManimGL (NOT Manim CE: `ShowCreation`, `from
manimlib import *`). Loop: new_scene/write_scene → check → trace (verify block count/durations)
→ contact_sheet (see the whole animation) → frame at suspicious times → fix → export. Write
colours as hex strings. All paths are relative to the workspace root. Editor open or closed makes
no difference; timeline edits in `<scene>.studio.json` are honoured (`use_sidecar=false` to
ignore)."

Tools registered, exact names and signatures (docstring first line = description; document each
argument in the docstring):
`check(file)`, `trace(file, scene=None, width=None, height=None)`,
`frame(file, at, scene=None, width=None, height=None, use_sidecar=True)`,
`contact_sheet(file, every=0.5, times=None, start=0.0, end=None, columns=None, max_frames=48,
scene=None, use_sidecar=True)`, `export(file, output=None, scene=None, quality="low", fps=None,
still=False, gif=False, width=None, height=None, animation_range=None, use_sidecar=True)` (async,
`asyncio.to_thread`), `list_templates()`, `api_index(query=None, limit=200)`,
`describe_mobject(name, include_methods=True, include_colours=False)`,
`new_scene(file, template="circle_to_square", scene_name=None, overwrite=False)`,
`write_scene(file, code, overwrite=True)`.
(`new_scene`/`write_scene` are additions to the requested list: without them an agent whose
client has no file access cannot "write a scene". They are confined to the root — ADR 0013.)

Image tools return `[json.dumps(meta), Image(path=<png path>)]` (or `Image(data=png,
format="png")`; or the `CallToolResult` fallback from the spike). The JSON text always comes
first and includes `"path"` of the saved PNG (clients without image rendering can open the file).
All tool bodies: `try: ... except ToolFailure as exc: raise ToolError(str(exc)) from exc`.

Safety: the process writes NOTHING to stdout itself (no `print`); logging goes to stderr
(`logging.basicConfig(stream=sys.stderr)` in `serve`). `tests` assert that
`mcp_server` has no `print(` via a source grep test (cheap guard).

## 4.4 CLI wiring

`cmd_mcp(args)`: `root = Path(args.root or os.environ.get("MANIM_STUDIO_ROOT") or os.getcwd())
.resolve()`; error if not a directory (return 2); `from manim_studio.mcp_server import serve`
(lazy import so `--help` stays fast and works without `mcp`); `return serve(root)`.
Parser: `mcp = subparsers.add_parser("mcp", help="run the stdio MCP server for AI agents")`;
`--root DIR`. Add `"mcp"` to `frozen_entry.CLI_SUBCOMMANDS`.

## 4.5 Packaging

In `packaging/manim_studio.spec` add `*collect_submodules("mcp")` and
`*collect_submodules("mcp_types")` (verify the distribution's real top-level names with
`uv run python -c "import mcp_types"` — the lock shows `mcp-types`) to `hiddenimports`. Note in
the PR that frozen builds are not rebuilt/tested here.

## 4.6 `tests/test_mcp_server.py` (`pytest.importorskip("mcp")`; use `asyncio.run`, not async
tests, so no extra pytest plugin is needed)

In-memory `Client(build_server(tmp_root))`: `list_tools` names equal the 10 expected (assert as a
set); `call_tool("list_templates")` JSON has `scene` and `snippet`; `write_scene` then `check`
→ `ok` true; `check` with `file="../outside.py"` → `result.is_error` and message mentions
"outside"; `describe_mobject` (monkeypatch `schema_store.load_schema`); `frame` with
`agent_tools.run_cli` monkeypatched → content contains one `TextContent` and one `ImageContent`
(`mime_type == "image/png"`, base64 decodes to the PNG bytes); a failing CLI → `is_error` with
the stderr tail. Source-grep test: no `print(` in `mcp_server.py`/`agent_tools.py`.
GL end-to-end (`@pytest.mark.gl @pytest.mark.manim @pytest.mark.slow`): real subprocess —
`write_scene` (a 2-block scene using hex colours) → `trace` (2 blocks) → `frame` at 0.5 (PNG
decodes, size matches) → `contact_sheet` (image) → `export` with `quality="low"` writes an mp4
under the root (skip if ffmpeg unavailable, mirroring `tests/test_export.py`'s slow test).

## 4.7 Done when

all new tests green (non-GL in the fast loop); `uv run manim-studio mcp --help` works;
`echo` smoke: `uv run python scripts/mcp_smoke.py` is created in chunk 7 (not here). Commits: the
schema refactor first, then `feat(mcp): stdio MCP server for agents`.
