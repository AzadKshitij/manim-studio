"""Plan and write the agent instructions for one project (ADR 0013).

Pure logic: it reads and writes project files but imports neither Qt nor manimlib. Each target
produces :class:`PlannedFile` entries first, so ``--dry-run`` and tests see the same plan that a
real run applies.

Files this tool wholly owns (the skill and the Cursor rule) are created, left alone when they are
already identical, and skipped when someone has edited them, unless ``force`` is set. Files other
people also write are merged: ``AGENTS.md`` only between its markers, and MCP config only for the
``manim-studio`` server key. An existing file that cannot be merged is skipped, never overwritten.
"""

from __future__ import annotations

import json
import shlex
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from manim_studio.agent_docs import (
    AGENTS_BEGIN,
    AGENTS_END,
    MCP_SERVER_NAME,
    SKILL_BODY,
    SKILL_DESCRIPTION,
    SKILL_NAME,
    agents_block,
)

__all__ = [
    "TARGETS",
    "PlannedFile",
    "apply_plan",
    "cursor_rule",
    "manual_instructions",
    "merge_agents_md",
    "merge_mcp_json",
    "plan_init",
    "render_skill",
]

TARGETS = ("claude", "cursor", "agents", "mcp")

SKILL_PATH = f".claude/skills/{SKILL_NAME}/SKILL.md"
CURSOR_RULE_PATH = ".cursor/rules/manim-studio.mdc"
CURSOR_MCP_PATH = ".cursor/mcp.json"
CLAUDE_MCP_PATH = ".mcp.json"
AGENTS_PATH = "AGENTS.md"


@dataclass(frozen=True, slots=True)
class PlannedFile:
    """One file ``agent-init`` would write, and what it would do."""

    relpath: str  #: posix style, relative to the project directory
    content: str  #: full content after the change
    action: str  #: "create" | "update" | "unchanged" | "skip"
    reason: str = ""


def render_skill() -> str:
    """The skill as a Claude Code skill file: front matter, then :data:`SKILL_BODY`."""
    return (
        "---\n"
        f"name: {SKILL_NAME}\n"
        f"description: {json.dumps(SKILL_DESCRIPTION)}\n"
        "---\n\n"
        f"{SKILL_BODY}"
    )


def cursor_rule(command: str) -> str:
    """The Cursor rule: applies to Python files, with the same guidance and the server command."""
    return (
        "---\n"
        "description: Write and check ManimGL scenes with Manim Studio\n"
        "globs: **/*.py\n"
        "alwaysApply: false\n"
        "---\n\n"
        f"{SKILL_BODY}\n"
        f"The MCP server starts with `{command} mcp --root .` (see `.cursor/mcp.json`).\n"
    )


def manual_instructions(command: str) -> str:
    """How to register the server with a client that this tool does not configure."""
    return (
        "Codex and opencode are not configured automatically. Register an stdio MCP server with "
        f"the command `{command} mcp` (add `--root <project>` to pin the workspace)."
    )


def _server_entry(command: str) -> dict[str, Any]:
    parts = shlex.split(command)
    if not parts:
        raise ValueError("the command must not be empty")
    return {"command": parts[0], "args": [*parts[1:], "mcp"]}


def merge_mcp_json(existing: str | None, command: str) -> str:
    """``.mcp.json`` content with the ``manim-studio`` server set; other servers are kept.

    Raises :class:`ValueError` when ``existing`` is not a JSON object.
    """
    data: dict[str, Any] = {}
    if existing is not None and existing.strip():
        loaded = json.loads(existing)
        if not isinstance(loaded, dict):
            raise ValueError("the file does not hold a JSON object")
        data = loaded
    servers = data.get("mcpServers")
    if not isinstance(servers, dict):
        servers = {}
    servers[MCP_SERVER_NAME] = _server_entry(command)
    data["mcpServers"] = servers
    return json.dumps(data, indent=2) + "\n"


def merge_agents_md(existing: str | None) -> str:
    """``AGENTS.md`` with the managed block in place.

    Text outside the markers is never touched. A file without markers gets the block appended.
    """
    block = agents_block()
    if existing is None or not existing.strip():
        return block + "\n"
    begin = existing.find(AGENTS_BEGIN)
    end = existing.find(AGENTS_END)
    if begin != -1 and end != -1 and end > begin:
        return existing[:begin] + block + existing[end + len(AGENTS_END) :]
    separator = "\n" if existing.endswith("\n") else "\n\n"
    return existing + separator + block + "\n"


def _read(project: Path, relpath: str) -> str | None:
    path = project / relpath
    if not path.is_file():
        return None
    return path.read_text(encoding="utf-8")


def _plan_owned(project: Path, relpath: str, content: str, force: bool) -> PlannedFile:
    current = _read(project, relpath)
    if current is None:
        return PlannedFile(relpath, content, "create")
    if current == content:
        return PlannedFile(relpath, content, "unchanged")
    if force:
        return PlannedFile(relpath, content, "update", "replaced (forced)")
    return PlannedFile(relpath, content, "skip", "modified; use --force to replace it")


def _plan_agents(project: Path) -> PlannedFile:
    current = _read(project, AGENTS_PATH)
    content = merge_agents_md(current)
    if current is None:
        return PlannedFile(AGENTS_PATH, content, "create")
    if content == current:
        return PlannedFile(AGENTS_PATH, content, "unchanged")
    return PlannedFile(AGENTS_PATH, content, "update", "managed block refreshed")


def _plan_json(project: Path, relpath: str, command: str) -> PlannedFile:
    current = _read(project, relpath)
    try:
        content = merge_mcp_json(current, command)
    except ValueError as exc:
        return PlannedFile(relpath, current or "", "skip", f"not valid MCP config: {exc}")
    if current is None:
        return PlannedFile(relpath, content, "create")
    if content == current:
        return PlannedFile(relpath, content, "unchanged")
    return PlannedFile(relpath, content, "update", "manim-studio server set")


def plan_init(
    project: Path,
    targets: Sequence[str] = TARGETS,
    *,
    command: str = "manim-studio",
    force: bool = False,
) -> tuple[PlannedFile, ...]:
    """What ``agent-init`` would do for ``targets``. Writes nothing."""
    unknown = [target for target in targets if target not in TARGETS]
    if unknown:
        choices = ", ".join(TARGETS)
        raise ValueError(f"unknown target(s): {', '.join(unknown)}; choose from {choices}")

    planned: dict[str, PlannedFile] = {}

    def add(entry: PlannedFile) -> None:
        planned.setdefault(entry.relpath, entry)

    for target in targets:
        if target == "claude":
            add(_plan_owned(project, SKILL_PATH, render_skill(), force))
        elif target == "cursor":
            add(_plan_owned(project, CURSOR_RULE_PATH, cursor_rule(command), force))
            add(_plan_json(project, CURSOR_MCP_PATH, command))
        elif target == "agents":
            add(_plan_agents(project))
        elif target == "mcp":
            add(_plan_json(project, CLAUDE_MCP_PATH, command))
            add(_plan_json(project, CURSOR_MCP_PATH, command))
    return tuple(planned.values())


def apply_plan(project: Path, plan: Sequence[PlannedFile]) -> tuple[PlannedFile, ...]:
    """Write every ``create`` and ``update`` entry; ``unchanged`` and ``skip`` are left alone."""
    for entry in plan:
        if entry.action not in ("create", "update"):
            continue
        path = project / entry.relpath
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(entry.content, encoding="utf-8", newline="\n")
    return tuple(plan)
