"""Tests for the frozen-aware subprocess protocol (spawn + dispatch)."""

from __future__ import annotations

import sys

import pytest

from manim_studio import spawn


class TestDevelopmentCommands:
    def test_preview_host_uses_module_switch(self) -> None:
        executable, args = spawn.preview_host_command(["scene.py", "--port", "1"])
        assert executable == sys.executable
        assert list(args) == ["-m", "manim_studio.preview_host", "scene.py", "--port", "1"]

    def test_export_uses_module_switch(self) -> None:
        executable, args = spawn.manimgl_export_command(["scene.py", "-w"])
        assert executable == sys.executable
        assert list(args) == ["-m", "manim_studio.manimgl_launcher", "scene.py", "-w"]

    def test_not_frozen_by_default(self) -> None:
        assert spawn.frozen() is False


class TestFrozenCommands:
    @pytest.fixture(autouse=True)
    def _freeze(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(sys, "frozen", True, raising=False)

    def test_frozen_flag_reported(self) -> None:
        assert spawn.frozen() is True

    def test_preview_host_uses_flag(self) -> None:
        executable, args = spawn.preview_host_command(["scene.py", "--embedded"])
        assert executable == sys.executable
        assert list(args) == [spawn.PREVIEW_HOST_FLAG, "scene.py", "--embedded"]

    def test_export_uses_flag(self) -> None:
        executable, args = spawn.manimgl_export_command(["scene.py", "-w"])
        assert executable == sys.executable
        assert list(args) == [spawn.MANIMGL_EXPORT_FLAG, "scene.py", "-w"]


class TestClassifyInvocation:
    @pytest.fixture(autouse=True)
    def _freeze(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Classification only ever sees frozen-style commands (ADR 0005): the
        # dispatch runs in the frozen exe, dev checkouts use `python -m` directly.
        monkeypatch.setattr(sys, "frozen", True, raising=False)

    def test_roundtrip_preview_host(self) -> None:
        _, command = spawn.preview_host_command(["a.py", "--port", "7"])
        target, rest = spawn.classify_invocation(command)
        assert target == "preview_host"
        assert rest == ("a.py", "--port", "7")

    def test_roundtrip_export(self) -> None:
        _, command = spawn.manimgl_export_command(["a.py", "-w", "--hd"])
        target, rest = spawn.classify_invocation(command)
        assert target == "manimgl_export"
        assert rest == ("a.py", "-w", "--hd")

    @pytest.mark.parametrize("argv", [(), ("gui",), ("check", "a.py"), ("--version",)])
    def test_cli_passthrough(self, argv: tuple[str, ...]) -> None:
        target, rest = spawn.classify_invocation(argv)
        assert target == "cli"
        assert rest == argv

    def test_dev_commands_pass_through_untouched(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(sys, "frozen", False, raising=False)
        for factory in (spawn.preview_host_command, spawn.manimgl_export_command):
            _, command = factory(["x.py"])
            target, rest = spawn.classify_invocation(command)
            assert target == "cli"
            assert rest == tuple(command)

    def test_flags_are_not_cli_subcommands(self) -> None:
        # The dispatch flags must never collide with the argparse CLI surface.
        from manim_studio.frozen_entry import CLI_SUBCOMMANDS

        assert spawn.PREVIEW_HOST_FLAG not in CLI_SUBCOMMANDS
        assert spawn.MANIMGL_EXPORT_FLAG not in CLI_SUBCOMMANDS
