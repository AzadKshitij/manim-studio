"""Entry point for exports: ``python -m manim_studio.manimgl_launcher``.

Behaves like ``python -m manimlib`` (same CLI, same exit behavior) but applies
the manimgl LaTeX compatibility fix first, so ``Tex``/``Brace`` exports also
work on MiKTeX.  :func:`manim_studio.export.build_export_plan` uses this module
instead of ``manimlib`` directly.

Timeline edits reach the render through two optional arguments, both consumed
before manimgl's own CLI parser sees them (``manimlib.config`` parses ``sys.argv``
at import time, so a leftover flag would make it exit with error 2):

``--patch-file``
    A sidecar-format JSON file of anchored timeline patches
    (:mod:`manim_studio.patches`).  The scene file is executed as patched in-memory
    source, exactly what the editor's preview runs, so preview and export agree.
``--timing-file``
    A legacy JSON ``{block_index: factor}`` map; installs a
    :class:`~manim_studio.scrub.TimingScaler` on every scene.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from manim_studio.patches import Patch

__all__ = ["main"]

_TIMING_ARG = "--timing-file"
_PATCH_ARG = "--patch-file"


def _consume_path_arg(argv: list[str], flag: str) -> Path | None:
    """Pop ``flag VALUE`` / ``flag=VALUE`` from ``argv`` and return the path."""
    for position, argument in enumerate(list(argv)):
        if argument == flag:
            if position + 1 >= len(argv):
                del argv[position]
                return None
            raw = argv[position + 1]
            del argv[position : position + 2]
            return Path(raw)
        if argument.startswith(f"{flag}="):
            raw = argument.split("=", 1)[1]
            del argv[position]
            return Path(raw)
    return None


def _consume_timing_arg(argv: list[str]) -> dict[int, float]:
    """Pop ``--timing-file`` from ``argv`` and return the parsed factors."""
    path = _consume_path_arg(argv, _TIMING_ARG)
    return {} if path is None else _load_factors(path)


def _load_factors(path: Path) -> dict[int, float]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(payload, dict):
        return {}
    factors: dict[int, float] = {}
    for index, factor in payload.items():
        try:
            factors[int(index)] = float(factor)
        except (TypeError, ValueError):
            continue
    return factors


def _consume_patch_arg(argv: list[str]) -> tuple[Patch, ...]:
    """Pop ``--patch-file`` from ``argv`` and return the parsed patches."""
    path = _consume_path_arg(argv, _PATCH_ARG)
    if path is None:
        return ()
    from manim_studio.patches import PatchError, parse_patch

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        items = payload["patches"] if isinstance(payload, dict) else payload
        return tuple(parse_patch(item) for item in items)
    except (OSError, ValueError, KeyError, TypeError, PatchError) as exc:
        sys.stderr.write(f"manim-studio: ignoring unreadable patch file {path}: {exc}\n")
        return ()


def _install_timing_scaler(factors: dict[int, float]) -> None:
    """Wrap ``Scene.run`` so retimed blocks render at their new ``run_time``."""
    from manimlib.scene.scene import Scene

    from manim_studio.scrub import TimingScaler

    original_run = Scene.run

    def run(self):  # type: ignore[no-untyped-def]
        TimingScaler(self, factors)
        return original_run(self)

    Scene.run = run


def _install_patched_loader(patches: tuple[Patch, ...]) -> None:
    """Make manimgl execute the scene file as timeline-patched source.

    manimgl imports the scene through ``ModuleLoader.get_module``; wrapping it is
    enough because it is only ever called for the scene file (helper modules go
    through the normal import system).  Files none of the patches apply to load
    untouched.
    """
    from manimlib.module_loader import ModuleLoader

    from manim_studio.patch_runtime import StudioRuntime
    from manim_studio.patches import RUNTIME_NAME, apply_patches
    from manim_studio.scene_runner import load_patched_module

    original = ModuleLoader.get_module
    runtime = StudioRuntime()
    reported = False

    def get_module(file_name: str | None, is_during_reload: bool = False) -> Any:
        nonlocal reported
        if file_name is None:
            return original(file_name, is_during_reload)
        resolved = Path(file_name).resolve()
        try:
            result = apply_patches(resolved.read_text(encoding="utf-8"), patches)
        except (OSError, SyntaxError):
            return original(file_name, is_during_reload)
        if not reported:
            reported = True
            for orphan in result.orphaned:
                sys.stderr.write(f"manim-studio: skipped a timeline edit: {orphan.reason}\n")
        if not result.changed:
            return original(file_name, is_during_reload)
        return load_patched_module(
            ModuleLoader, resolved, result.text, runtime, RUNTIME_NAME, is_during_reload
        )

    ModuleLoader.get_module = staticmethod(get_module)


def main() -> int:
    # Strip our arguments BEFORE importing anything that pulls in manimlib.
    factors = _consume_timing_arg(sys.argv)
    patches = _consume_patch_arg(sys.argv)

    from manim_studio.latex_compat import patch_manimgl_latex_compat

    patch_manimgl_latex_compat()
    from manimlib.__main__ import main as manimgl_main

    if factors:
        _install_timing_scaler(factors)
    if patches:
        _install_patched_loader(patches)

    manimgl_main()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
