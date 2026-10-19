"""Entry point for exports: ``python -m manim_studio.manimgl_launcher``.

Behaves like ``python -m manimlib`` (same CLI, same exit behavior) but applies
the manimgl LaTeX compatibility fix first, so ``Tex``/``Brace`` exports also
work on MiKTeX.  :func:`manim_studio.export.build_export_plan` uses this module
instead of ``manimlib`` directly.

Timeline edits reach the render through ``--patch-file``: a sidecar-format JSON
file of anchored patches (:mod:`manim_studio.patches`).  Scene settings (size, fps,
background) arrive through ``--settings-file`` and override the scene's own camera
configuration, so an export matches what the editor shows.  The arguments are consumed
before manimgl's own CLI parser sees them (``manimlib.config`` parses ``sys.argv`` at
import time, so a leftover flag would make it exit with error 2).  The scene file
is then executed as patched in-memory source, exactly what the editor's preview
runs, so preview and export agree.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from manim_studio.patches import Patch
    from manim_studio.scene_settings import SceneSettings

__all__ = ["main"]

_PATCH_ARG = "--patch-file"
_SETTINGS_ARG = "--settings-file"


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


def _consume_settings_arg(argv: list[str]) -> SceneSettings | None:
    """Pop ``--settings-file`` from ``argv`` and return the settings it holds."""
    path = _consume_path_arg(argv, _SETTINGS_ARG)
    if path is None:
        return None
    from manim_studio.sidecar import SidecarError, read_settings_file

    try:
        return read_settings_file(path)
    except SidecarError as exc:
        sys.stderr.write(f"manim-studio: ignoring unreadable settings file {path}: {exc}\n")
        return None


def _install_settings(settings: SceneSettings) -> None:
    """Give every scene these camera settings, ahead of its own class-level defaults.

    ``Scene.__init__`` merges the ``camera_config`` argument last, so the studio's
    settings win over the camera settings the scene source passes up (its generated
    ``__init__``).  The pixel-aspect correction is applied to the camera right away, as the
    host does.
    """
    from manimlib.scene.scene import Scene

    from manim_studio.scene_runner import fit_camera_to_pixel_aspect

    original_init = Scene.__init__

    def __init__(self: Scene, *args: Any, camera_config: dict | None = None, **kwargs: Any) -> None:
        merged = {**(camera_config or {}), **settings.camera_config()}
        original_init(self, *args, camera_config=merged, **kwargs)
        if settings.needs_pixel_fix:
            fit_camera_to_pixel_aspect(self.camera)

    Scene.__init__ = __init__


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
    # Strip our argument BEFORE importing anything that pulls in manimlib.
    patches = _consume_patch_arg(sys.argv)
    settings = _consume_settings_arg(sys.argv)

    from manim_studio.latex_compat import patch_manimgl_latex_compat

    patch_manimgl_latex_compat()
    from manimlib.__main__ import main as manimgl_main

    if patches:
        _install_patched_loader(patches)
    if settings is not None:
        _install_settings(settings)

    manimgl_main()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
