"""agent_init: planning and writing the agent instructions into a project (pure, no GL)."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from manim_studio import agent_init
from manim_studio.agent_docs import AGENTS_BEGIN, AGENTS_END, SKILL_BODY
from manim_studio.agent_init import (
    PlannedFile,
    apply_plan,
    cursor_rule,
    manual_instructions,
    merge_agents_md,
    merge_mcp_json,
    merge_opencode_json,
    plan_init,
    render_skill,
)
from manim_studio.cli import main

REPO = Path(__file__).resolve().parents[1]
OPENCODE_SKILL = REPO / ".opencode" / "skills" / "manim-scene-authoring" / "SKILL.md"
COLOUR_CONSTANT = re.compile(r"\b(BLUE|RED|GREEN|YELLOW|WHITE|ORANGE|PURPLE|BLACK|PINK|TEAL)\b")
FENCE = re.compile(r"```.*?```", re.DOTALL)

ALL_FILES = {
    ".claude/skills/manim-scene-authoring/SKILL.md",
    ".cursor/rules/manim-studio.mdc",
    "AGENTS.md",
    ".mcp.json",
    ".cursor/mcp.json",
    "opencode.json",
}


class TestSkill:
    def test_has_front_matter_with_the_skill_name(self) -> None:
        text = render_skill()
        assert text.startswith("---\n")
        assert "name: manim-scene-authoring" in text

    def test_code_examples_use_hex_colours_only(self) -> None:
        for block in FENCE.findall(SKILL_BODY):
            assert COLOUR_CONSTANT.search(block) is None, block

    def test_the_committed_opencode_copy_matches_the_template(self) -> None:
        assert OPENCODE_SKILL.read_text(encoding="utf-8") == render_skill()


class TestMergeAgentsMd:
    def test_absent_file_gets_only_the_block(self) -> None:
        text = merge_agents_md(None)
        assert text.startswith(AGENTS_BEGIN)
        assert text.rstrip().endswith(AGENTS_END)

    def test_existing_text_is_kept_verbatim_before_the_block(self) -> None:
        original = "# Project\n\nSome rules.\n"
        text = merge_agents_md(original)
        assert text.startswith(original)
        assert AGENTS_BEGIN in text

    def test_markers_are_replaced_and_surrounding_text_untouched(self) -> None:
        original = f"before\n\n{AGENTS_BEGIN}\nold stuff\n{AGENTS_END}\n\nafter\n"
        text = merge_agents_md(original)
        assert text.startswith("before\n\n")
        assert text.endswith("\n\nafter\n")
        assert "old stuff" not in text
        assert text.count(AGENTS_BEGIN) == 1

    def test_merging_twice_equals_merging_once(self) -> None:
        once = merge_agents_md("# Project\n")
        assert merge_agents_md(once) == once


class TestMergeMcpJson:
    def test_absent_file_gets_one_server(self) -> None:
        data = json.loads(merge_mcp_json(None, "manim-studio"))
        assert data == {
            "mcpServers": {"manim-studio": {"command": "manim-studio", "args": ["mcp"]}}
        }

    def test_other_servers_are_preserved(self) -> None:
        existing = json.dumps({"mcpServers": {"github": {"command": "gh-mcp", "args": []}}})
        data = json.loads(merge_mcp_json(existing, "manim-studio"))
        assert set(data["mcpServers"]) == {"github", "manim-studio"}
        assert data["mcpServers"]["github"]["command"] == "gh-mcp"

    def test_stale_manim_studio_entry_is_replaced(self) -> None:
        existing = json.dumps({"mcpServers": {"manim-studio": {"command": "old", "args": ["x"]}}})
        data = json.loads(merge_mcp_json(existing, "uv run --directory repo manim-studio"))
        assert data["mcpServers"]["manim-studio"] == {
            "command": "uv",
            "args": ["run", "--directory", "repo", "manim-studio", "mcp"],
        }

    def test_invalid_json_is_refused(self) -> None:
        with pytest.raises(ValueError):
            merge_mcp_json("{not json", "manim-studio")

    def test_non_object_json_is_refused(self) -> None:
        with pytest.raises(ValueError):
            merge_mcp_json("[1, 2]", "manim-studio")


class TestMergeOpencodeJson:
    def test_absent_file_gets_a_local_server_with_a_command_array(self) -> None:
        data = json.loads(merge_opencode_json(None, "manim-studio"))
        assert data == {
            "mcp": {"manim-studio": {"type": "local", "command": ["manim-studio", "mcp"]}}
        }

    def test_command_with_leading_words_is_split_into_the_array(self) -> None:
        data = json.loads(merge_opencode_json(None, "uv run --directory repo manim-studio"))
        assert data["mcp"]["manim-studio"]["command"] == [
            "uv",
            "run",
            "--directory",
            "repo",
            "manim-studio",
            "mcp",
        ]

    def test_other_keys_and_servers_are_kept(self) -> None:
        existing = json.dumps(
            {
                "model": "provider/model",
                "mcp": {"github": {"type": "local", "command": ["gh-mcp"]}},
            }
        )
        data = json.loads(merge_opencode_json(existing, "manim-studio"))
        assert data["model"] == "provider/model"
        assert set(data["mcp"]) == {"github", "manim-studio"}

    def test_merging_twice_equals_merging_once(self) -> None:
        once = merge_opencode_json(None, "manim-studio")
        assert merge_opencode_json(once, "manim-studio") == once

    def test_invalid_json_is_refused(self) -> None:
        with pytest.raises(ValueError):
            merge_opencode_json("// comment\n{}", "manim-studio")


class TestRegistrationText:
    def test_cursor_rule_names_the_command_the_config_runs(self) -> None:
        # The rule must describe the server entry that .cursor/mcp.json actually holds.
        assert "`manim-studio mcp`" in cursor_rule("manim-studio")
        assert "--root" not in cursor_rule("manim-studio")

    def test_codex_is_given_the_exact_registration_command(self) -> None:
        text = manual_instructions("uv run --directory repo manim-studio")
        assert "codex mcp add manim-studio -- uv run --directory repo manim-studio mcp" in text

    def test_opencode_is_written_not_just_described(self, tmp_path: Path) -> None:
        plan = plan_init(tmp_path, ["opencode"])
        assert [entry.relpath for entry in plan] == ["opencode.json"]
        assert plan[0].action == "create"


class TestPlanAndApply:
    def test_every_target_creates_its_files(self, tmp_path: Path) -> None:
        plan = plan_init(tmp_path)
        assert {entry.relpath for entry in plan} == ALL_FILES
        assert all(entry.action == "create" for entry in plan)
        apply_plan(tmp_path, plan)
        assert {
            p.relative_to(tmp_path).as_posix() for p in tmp_path.rglob("*") if p.is_file()
        } == ALL_FILES

    def test_second_run_changes_nothing(self, tmp_path: Path) -> None:
        apply_plan(tmp_path, plan_init(tmp_path))
        again = plan_init(tmp_path)
        assert {entry.action for entry in again} == {"unchanged"}

    def test_edited_skill_is_skipped_then_forced(self, tmp_path: Path) -> None:
        apply_plan(tmp_path, plan_init(tmp_path, ["claude"]))
        skill = tmp_path / ".claude/skills/manim-scene-authoring/SKILL.md"
        skill.write_text("my own notes\n", encoding="utf-8")

        (entry,) = plan_init(tmp_path, ["claude"])
        assert entry.action == "skip"
        assert "--force" in entry.reason
        apply_plan(tmp_path, [entry])
        assert skill.read_text(encoding="utf-8") == "my own notes\n"

        (forced,) = plan_init(tmp_path, ["claude"], force=True)
        assert forced.action == "update"
        apply_plan(tmp_path, [forced])
        assert skill.read_text(encoding="utf-8") == render_skill()

    def test_dry_run_is_just_the_plan(self, tmp_path: Path) -> None:
        plan = plan_init(tmp_path)
        assert plan
        assert not any(tmp_path.iterdir())

    def test_invalid_existing_mcp_file_is_skipped_untouched(self, tmp_path: Path) -> None:
        broken = tmp_path / ".mcp.json"
        broken.write_text("{ broken", encoding="utf-8")
        plan = {entry.relpath: entry for entry in plan_init(tmp_path, ["mcp"])}
        assert plan[".mcp.json"].action == "skip"
        apply_plan(tmp_path, list(plan.values()))
        assert broken.read_text(encoding="utf-8") == "{ broken"

    def test_mcp_target_covers_both_mcp_files(self, tmp_path: Path) -> None:
        plan = plan_init(tmp_path, ["mcp"])
        assert {entry.relpath for entry in plan} == {".mcp.json", ".cursor/mcp.json"}

    def test_cursor_target_writes_rule_and_cursor_mcp(self, tmp_path: Path) -> None:
        plan = plan_init(tmp_path, ["cursor"])
        assert {entry.relpath for entry in plan} == {
            ".cursor/rules/manim-studio.mdc",
            ".cursor/mcp.json",
        }

    def test_unknown_target_is_refused(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="unknown target"):
            plan_init(tmp_path, ["codex-typo"])

    def test_empty_command_is_refused(self) -> None:
        with pytest.raises(ValueError, match="empty"):
            agent_init.merge_mcp_json(None, "   ")

    def test_planned_file_is_a_value(self) -> None:
        entry = PlannedFile("AGENTS.md", "x", "create")
        assert entry.reason == ""


class TestAgentInitCommand:
    def test_json_lists_each_file(self, tmp_path: Path, capsys) -> None:
        assert main(["agent-init", str(tmp_path), "--json"]) == 0
        payload = json.loads(capsys.readouterr().out)
        assert {f["path"] for f in payload["files"]} == ALL_FILES
        assert all(f["action"] == "created" for f in payload["files"])
        assert "manual" in payload

    def test_dry_run_writes_nothing(self, tmp_path: Path, capsys) -> None:
        assert main(["agent-init", str(tmp_path), "--dry-run"]) == 0
        assert "would create" in capsys.readouterr().out
        assert not (tmp_path / "AGENTS.md").exists()

    def test_unknown_target_is_usage_error(self, tmp_path: Path) -> None:
        assert main(["agent-init", str(tmp_path), "--target", "nope"]) == 2

    def test_empty_target_is_usage_error(self, tmp_path: Path) -> None:
        assert main(["agent-init", str(tmp_path), "--target", " , "]) == 2

    def test_a_file_is_not_a_project(self, tmp_path: Path) -> None:
        file = tmp_path / "file.txt"
        file.write_text("x", encoding="utf-8")
        assert main(["agent-init", str(file)]) == 2

    def test_text_output_names_each_action(self, tmp_path: Path, capsys) -> None:
        main(["agent-init", str(tmp_path), "--target", "agents"])
        out = capsys.readouterr().out
        assert "created" in out and "AGENTS.md" in out
