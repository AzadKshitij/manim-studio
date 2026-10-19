# Chunk 5 — `manim-studio agent-init`: install the agent instructions into a project

Files: create `src/manim_studio/agent_docs.py` (text constants), `src/manim_studio/agent_init.py`
(pure planning + merge logic), `tests/test_agent_init.py`; edit `cli.py`, `frozen_entry.py`
(`"agent-init"`), `.opencode/skills/manim-scene-authoring/SKILL.md`, AGENTS.md map.
Both new modules are pure (stdlib only) — list them in AGENTS.md rule 1.

## 5.1 Content (`agent_docs.py`)

`SKILL_NAME = "manim-scene-authoring"`, `SKILL_DESCRIPTION` (one sentence: when to use it) and
`SKILL_BODY` (markdown, no front matter). Rewrite the existing `.opencode` skill into an
agent-first document with these sections, in this order:
1. **The loop** (MCP tools first, CLI equivalents in a 2-column table): `new_scene`/`write_scene`
   → `check` → `trace` → `contact_sheet` → `frame` → fix → `export`. Explain how to read a contact
   sheet (each tile = `t=<seconds> #<block index> <block name>`; look for empty tiles, overlapping
   objects, things that never move, objects leaving the frame).
2. **ManimGL, not Manim CE**: naming list kept from the old skill (`ShowCreation`, `from
   manimlib import *`, `RegularPolygon(n=5)` instead of `Star`, camera `self.frame`).
3. **Colours: always hex strings** (`"#58C4DD"`), never constants like `BLUE`; `Circle(stroke_color=
   "#58C4DD")` (the `Circle(color=...)` sharp edge from the old skill, rewritten with hex).
   Get exact constant values from `describe_mobject(..., include_colours=True)`.
4. **Structure that traces well** (the old example rewritten with hex colours, `run_time=` on
   every `play`).
5. **Studio specifics**: sidecar `<scene>.studio.json` timeline edits are honoured by `frame`,
   `contact_sheet` and `export` (`use_sidecar=false` to ignore); never hand-edit it; with the
   editor open, saving the `.py` auto-reloads; `self.embed()` is a no-op.
6. **Debugging**: errors come back with the scene line; `check` then `trace`; LaTeX hint
   (`manim-studio latex status`).
Keep it under ~150 lines. Examples contain no `BLUE`-style constants anywhere (test enforces).

Also `MCP_SERVER_NAME = "manim-studio"`, `AGENTS_BEGIN = "<!-- manim-studio:begin -->"`,
`AGENTS_END = "<!-- manim-studio:end -->"`, and `agents_block()` = begin marker + a SHORT section
(15-25 lines: what Manim Studio is, the loop, pointer to the skill file) + end marker.

## 5.2 `agent_init.py` API

```python
@dataclass(frozen=True, slots=True)
class PlannedFile:
    relpath: str            # posix style, relative to the project dir
    content: str            # full new content
    action: str             # "create" | "update" | "unchanged" | "skip"
    reason: str = ""

TARGETS = ("claude", "cursor", "agents", "mcp")

def plan_init(project: Path, targets: Sequence[str] = TARGETS, *, command: str = "manim-studio",
              force: bool = False) -> tuple[PlannedFile, ...]
def apply_plan(project: Path, plan: Sequence[PlannedFile]) -> tuple[PlannedFile, ...]  # writes create/update
def merge_agents_md(existing: str | None) -> str
def merge_mcp_json(existing: str | None, command: str) -> str   # raises ValueError on invalid JSON
def cursor_rule(command: str) -> str
def render_skill() -> str      # front matter (name, description) + SKILL_BODY
def manual_instructions(command: str) -> str  # text for clients we do not write config for
```
Targets and files (all relative to `project`):
* `claude` → `.claude/skills/manim-scene-authoring/SKILL.md` = `render_skill()`.
* `cursor` → `.cursor/rules/manim-studio.mdc` = front matter (`description:`, `globs: **/*.py`,
  `alwaysApply: false`) + the same body; and the MCP entry below also for `.cursor/mcp.json`.
* `agents` → `AGENTS.md`: `merge_agents_md`: file absent → just the block; present with both
  markers → replace everything between and including markers with the new block; present without
  markers → existing text + blank line + block. Never touches text outside the markers.
* `mcp` → `.mcp.json` (Claude Code project scope) and `.cursor/mcp.json`: JSON
  `{"mcpServers": {"manim-studio": {"command": <command>, "args": ["mcp"]}}}` merged into an
  existing file (preserve other servers; replace only the `manim-studio` key); invalid existing
  JSON → action `skip` with the reason, never overwrite.
* Files that are wholly ours (skill, cursor rule): absent → `create`; identical → `unchanged`;
  different → `skip` with reason "modified; use --force" unless `force` → `update`.
* Codex and opencode configs are NOT written (their config files are user-global or have formats
  we should not guess); `manual_instructions` returns the exact command to print for them:
  `codex mcp add manim-studio -- manim-studio mcp` and the opencode `mcp` block. **Before
  writing these two strings, verify them with Context7 / the clients' docs; if unverifiable,
  print only "register an stdio MCP server with command `manim-studio mcp`".**
* `plan_init` with an unknown target → `ValueError`.

## 5.3 CLI

```
manim-studio agent-init [DIR] [--target claude,cursor,agents,mcp | all] [--command CMD]
                        [--force] [--dry-run] [--json]
```
`DIR` defaults to `.`. `--target` accepts a comma list or `all` (default). `--command` default
`manim-studio` (users running from a checkout can pass `uv run --directory <repo> manim-studio`
— the string is split into `command` + leading `args` with `shlex.split`, then `mcp` appended).
Output: one line per file `created|updated|unchanged|skipped  <relpath>`; with `--json`
`{"project": ..., "files": [{"path","action","reason"}], "manual": "<text>"}`. `--dry-run` writes
nothing. Unknown target → exit 2. Exit 0 even when some files are `skipped` (reported, not an
error).

## 5.4 Tests (`tests/test_agent_init.py`, all pure, use `tmp_path`)

* `render_skill` starts with `---`, contains `name: manim-scene-authoring`; body contains no
  `\b(BLUE|RED|GREEN|YELLOW|WHITE|ORANGE|PURPLE)\b` colour constants outside an explicit
  "never use" sentence (assert on a regex over fenced code blocks only).
* `.opencode/skills/manim-scene-authoring/SKILL.md` body equals `SKILL_BODY` (drift guard).
* `merge_agents_md`: None → block only; no markers → original text preserved verbatim followed by
  block; markers → replaced, text before/after untouched, idempotent (merge twice == merge once).
* `merge_mcp_json`: None → single server; other servers preserved; replaces stale
  `manim-studio`; invalid JSON raises.
* `plan_init` + `apply_plan` in `tmp_path`: all four targets create the expected files; second run
  → all `unchanged`; edit the skill file → `skip`; with `force=True` → `update`; `--dry-run` path
  (plan only) writes nothing; invalid existing `.mcp.json` → skip and file untouched.
* CLI: `main(["agent-init", str(tmp_path), "--json"])` → 0 and JSON lists the files;
  `--target nope` → 2.

## 5.5 Done when

tests green; `uv run manim-studio agent-init $env:TEMP\proj --dry-run` prints the plan;
commit `feat(agent-init): write agent instructions into a project`.
