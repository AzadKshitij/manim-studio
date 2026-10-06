"""Tests for the frozen executable entry points and their dispatch."""

from __future__ import annotations

import sys
from typing import Any

import pytest

from manim_editor import frozen_entry


def _capture_cli(monkeypatch: pytest.MonkeyPatch) -> list[list[str]]:
    """Replace the CLI main with a recorder, returning the argv log."""
    log: list[list[str]] = []

    def fake_main(argv: list[str] | None = None) -> int:
        log.append(list(argv) if argv is not None else [])
        return 0

    monkeypatch.setattr("manim_editor.cli.main", fake_main)
    return log


class TestEnsureStandardStreams:
    def test_none_stdout_becomes_writable(self, monkeypatch: pytest.MonkeyPatch) -> None:
        original = sys.stdout
        monkeypatch.setattr(sys, "stdout", None)
        frozen_entry.ensure_standard_streams()
        assert sys.stdout is not None
        sys.stdout.write("ok")
        sys.stdout.flush()
        monkeypatch.setattr(sys, "stdout", original)  # keep pytest capture intact

    def test_existing_streams_untouched(self, monkeypatch: pytest.MonkeyPatch) -> None:
        sentinel = sys.stdout
        frozen_entry.ensure_standard_streams()
        assert sys.stdout is sentinel
        assert sys.stderr is not None and sys.stdin is not None


class TestRunTarget:
    def test_cli_target_passes_argv(self, monkeypatch: pytest.MonkeyPatch) -> None:
        log = _capture_cli(monkeypatch)
        code = frozen_entry._run_target("cli", ("check", "a.py"))
        assert code == 0
        assert log == [["check", "a.py"]]

    def test_preview_host_target(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from manim_editor import preview_host

        seen: dict[str, Any] = {}

        def fake_host(argv: list[str]) -> int:
            seen["argv"] = list(argv)
            return 3

        monkeypatch.setattr(preview_host, "main", fake_host)
        code = frozen_entry._run_target("preview_host", ("a.py", "--port", "9"))
        assert code == 3
        assert seen["argv"] == ["a.py", "--port", "9"]

    def test_manimgl_export_target_patches_and_restores_argv(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from manim_editor import manimgl_launcher

        seen: dict[str, Any] = {}

        def fake_export() -> int:
            seen["argv"] = list(sys.argv)
            return 7

        monkeypatch.setattr(manimgl_launcher, "main", fake_export)
        before = list(sys.argv)
        code = frozen_entry._run_target("manimgl_export", ("a.py", "-w", "--hd"))
        assert code == 7
        # manimgl must see a plain argv without the internal dispatch flag …
        assert seen["argv"] == [before[0], "a.py", "-w", "--hd"]
        # … and our own argv must be restored afterwards.
        assert sys.argv == before


class TestGuiEntry:
    def test_no_args_opens_gui(self, monkeypatch: pytest.MonkeyPatch) -> None:
        log = _capture_cli(monkeypatch)
        monkeypatch.setattr(sys, "argv", ["ManimEditor.exe"])
        assert frozen_entry.gui_entry() == 0
        assert log == [["gui"]]

    def test_bare_path_opens_that_file(self, monkeypatch: pytest.MonkeyPatch) -> None:
        log = _capture_cli(monkeypatch)
        monkeypatch.setattr(sys, "argv", ["ManimEditor.exe", "scenes/intro.py"])
        assert frozen_entry.gui_entry() == 0
        assert log == [["gui", "scenes/intro.py"]]

    def test_known_subcommand_passes_through(self, monkeypatch: pytest.MonkeyPatch) -> None:
        log = _capture_cli(monkeypatch)
        monkeypatch.setattr(sys, "argv", ["ManimEditor.exe", "check", "scenes/intro.py"])
        assert frozen_entry.gui_entry() == 0
        assert log == [["check", "scenes/intro.py"]]

    def test_preview_flag_is_not_wrapped_in_gui(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from manim_editor import preview_host

        seen: dict[str, Any] = {}

        def fake_host(argv: list[str]) -> int:
            seen["argv"] = list(argv)
            return 0

        monkeypatch.setattr(preview_host, "main", fake_host)
        monkeypatch.setattr(
            sys, "argv", ["ManimEditor.exe", "--preview-host", "a.py", "--embedded"]
        )
        assert frozen_entry.gui_entry() == 0
        assert seen["argv"] == ["a.py", "--embedded"]


class TestCliEntry:
    def test_plain_args_reach_the_cli(self, monkeypatch: pytest.MonkeyPatch) -> None:
        log = _capture_cli(monkeypatch)
        monkeypatch.setattr(sys, "argv", ["manim-editor", "templates"])
        assert frozen_entry.cli_entry() == 0
        assert log == [["templates"]]

    def test_worker_flag_routes_to_host(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from manim_editor import preview_host

        seen: dict[str, Any] = {}

        def fake_host(argv: list[str]) -> int:
            seen["argv"] = list(argv)
            return 0

        monkeypatch.setattr(preview_host, "main", fake_host)
        monkeypatch.setattr(sys, "argv", ["manim-editor", "--preview-host", "a.py"])
        assert frozen_entry.cli_entry() == 0
        assert seen["argv"] == ["a.py"]


class TestSubcommandMirror:
    def test_cli_subcommands_match_the_parser(self) -> None:
        from argparse import _SubParsersAction

        from manim_editor.cli import build_parser

        parser = build_parser()
        choices: set[str] = set()
        for action in parser._actions:
            if isinstance(action, _SubParsersAction):
                choices.update(action.choices)
        assert choices == set(frozen_entry.CLI_SUBCOMMANDS)
