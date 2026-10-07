"""Entry point for exports: ``python -m manim_studio.manimgl_launcher``.

Behaves like ``python -m manimlib`` (same CLI, same exit behavior) but applies
the manimgl LaTeX compatibility fix first, so ``Tex``/``Brace`` exports also
work on MiKTeX.  :func:`manim_studio.export.build_export_plan` uses this module
instead of ``manimlib`` directly.

When the editor has retimed timeline sections, the plan adds a
``--timing-file`` argument pointing at a JSON ``{block_index: factor}`` map.
This module consumes that argument before manimgl's own CLI parser sees it and
installs a :class:`~manim_studio.scrub.TimingScaler` on every scene, so the
exported render bakes in the retimed ``run_time``s instead of the scene's
original timing.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

__all__ = ["main"]

_TIMING_ARG = "--timing-file"


def _consume_timing_arg(argv: list[str]) -> dict[int, float]:
    """Pop ``--timing-file`` from ``argv`` and return the parsed factors."""
    for position, argument in enumerate(list(argv)):
        if argument == _TIMING_ARG:
            if position + 1 >= len(argv):
                return {}
            raw = argv[position + 1]
            del argv[position : position + 2]
            return _load_factors(Path(raw))
        if argument.startswith(f"{_TIMING_ARG}="):
            raw = argument.split("=", 1)[1]
            del argv[position]
            return _load_factors(Path(raw))
    return {}


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


def _install_timing_scaler(factors: dict[int, float]) -> None:
    """Wrap ``Scene.run`` so retimed blocks render at their new ``run_time``."""
    from manimlib.scene.scene import Scene

    from manim_studio.scrub import TimingScaler

    original_run = Scene.run

    def run(self):  # type: ignore[no-untyped-def]
        TimingScaler(self, factors)
        return original_run(self)

    Scene.run = run


def main() -> int:
    # Strip our argument BEFORE importing anything that pulls in manimlib:
    # ``manimlib.config`` parses ``sys.argv`` at import time, so a leftover
    # ``--timing-file`` would make its argparse parser exit with error 2.
    factors = _consume_timing_arg(sys.argv)

    from manim_studio.latex_compat import patch_manimgl_latex_compat

    patch_manimgl_latex_compat()
    from manimlib.__main__ import main as manimgl_main

    if factors:
        _install_timing_scaler(factors)

    manimgl_main()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
