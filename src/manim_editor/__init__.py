"""Manim Editor — realtime editor for ManimGL scenes.

This package intentionally keeps its import cheap: no Qt, no manimgl, no OpenGL
is imported here so that CLI entry points and pure-core tooling stay usable
everywhere (including headless CI machines).
"""

from __future__ import annotations

__version__ = "0.1.0"

APP_NAME = "Manim Editor"
ORG_NAME = "ManimEditor"

__all__ = ["APP_NAME", "ORG_NAME", "__version__"]
