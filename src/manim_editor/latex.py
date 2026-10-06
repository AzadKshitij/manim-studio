"""LaTeX detection for ManimGL ``Tex``/``Brace`` support.

manimgl shells out to ``latex`` + ``dvisvgm`` (see
``manimlib.utils.tex_file_writing``).  Neither ships with Python, so this
module answers "is LaTeX usable right now?" and "where did it come from?".

Search order: ``MANIM_EDITOR_LATEX_DIR`` env override, then the per-user
on-demand install location (``Tools > LaTeX setup`` in the editor), then the
system ``PATH``.  Everything here is stdlib-only so the pure core stays
import-clean per ``AGENTS.md``.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

__all__ = [
    "DEFAULT_TEX_PREAMBLE",
    "LATEX_COMPILE_HINT",
    "LATEX_SETUP_HINT",
    "MANIM_TEX_PACKAGES",
    "LatexProbe",
    "LatexStatus",
    "apply_runtime_path",
    "bin_dir",
    "install_dir",
    "is_latex_error",
    "is_miktex",
    "latex_hint_for",
    "latex_status",
    "probe_compile",
    "runtime_env",
]

#: Hint when the ``latex``/``dvisvgm`` programs are not installed at all.
LATEX_SETUP_HINT = (
    "LaTeX is missing (manimgl needs the 'latex' and 'dvisvgm' programs). "
    "Install it via Tools > LaTeX setup in the editor, "
    "or run 'manim-editor latex install'."
)

#: Hint when the programs exist but the compile itself failed.
LATEX_COMPILE_HINT = (
    "LaTeX is installed but the compile failed. "
    "Open Tools > LaTeX setup > Diagnose (or run 'manim-editor latex diagnose') "
    "for the exact TeX error."
)

#: manimgl's default template preamble, mirrored from ``tex_templates.yml``.
#: Used by :func:`probe_compile` so diagnosis exercises the real package set.
DEFAULT_TEX_PREAMBLE = "\n".join(
    (
        r"\usepackage[english]{babel}",
        r"\usepackage[utf8]{inputenc}",
        r"\usepackage[T1]{fontenc}",
        r"\usepackage{amsmath}",
        r"\usepackage{amssymb}",
        r"\usepackage{dsfont}",
        r"\usepackage{setspace}",
        r"\usepackage{tipa}",
        r"\usepackage{relsize}",
        r"\usepackage{textcomp}",
        r"\usepackage{mathrsfs}",
        r"\usepackage{calligra}",
        r"\usepackage{wasysym}",
        r"\usepackage{ragged2e}",
        r"\usepackage{physics}",
        r"\usepackage{xcolor}",
        r"\usepackage{microtype}",
        r"\usepackage{pifont}",
        r"\DisableLigatures{encoding = *, family = * }",
        r"\linespread{1}",
    )
)

#: manimgl default-template packages that must exist for ``Tex``/``Brace``.
#: Maps to TeX Live package names installed via ``tlmgr`` after TinyTeX setup.
MANIM_TEX_PACKAGES: tuple[str, ...] = (
    "standalone",
    "preview",
    "amsmath",
    "amssymb",
    "babel",
    "latex-fonts",
    "doublestroke",
    "setspace",
    "tipa",
    "relsize",
    "textcomp",
    "mathrsfs",
    "calligra",
    "wasysym",
    "ragged2e",
    "physics",
    "xcolor",
    "microtype",
    "pifont",
    "ec",
    "dvisvgm",
)

_ENV_OVERRIDE = "MANIM_EDITOR_LATEX_DIR"


def install_dir(target: str | Path | None = None) -> Path:
    """User-local directory for the on-demand LaTeX distribution."""
    if target is not None:
        return Path(target)
    override = os.environ.get(_ENV_OVERRIDE)
    if override:
        return Path(override)
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        return Path(base) / "ManimEditor" / "latex"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "ManimEditor" / "latex"
    xdg = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    return Path(xdg) / "manim-editor" / "latex"


def bin_dir(target: str | Path | None = None) -> Path:
    """Directory holding the ``latex``/``dvisvgm`` binaries."""
    root = install_dir(target)
    if sys.platform == "win32":
        # TinyTeX portable on Windows keeps binaries under tlpkg/tlgs + bin.
        for candidate in (root / "bin" / "windows", root / "bin" / "win32"):
            if candidate.is_dir():
                return candidate
        return root / "bin" / "windows"
    return root / "bin" / universal_bin_subdir()


def universal_bin_subdir() -> str:
    """TinyTeX binary subfolder for the current platform."""
    if sys.platform == "darwin":
        return "universal-darwin"
    machine = os.uname().machine if hasattr(os, "uname") else "x86_64"
    if machine in ("aarch64", "arm64"):
        return "aarch64-linux"
    return "x86_64-linux"


def _which(name: str, extra_dirs: tuple[str, ...] = ()) -> str | None:
    """Like ``shutil.which`` but also honors Windows ``PATHEXT`` for fake dirs."""
    found = shutil.which(name, path=os.pathsep.join(extra_dirs) if extra_dirs else None)
    if found:
        return found
    if sys.platform == "win32" and extra_dirs:
        for directory in extra_dirs:
            for ext in (".exe", ".bat", ".cmd"):
                candidate = Path(directory) / f"{name}{ext}"
                if candidate.is_file():
                    return str(candidate)
    return None


@dataclass(frozen=True, slots=True)
class LatexStatus:
    """Outcome of :func:`latex_status` — always returned, never raised."""

    ok: bool
    has_latex: bool
    has_dvisvgm: bool
    latex_path: str | None
    dvisvgm_path: str | None
    bundled: bool
    install_dir: str
    missing: tuple[str, ...]
    miktex: bool = False

    def to_dict(self) -> dict[str, object]:
        return {
            "ok": self.ok,
            "has_latex": self.has_latex,
            "has_dvisvgm": self.has_dvisvgm,
            "latex_path": self.latex_path,
            "dvisvgm_path": self.dvisvgm_path,
            "bundled": self.bundled,
            "install_dir": self.install_dir,
            "missing": list(self.missing),
            "miktex": self.miktex,
        }


def is_miktex(path: str | Path | None) -> bool:
    """True when the executable lives in a MiKTeX installation."""
    return path is not None and "miktex" in str(path).lower()


def latex_status(target: str | Path | None = None) -> LatexStatus:
    """Check for usable ``latex`` + ``dvisvgm``, preferring the bundled copy."""
    root = install_dir(target)
    extra = (str(bin_dir(root)),)
    latex_path = _which("latex", extra) or shutil.which("latex")
    dvisvgm_path = _which("dvisvgm", extra) or shutil.which("dvisvgm")
    bundled = (
        bool(
            latex_path
            and dvisvgm_path
            and Path(latex_path).is_relative_to(root)
            and Path(dvisvgm_path).is_relative_to(root)
        )
        if sys.version_info >= (3, 9)
        else False
    )
    missing = tuple(
        name for name, found in (("latex", latex_path), ("dvisvgm", dvisvgm_path)) if not found
    )
    return LatexStatus(
        ok=not missing,
        has_latex=latex_path is not None,
        has_dvisvgm=dvisvgm_path is not None,
        latex_path=latex_path,
        dvisvgm_path=dvisvgm_path,
        bundled=bundled,
        install_dir=str(root),
        missing=missing,
        miktex=is_miktex(latex_path) or is_miktex(dvisvgm_path),
    )


@dataclass(frozen=True, slots=True)
class LatexProbe:
    """Outcome of :func:`probe_compile` — a real end-to-end TeX test."""

    ok: bool
    kind: str  # ok | missing | timeout | latex_error | dvisvgm_error
    summary: str
    detail: str = ""
    latex_path: str | None = None
    dvisvgm_path: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "ok": self.ok,
            "kind": self.kind,
            "summary": self.summary,
            "detail": self.detail,
            "latex_path": self.latex_path,
            "dvisvgm_path": self.dvisvgm_path,
        }


def probe_compile(timeout: float = 300.0) -> LatexProbe:
    """Compile manimgl's default template with the detected toolchain.

    Mirrors what manimgl actually runs (including the
    :mod:`manim_editor.latex_compat` fix), so "diagnose" reports the same
    result the preview/export will get.  Never raises.
    """
    status = latex_status()
    if not status.ok:
        return LatexProbe(
            ok=False,
            kind="missing",
            summary=f"missing: {', '.join(status.missing)}",
            detail=LATEX_SETUP_HINT,
            latex_path=status.latex_path,
            dvisvgm_path=status.dvisvgm_path,
        )
    content = (
        "\\documentclass[preview]{standalone}\n"
        + DEFAULT_TEX_PREAMBLE
        + "\n\\begin{document}\n$x^2$\n\\end{document}\n"
    )
    with tempfile.TemporaryDirectory(prefix="manim-editor-latex-probe-") as tmp:
        tmp_path = Path(tmp)
        tex_path = tmp_path / "working.tex"
        tex_path.write_text(content, encoding="utf-8")
        dvi_path = tmp_path / "working.dvi"
        try:
            process = subprocess.run(
                [
                    status.latex_path or "latex",
                    "-interaction=batchmode",
                    "-halt-on-error",
                    f"-output-directory={tmp}",
                    str(tex_path),
                ],
                capture_output=True,
                text=True,
                stdin=subprocess.DEVNULL,
                timeout=timeout,
                env=runtime_env(),
            )
        except subprocess.TimeoutExpired:
            return LatexProbe(
                ok=False,
                kind="timeout",
                summary=f"latex did not finish within {timeout:.0f}s",
                detail=(
                    "The TeX distribution may be waiting for input (e.g. a package "
                    "installation prompt) or generating fonts for the first time. "
                    "Retry once; if it keeps hanging, install TinyTeX via "
                    "Tools > LaTeX setup."
                ),
                latex_path=status.latex_path,
                dvisvgm_path=status.dvisvgm_path,
            )
        except OSError as exc:
            return LatexProbe(
                ok=False,
                kind="missing",
                summary=f"cannot run {status.latex_path}: {exc}",
                detail=LATEX_SETUP_HINT,
                latex_path=status.latex_path,
                dvisvgm_path=status.dvisvgm_path,
            )
        stderr = (process.stderr or "").strip()
        if process.returncode != 0:
            log_text = _read_text(tmp_path / "working.log")
            errors = "\n".join(line for line in log_text.splitlines() if line.startswith("!"))
            detail = "\n".join(part for part in (stderr, errors) if part).strip()
            if "security risk: running with elevated privileges" in stderr:
                detail = (
                    f"{detail}\n\nNote: MiKTeX reports elevated privileges. "
                    "Prefer running the editor without Administrator rights."
                ).strip()
            if not detail:
                detail = "latex exited with a failure but produced no error text."
            return LatexProbe(
                ok=False,
                kind="latex_error",
                summary="latex compilation failed",
                detail=detail[-4000:],
                latex_path=status.latex_path,
                dvisvgm_path=status.dvisvgm_path,
            )
        if not dvi_path.is_file():
            return LatexProbe(
                ok=False,
                kind="latex_error",
                summary="latex reported success but produced no .dvi",
                detail=f"expected {dvi_path}",
                latex_path=status.latex_path,
                dvisvgm_path=status.dvisvgm_path,
            )
        try:
            svg = subprocess.run(
                [status.dvisvgm_path or "dvisvgm", str(dvi_path), "-n", "-v", "0", "--stdout"],
                capture_output=True,
                text=True,
                stdin=subprocess.DEVNULL,
                timeout=timeout,
                env=runtime_env(),
            )
        except (subprocess.TimeoutExpired, OSError) as exc:
            return LatexProbe(
                ok=False,
                kind="dvisvgm_error",
                summary=f"dvisvgm failed: {exc}",
                detail=LATEX_SETUP_HINT,
                latex_path=status.latex_path,
                dvisvgm_path=status.dvisvgm_path,
            )
        if svg.returncode != 0 or "<svg" not in (svg.stdout or ""):
            return LatexProbe(
                ok=False,
                kind="dvisvgm_error",
                summary="dvisvgm did not produce SVG output",
                detail=(svg.stderr or svg.stdout or "").strip()[-4000:],
                latex_path=status.latex_path,
                dvisvgm_path=status.dvisvgm_path,
            )
    return LatexProbe(
        ok=True,
        kind="ok",
        summary="latex + dvisvgm verified",
        detail="",
        latex_path=status.latex_path,
        dvisvgm_path=status.dvisvgm_path,
    )


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def runtime_env(base: dict[str, str] | None = None) -> dict[str, str]:
    """Environment with the bundled LaTeX ``bin/`` prepended to ``PATH``."""
    import copy

    env = copy.deepcopy(base) if base is not None else dict(os.environ)
    directory = str(bin_dir())
    path = env.get("PATH", "")
    if directory and directory not in path.split(os.pathsep):
        env["PATH"] = directory + os.pathsep + path if path else directory
    return env


def apply_runtime_path() -> bool:
    """Prepend the bundled LaTeX ``bin/`` to this process's ``PATH``.

    Idempotent; returns True when a bundled install directory exists and was
    applied.  Safe to call on every CLI/preview/export entry point.
    """
    directory = bin_dir()
    if not directory.is_dir():
        return False
    path = os.environ.get("PATH", "")
    if str(directory) in path.split(os.pathsep):
        return True
    os.environ["PATH"] = str(directory) + os.pathsep + path if path else str(directory)
    return True


def is_latex_error(exc: BaseException) -> bool:
    """True when ``exc`` is (or wraps) manimgl's ``LatexError``.

    Matches by class/module name so callers never import manimlib at module
    scope (per ``AGENTS.md`` hard dependency rules).
    """
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if type(current).__name__ == "LatexError":
            return True
        cause = current.__cause__ or current.__context__
        current = cause if isinstance(cause, BaseException) else None
    return False


def latex_hint_for(exc: BaseException) -> str | None:
    """Actionable hint when ``exc`` is a LaTeX failure, else None.

    Distinguishes "not installed" from "installed but compile failed" so the
    editor never claims LaTeX is missing when only the compile failed.
    """
    if not is_latex_error(exc):
        return None
    try:
        status = latex_status()
    except Exception:  # never mask the original error with a PATH probe failure
        return LATEX_SETUP_HINT
    return LATEX_SETUP_HINT if not status.ok else LATEX_COMPILE_HINT
