"""PyInstaller entry point of the console ``manim-studio`` executable."""

from __future__ import annotations

from manim_studio.frozen_entry import cli_entry

if __name__ == "__main__":
    raise SystemExit(cli_entry())
