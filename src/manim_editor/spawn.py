"""Frozen-aware argv builders for helper processes.

The editor launches two kinds of helper processes:

* the **preview host** (the only GL process), and
* an **export renderer** (``manimgl -w`` through :mod:`manim_editor.manimgl_launcher`).

In a development checkout both are started as ``python -m <module> …``.  A
PyInstaller-frozen build cannot do that: ``sys.executable`` is our own bundled
exe, which ignores ``-m``.  Frozen builds therefore re-invoke the same exe with
an internal flag, and :mod:`manim_editor.frozen_entry` routes those flags back
to the right module.

This module is the single source of truth for that protocol: it produces the
commands *and* classifies them, so the two sides can never drift apart.
See ADR 0005.
"""

from __future__ import annotations

import sys
from collections.abc import Sequence
from typing import Literal

__all__ = [
    "MANIMGL_EXPORT_FLAG",
    "PREVIEW_HOST_FLAG",
    "LaunchTarget",
    "classify_invocation",
    "frozen",
    "manimgl_export_command",
    "preview_host_command",
]

#: Internal flag: run the preview host with the remaining arguments.
PREVIEW_HOST_FLAG = "--preview-host"
#: Internal flag: render with manimgl using the remaining arguments.
MANIMGL_EXPORT_FLAG = "--manimgl-export"

_PREVIEW_HOST_MODULE = "manim_editor.preview_host"
_MANIMGL_EXPORT_MODULE = "manim_editor.manimgl_launcher"

LaunchTarget = Literal["preview_host", "manimgl_export", "cli"]


def frozen() -> bool:
    """True when running inside a PyInstaller-frozen executable."""
    return bool(getattr(sys, "frozen", False))


def preview_host_command(args: Sequence[str]) -> tuple[str, tuple[str, ...]]:
    """Return ``(executable, argv[1:])`` that starts the preview host.

    ``args`` are the arguments normally passed after
    ``python -m manim_editor.preview_host``.
    """
    if frozen():
        return sys.executable, (PREVIEW_HOST_FLAG, *args)
    return sys.executable, ("-m", _PREVIEW_HOST_MODULE, *args)


def manimgl_export_command(args: Sequence[str]) -> tuple[str, tuple[str, ...]]:
    """Return ``(executable, argv[1:])`` that renders with manimgl.

    ``args`` are the arguments normally passed after
    ``python -m manim_editor.manimgl_launcher``.
    """
    if frozen():
        return sys.executable, (MANIMGL_EXPORT_FLAG, *args)
    return sys.executable, ("-m", _MANIMGL_EXPORT_MODULE, *args)


def classify_invocation(argv: Sequence[str]) -> tuple[LaunchTarget, tuple[str, ...]]:
    """Split a full command tail into ``(target, remaining arguments)``.

    This is the inverse of :func:`preview_host_command` /
    :func:`manimgl_export_command`; anything that is not an internal flag is
    passed through to the CLI untouched.
    """
    head = tuple(argv[:1])
    if head == (PREVIEW_HOST_FLAG,):
        return "preview_host", tuple(argv[1:])
    if head == (MANIMGL_EXPORT_FLAG,):
        return "manimgl_export", tuple(argv[1:])
    return "cli", tuple(argv)
