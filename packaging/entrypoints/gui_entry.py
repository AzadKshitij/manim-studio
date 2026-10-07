"""PyInstaller entry point of the windowed ``ManimStudio`` executable."""

from __future__ import annotations

from manim_studio.frozen_entry import gui_entry

if __name__ == "__main__":
    raise SystemExit(gui_entry())
