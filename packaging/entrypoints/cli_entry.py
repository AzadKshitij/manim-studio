"""PyInstaller entry point of the console ``manim-editor`` executable."""

from __future__ import annotations

from manim_editor.frozen_entry import cli_entry

if __name__ == "__main__":
    raise SystemExit(cli_entry())
