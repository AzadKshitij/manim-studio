"""PyInstaller entry point of the windowed ``ManimEditor`` executable."""

from __future__ import annotations

from manim_editor.frozen_entry import gui_entry

if __name__ == "__main__":
    raise SystemExit(gui_entry())
