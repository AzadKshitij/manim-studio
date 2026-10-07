"""Entry points for the PyInstaller-frozen executables (see ADR 0005).

One spec file builds two executables from this module:

* ``ManimStudio.exe`` (windowed) — :func:`gui_entry`; double-clicking it opens the
  editor, and a bare scene path (drag & drop) opens that file.
* ``manim-studio.exe`` (console) — :func:`cli_entry`; the full CLI, and the worker
  exe the frozen parent re-invokes for preview/export children.

Both entries classify the arguments through :mod:`manim_studio.spawn`, so a frozen
parent process can launch its own executable instead of ``python -m …``.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Sequence
from typing import IO, cast

from manim_studio.spawn import LaunchTarget, classify_invocation

__all__ = ["CLI_SUBCOMMANDS", "cli_entry", "ensure_standard_streams", "gui_entry"]

#: Mirrors the subparsers of :func:`manim_studio.cli.build_parser`; guarded by a test
#: so the two can never drift.
CLI_SUBCOMMANDS = frozenset(
    {
        "api-index",
        "check",
        "export",
        "gui",
        "latex",
        "new",
        "preview",
        "still",
        "templates",
        "trace",
    }
)


def ensure_standard_streams() -> None:
    """Give stdin/stdout/stderr a usable target in a console-less process.

    A windowed exe started without redirection has ``sys.stdout is None``; the
    unguarded ``print``/``logging`` calls downstream would then crash the process.
    When the parent *did* redirect the handles (``QProcess``, pipes) the file
    descriptor is valid and we rebuild the stream on top of a ``dup`` of it, so
    progress output still reaches the caller; otherwise fall back to the null
    device.
    """
    for name, fd, mode in (("stdin", 0, "r"), ("stdout", 1, "w"), ("stderr", 2, "w")):
        if getattr(sys, name) is not None:
            continue
        setattr(sys, name, _stream_for_fd(fd, mode))


def _stream_for_fd(fd: int, mode: str) -> IO[str]:
    try:
        stream = os.fdopen(os.dup(fd), mode, encoding="utf-8", errors="replace")
    except OSError:
        # No usable descriptor (double-clicked windowed exe): write nowhere.
        stream = open(os.devnull, mode, encoding="utf-8", errors="replace")  # noqa: SIM115
    return cast("IO[str]", stream)


def _run_target(target: LaunchTarget, rest: Sequence[str]) -> int:
    if target == "preview_host":
        from manim_studio.preview_host import main as host_main

        return int(host_main(list(rest)))
    if target == "manimgl_export":
        from manim_studio.manimgl_launcher import main as export_main

        # manimgl parses ``sys.argv`` itself (manimlib.config does it at import
        # time), so present the remaining arguments as a plain argv first.
        saved_argv = sys.argv
        try:
            sys.argv = [saved_argv[0], *rest]
            return int(export_main())
        finally:
            sys.argv = saved_argv
    from manim_studio.cli import main as cli_main

    return int(cli_main(list(rest)))


def cli_entry() -> int:
    """Entry point of the console executable ``manim-studio``."""
    ensure_standard_streams()
    target, rest = classify_invocation(sys.argv[1:])
    return _run_target(target, rest)


def gui_entry() -> int:
    """Entry point of the windowed executable ``ManimStudio``."""
    ensure_standard_streams()
    target, args = classify_invocation(sys.argv[1:])
    if target != "cli":
        return _run_target(target, args)
    if not args:
        args = ("gui",)
    elif args[0] not in CLI_SUBCOMMANDS and not args[0].startswith("-"):
        # A bare path (double-click / drag & drop) opens the editor on that file.
        args = ("gui", *args)
    return _run_target("cli", args)
