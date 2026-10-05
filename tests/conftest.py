"""Pytest configuration for the Manim Editor test suite."""

from __future__ import annotations

import os

# Qt tests must never require a display server; offscreen works on all platforms.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
