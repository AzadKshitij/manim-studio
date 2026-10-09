"""Runtime compatibility fix for manimgl 1.7.2's LaTeX invocation.

manimgl 1.7.2 passes the TeX Live-only ``-no-pdf`` flag to the ``latex``
compiler.  MiKTeX rejects it as an unknown option, so every ``Tex``/``Brace``
compile fails on MiKTeX with a bare ``LatexError: LaTeX compilation failed``.
Upstream fixed this in manim PR #2298 by passing ``-no-pdf`` only for
``xelatex``; this module mirrors that fix at runtime, without editing
site-packages, and is a no-op on TeX Live.

Callers: :func:`manim_studio.scene_runner.run_construct` (preview, trace and
stills) and :mod:`manim_studio.manimgl_launcher` (exports).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Sequence

__all__ = ["patch_manimgl_latex_compat", "strip_unsupported_no_pdf"]


def strip_unsupported_no_pdf(argv: Sequence[str]) -> list[str]:
    """Drop ``-no-pdf`` when the command is the ``latex`` (DVI) compiler.

    ``xelatex`` needs the flag (it switches XDV output on) and is left alone.
    """
    cleaned = list(argv)
    if not cleaned:
        return cleaned
    compiler = str(cleaned[0]).replace("\\", "/").rsplit("/", 1)[-1].lower()
    compiler = compiler.removesuffix(".exe")
    if compiler == "latex" and "-no-pdf" in cleaned:
        cleaned = [item for item in cleaned if item != "-no-pdf"]
    return cleaned


def patch_manimgl_latex_compat() -> bool:
    """Apply the fix to the installed manimlib; idempotent.

    Returns True when this call applied the patch, False when it was already
    applied.  Raises ImportError if manimlib is unavailable (callers only use
    this on paths that already import manimlib).
    """
    from manimlib.utils import tex_file_writing

    from manim_studio.latex import normalize_temp_dir

    # manimgl compiles in tempfile.TemporaryDirectory(); a '~' (8.3 short name)
    # in that path makes TeX abort with "Emergency stop".
    normalize_temp_dir()
    if getattr(tex_file_writing, "_manim_studio_latex_compat", False):
        return False
    real_subprocess = tex_file_writing.subprocess

    class _CompatSubprocess:
        """Module-local shim: only ``tex_file_writing`` sees this object."""

        @staticmethod
        def run(argv: Any, *args: Any, **kwargs: Any) -> Any:
            if isinstance(argv, (list, tuple)):
                argv = strip_unsupported_no_pdf(argv)
            return real_subprocess.run(argv, *args, **kwargs)

    tex_file_writing.subprocess = _CompatSubprocess
    tex_file_writing._manim_studio_latex_compat = True
    return True
