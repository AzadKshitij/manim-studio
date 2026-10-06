"""Entry point for exports: ``python -m manim_editor.manimgl_launcher``.

Behaves like ``python -m manimlib`` (same CLI, same exit behavior) but applies
the manimgl LaTeX compatibility fix first, so ``Tex``/``Brace`` exports also
work on MiKTeX.  :func:`manim_editor.export.build_export_plan` uses this module
instead of ``manimlib`` directly.
"""

from __future__ import annotations

__all__ = ["main"]


def main() -> int:
    from manim_editor.latex_compat import patch_manimgl_latex_compat

    patch_manimgl_latex_compat()
    from manimlib.__main__ import main as manimgl_main

    manimgl_main()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
