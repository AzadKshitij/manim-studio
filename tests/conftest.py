"""Pytest configuration for the Manim Studio test suite."""

from __future__ import annotations

import os
import sys

import pytest

# Qt tests must never require a display server; offscreen works on all platforms.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

# manimlib parses sys.argv when it is first imported. Pytest flags such as -m or -p are not
# manimgl options, so the first test to import it would fail to start. Import it once here,
# with a clean argv, before any test module runs.
_pytest_argv = sys.argv
sys.argv = [_pytest_argv[0]]
try:
    import manimlib  # noqa: F401
except ImportError:
    pass
finally:
    sys.argv = _pytest_argv


@pytest.fixture(autouse=True)
def _unsaved_prompt_never_blocks(monkeypatch: pytest.MonkeyPatch) -> None:
    """Answer "discard" to the unsaved-changes prompt for every test.

    pytest-qt closes each test's windows before fixtures tear down, so a window left with
    unsaved edits would open the real modal prompt and hang the run.  Tests that exercise
    the prompt replace this default with their own answer.
    """
    from manim_studio.editor import main_window

    monkeypatch.setattr(main_window, "ask_discard", lambda *_args, **_kwargs: "discard")
