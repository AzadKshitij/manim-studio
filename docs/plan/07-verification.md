# Chunk 7 — verification and risks

## 7.1 Automated

1. `uv sync --all-groups`
2. `uv run ruff format src tests; uv run ruff check src tests; uv run mypy`
3. `uv run pytest -m "not gl and not slow"` (fast loop)
4. `uv run pytest -m gl` and `uv run pytest -m slow` on a machine with a GPU/ffmpeg.
5. Create `scripts/mcp_smoke.py` (committed with chunk 6 or its own `chore:` commit): uses the
   SDK's stdio client (`StdioServerParameters(command=sys.executable, args=["-m","manim_studio",
   "mcp","--root", tmp])` + `stdio_client` + `ClientSession`) to run the full agent loop:
   `write_scene` (broken colour/typo) → `check` shows error → `write_scene` fixed → `trace` →
   `contact_sheet` (assert an image block) → `frame` → `export` low quality → assert the mp4
   exists. Prints OK/FAIL, exit code accordingly. Run: `uv run python scripts/mcp_smoke.py`.

## 7.2 Manual (acceptance = the task's "Done")

* Editor CLOSED: register the server in Claude Code
  (`claude mcp add manim-studio -- uv run --directory <repo> manim-studio mcp`), ask the agent:
  "make a scene where a circle becomes a square, look at it, and export it". Confirm it calls
  `write_scene/new_scene → check → contact_sheet → frame → export` and that the contact-sheet
  image is visible to the agent.
* Editor OPEN on the same file (`uv run manim-studio gui <file>`): repeat; confirm the agent's
  `write_scene` edits auto-reload in the editor and the agent's frames match the preview; add a
  timeline duration edit in the editor, apply nothing to source, and confirm `frame`/`export`
  honour the sidecar (total_duration changes) and `use_sidecar=false` ignores it.
* `uv run manim-studio agent-init <tmpdir>` then open `<tmpdir>` in Claude Code and in Cursor and
  confirm the skill/rule/MCP config are picked up.
* `uv run manim-studio frame <file> --at 99` clamps and reports `"clamped": true`.

## 7.3 Risks and what to do

| Risk | Mitigation |
|------|-----------|
| Headless pose + readback does not work (no GL on a runner) | Spike in chunk 2; GL tests skip; report to owner if impossible everywhere. |
| MCP v2 API names differ from Context7 notes | Spike 4.0 first; adjust plan note; do not guess later. |
| N replays slow for long scenes | cap 48 frames; ADR notes `stream_frames` follow-up. |
| Scene prints to stdout corrupting the MCP wire | GL work is in child processes with captured stdout; server never prints; guard test. |
| Agent reads/writes outside the project | `resolve_in_root`, `.py` only, size cap; documented. |
| `pywin32`/starlette bloat in frozen builds | hiddenimports note; not rebuilt here; flagged in PR. |
| PR stacks on `ux/editor-improvements` | say so in the PR description; retarget to `main` once that branch merges. |

## 7.4 Out of scope (do not do)

In-app AI of any kind; driving the running editor over IPC; HTTP/SSE MCP transports; changing the
preview-host protocol; a version bump; rebuilding the frozen installer.
