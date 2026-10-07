"""Tests for on-demand LaTeX detection, install, compatibility and error hints."""

from __future__ import annotations

import os
import platform
import stat
import sys
import tarfile
import zipfile
from pathlib import Path

import pytest

from manim_studio import latex_install
from manim_studio.latex import (
    LATEX_COMPILE_HINT,
    LATEX_SETUP_HINT,
    apply_runtime_path,
    bin_dir,
    install_dir,
    is_latex_error,
    is_miktex,
    latex_hint_for,
    latex_status,
    probe_compile,
    runtime_env,
)
from manim_studio.latex_compat import strip_unsupported_no_pdf
from manim_studio.scene_runner import format_scene_error


def _make_bin(directory: Path) -> Path:
    tools = directory / "bin" / "x86_64-linux"
    tools.mkdir(parents=True, exist_ok=True)
    for name in ("latex", "dvisvgm", "tlmgr"):
        script = tools / name
        script.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        script.chmod(script.stat().st_mode | stat.S_IEXEC)
    return directory


_LATEX_IMPL = """\
import os
import sys
from pathlib import Path

out = None
for arg in sys.argv[1:]:
    if arg.startswith("-output-directory="):
        out = Path(arg.split("=", 1)[1])
if out is None:
    sys.exit(3)
out.mkdir(parents=True, exist_ok=True)
if os.environ.get("FAKE_LATEX_EXIT", "0") != "0":
    errors = os.environ.get("FAKE_LATEX_LOG", "")
    if errors:
        (out / "working.log").write_text(errors, encoding="utf-8")
    sys.stderr.write(os.environ.get("FAKE_LATEX_STDERR", ""))
    sys.exit(1)
if os.environ.get("FAKE_LATEX_DVI", "1") == "1":
    (out / "working.dvi").write_bytes(b"FAKE-DVI")
sys.exit(0)
"""

_DVISVGM_IMPL = """\
import os
import sys

rc = int(os.environ.get("FAKE_DVISVGM_EXIT", "0"))
if rc == 0:
    sys.stdout.write("<svg xmlns='http://www.w3.org/2000/svg'></svg>")
else:
    sys.stderr.write("dvisvgm exploded")
sys.exit(rc)
"""


def _install_fake_tools(root: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Create fake latex/dvisvgm executables in the platform bin dir."""
    directory = bin_dir(root)
    directory.mkdir(parents=True, exist_ok=True)
    for name, body in (("latex", _LATEX_IMPL), ("dvisvgm", _DVISVGM_IMPL)):
        impl = directory / f"{name}_impl.py"
        impl.write_text(body, encoding="utf-8")
        if sys.platform == "win32":
            launcher = directory / f"{name}.bat"
            launcher.write_text(
                f'@echo off\r\n"{sys.executable}" "%~dp0{name}_impl.py" %*\r\n',
                encoding="utf-8",
            )
        else:
            launcher = directory / name
            launcher.write_text(
                f'#!/bin/sh\nexec "{sys.executable}" "$(dirname "$0")/{name}_impl.py" "$@"\n',
                encoding="utf-8",
            )
            launcher.chmod(launcher.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("MANIM_STUDIO_LATEX_DIR", str(root))
    return root


def test_install_dir_env_override(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MANIM_STUDIO_LATEX_DIR", str(tmp_path / "custom"))
    assert install_dir() == tmp_path / "custom"


def test_status_missing_in_empty_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PATH", str(tmp_path))
    status = latex_status(tmp_path / "empty")
    assert not status.ok
    assert set(status.missing) == {"latex", "dvisvgm"}


def test_apply_runtime_path_idempotent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = _make_bin(tmp_path / "latex")
    import manim_studio.latex as latex_module

    monkeypatch.setenv("MANIM_STUDIO_LATEX_DIR", str(root))
    monkeypatch.setenv("PATH", os.pathsep.join(("",)))
    # bin_dir() resolves the platform subdir; point PATH via runtime_env instead.
    env = runtime_env()
    assert str(latex_module.bin_dir()) in env["PATH"]
    assert apply_runtime_path() in (True, False)
    first = os.environ.get("PATH", "")
    apply_runtime_path()
    assert os.environ.get("PATH", "") == first


def test_install_from_local_archive(tmp_path: Path) -> None:
    # Build a TinyTeX-shaped zip: top-level dir containing bin/ + tlmgr stub.
    staged = tmp_path / "staged" / "TinyTeX"
    bin_dir = staged / "bin" / "x86_64-linux"
    bin_dir.mkdir(parents=True, exist_ok=True)
    (staged / "tlpkg").mkdir(parents=True, exist_ok=True)
    (bin_dir / "tlmgr").write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    archive = tmp_path / "tinytex.zip"
    with zipfile.ZipFile(archive, "w") as bundle:
        for path in staged.rglob("*"):
            bundle.write(path, path.relative_to(tmp_path / "staged"))

    target = tmp_path / "install"

    def fetcher(url: str, dest: Path, on_progress: object = None, cancel: object = None) -> None:
        dest.write_bytes(archive.read_bytes())

    root = latex_install.install_latex(
        target,
        fetcher=fetcher,
        packages=(),
        skip_tlmgr=True,  # type: ignore[arg-type]
        kind="archive",
    )
    assert (root / "bin").is_dir()
    assert latex_install.is_installed(target)
    assert latex_install.uninstall_latex(target)
    assert not target.exists()


def test_install_cancelled(tmp_path: Path) -> None:
    with pytest.raises(latex_install.InstallError, match="cancelled"):
        latex_install.install_latex(
            tmp_path / "install",
            fetcher=lambda url, dest, on_progress=None, cancel=None: None,  # type: ignore[arg-type]
            cancel=lambda: True,
            packages=(),
            skip_tlmgr=True,
        )


def test_tinytex_sources_are_pinned_and_valid() -> None:
    for key, source in latex_install.TINYTEX_SOURCES.items():
        assert source.url.startswith("https://github.com/rstudio/tinytex-releases/")
        assert source.url.endswith(source.filename), key
        assert len(source.sha256) == 64, key
        assert int(source.sha256, 16) >= 0, key
        assert source.kind in ("sfx-exe", "archive"), key
        assert latex_install.TINYTEX_VERSION in source.filename, key
    assert latex_install.TINYTEX_SOURCES["win32"].kind == "sfx-exe"


def test_tinytex_source_platform_selection(monkeypatch: pytest.MonkeyPatch) -> None:
    import sys as _sys

    monkeypatch.setattr(_sys, "platform", "win32")
    assert latex_install.tinytex_source().filename.endswith(".exe")
    monkeypatch.setattr(_sys, "platform", "darwin")
    assert "darwin" in latex_install.tinytex_source().filename
    monkeypatch.setattr(_sys, "platform", "linux")
    monkeypatch.setattr(platform, "machine", lambda: "x86_64")
    monkeypatch.setattr(platform, "libc_ver", lambda: ("glibc", "2.39"))
    assert "linux-x86_64" in latex_install.tinytex_source().filename
    monkeypatch.setattr(platform, "machine", lambda: "arm64")
    assert "linux-arm64" in latex_install.tinytex_source().filename
    monkeypatch.setattr(platform, "machine", lambda: "x86_64")
    monkeypatch.setattr(platform, "libc_ver", lambda: ("musl", "1.2"))
    assert "linuxmusl" in latex_install.tinytex_source().filename


def test_install_from_local_tar_xz(tmp_path: Path) -> None:
    staged = tmp_path / "staged" / "TinyTeX"
    tools = staged / "bin" / "x86_64-linux"
    tools.mkdir(parents=True, exist_ok=True)
    (staged / "tlpkg").mkdir(parents=True, exist_ok=True)
    archive = tmp_path / "tinytex.tar.xz"
    with tarfile.open(archive, "w:xz") as bundle:
        bundle.add(staged, arcname="TinyTeX")

    target = tmp_path / "install"

    def fetcher(url: str, dest: Path, on_progress: object = None, cancel: object = None) -> None:
        dest.write_bytes(archive.read_bytes())

    root = latex_install.install_latex(
        target,
        fetcher=fetcher,
        packages=(),
        skip_tlmgr=True,  # type: ignore[arg-type]
        kind="archive",
    )
    assert (root / "bin" / "x86_64-linux").is_dir()
    assert latex_install.is_installed(target)


def test_verify_sha256_rejects_corrupt(tmp_path: Path) -> None:
    payload = tmp_path / "bundle.bin"
    payload.write_bytes(b"not-the-release")
    with pytest.raises(latex_install.InstallError, match="checksum"):
        latex_install.verify_sha256(payload, "0" * 64)


def test_missing_from_info_parsing() -> None:
    text = "package:     standalone\ninstalled:   No\npackage:     preview\ninstalled:   Yes\n"
    assert latex_install._missing_from_info(text, ("standalone", "preview")) == ("standalone",)
    assert latex_install._missing_from_info(text, ("preview",)) == ()
    assert latex_install._missing_from_info("", ("standalone",)) == ("standalone",)


def test_tlmgr_install_self_updates_installs_and_verifies(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import subprocess as _subprocess

    calls: list[tuple[str, ...]] = []

    class _Result:
        returncode = 0
        stdout = "package:     standalone\ninstalled:   Yes\n"
        stderr = ""

    def fake_run(argv: object, **kwargs: object) -> _Result:
        calls.append(tuple(argv))  # type: ignore[arg-type]
        return _Result()

    monkeypatch.setattr(_subprocess, "run", fake_run)
    root = tmp_path / "latex"
    tlmgr = root / "bin" / "windows" / "tlmgr.bat"
    tlmgr.parent.mkdir(parents=True)
    tlmgr.write_text("@echo off\n", encoding="utf-8")
    latex_install._tlmgr_install(root, ("standalone",))
    kinds = [call[1:] for call in calls]
    assert kinds[0][:2] == ("update", "--self")
    assert kinds[1][:2] == ("install", "standalone")
    assert kinds[2][0] == "info"


def test_tlmgr_verify_fails_when_package_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import subprocess as _subprocess

    class _Result:
        returncode = 0
        stdout = "package:     standalone\ninstalled:   No\n"
        stderr = ""

    monkeypatch.setattr(_subprocess, "run", lambda argv, **kwargs: _Result())
    root = tmp_path / "latex"
    tlmgr = root / "bin" / "windows" / "tlmgr.bat"
    tlmgr.parent.mkdir(parents=True)
    tlmgr.write_text("@echo off\n", encoding="utf-8")
    with pytest.raises(latex_install.InstallError, match="still missing: standalone"):
        latex_install._tlmgr_install(root, ("standalone",))


def test_download_retries_then_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import urllib.error

    calls = {"count": 0}

    def boom(request: object, timeout: float = 0) -> object:
        calls["count"] += 1
        raise urllib.error.URLError("flaky network")

    monkeypatch.setattr(latex_install.urllib.request, "urlopen", boom)
    monkeypatch.setattr(latex_install.time, "sleep", lambda seconds: None)
    with pytest.raises(latex_install.InstallError, match="download failed"):
        latex_install.download_file("https://example.invalid/bundle", tmp_path / "out")
    assert calls["count"] == latex_install._DOWNLOAD_ATTEMPTS


def test_latex_error_detection() -> None:
    class LatexError(Exception):
        pass

    exc = LatexError("LaTeX compilation failed")
    assert is_latex_error(exc)
    assert latex_hint_for(exc) is not None
    assert latex_hint_for(ValueError("nope")) is None


def test_format_scene_error_appends_latex_hint(tmp_path: Path) -> None:
    class LatexError(Exception):
        pass

    scene = tmp_path / "scene.py"
    scene.write_text("x = 1\n", encoding="utf-8")
    try:
        raise LatexError("LaTeX compilation failed")
    except LatexError as exc:
        info = format_scene_error(exc, scene)
    assert "LaTeX" in info.message
    assert "LaTeX setup" in info.message


def test_cli_latex_status_json(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from manim_studio.cli import main

    monkeypatch.setenv("MANIM_STUDIO_LATEX_DIR", str(tmp_path / "missing"))
    assert main(["latex", "status"]) in (0, 1)


def test_setup_dialog_smoke(qtbot: object, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from manim_studio.editor.latex_dialog import LatexSetupDialog

    monkeypatch.setenv("PATH", str(tmp_path))
    monkeypatch.setenv("MANIM_STUDIO_LATEX_DIR", str(tmp_path / "empty"))
    dialog = LatexSetupDialog()
    dialog.refresh()
    assert "missing" in dialog._status_label.text().lower()
    _install_fake_tools(tmp_path / "tools", monkeypatch)
    dialog.refresh()
    assert "ready" in dialog._status_label.text().lower()
    dialog.close()


# ---------------------------------------------------------------------------
# manimgl 1.7.2 compatibility (MiKTeX rejects '-no-pdf')
# ---------------------------------------------------------------------------


def test_strip_unsupported_no_pdf() -> None:
    assert strip_unsupported_no_pdf(["latex", "-no-pdf", "-interaction=batchmode", "x.tex"]) == [
        "latex",
        "-interaction=batchmode",
        "x.tex",
    ]
    assert strip_unsupported_no_pdf(["xelatex", "-no-pdf", "x.tex"]) == [
        "xelatex",
        "-no-pdf",
        "x.tex",
    ]
    assert strip_unsupported_no_pdf(
        [r"C:\Program Files\MiKTeX\miktex\bin\x64\latex.exe", "-no-pdf", "x.tex"]
    ) == [r"C:\Program Files\MiKTeX\miktex\bin\x64\latex.exe", "x.tex"]
    assert strip_unsupported_no_pdf(["latex", "x.tex"]) == ["latex", "x.tex"]
    assert strip_unsupported_no_pdf([]) == []


def test_is_miktex() -> None:
    assert is_miktex(r"C:\Program Files\MiKTeX\miktex\bin\x64\latex.exe")
    assert not is_miktex("/usr/bin/latex")
    assert not is_miktex(None)


def test_patch_manimgl_latex_compat_idempotent() -> None:
    pytest.importorskip("manimlib.utils.tex_file_writing")
    from manim_studio.latex_compat import patch_manimgl_latex_compat

    patch_manimgl_latex_compat()
    assert patch_manimgl_latex_compat() is False


def test_hint_distinguishes_missing_from_failed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class LatexError(Exception):
        pass

    exc = LatexError("LaTeX compilation failed")
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    monkeypatch.setenv("MANIM_STUDIO_LATEX_DIR", str(tmp_path / "empty"))
    assert latex_hint_for(exc) == LATEX_SETUP_HINT
    _install_fake_tools(tmp_path / "tools", monkeypatch)
    assert latex_hint_for(exc) == LATEX_COMPILE_HINT


# ---------------------------------------------------------------------------
# End-to-end probe (fake toolchain, no network, no real TeX)
# ---------------------------------------------------------------------------


def test_probe_missing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PATH", str(tmp_path))
    monkeypatch.setenv("MANIM_STUDIO_LATEX_DIR", str(tmp_path / "empty"))
    probe = probe_compile(timeout=20)
    assert not probe.ok
    assert probe.kind == "missing"


def test_probe_ok(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fake_tools(tmp_path / "tools", monkeypatch)
    probe = probe_compile(timeout=30)
    assert probe.ok
    assert probe.kind == "ok"
    assert probe.summary == "latex + dvisvgm verified"


def test_probe_reports_latex_log_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fake_tools(tmp_path / "tools", monkeypatch)
    monkeypatch.setenv("FAKE_LATEX_EXIT", "1")
    monkeypatch.setenv("FAKE_LATEX_LOG", "! Undefined control sequence.\nl.5 \\foo\n")
    probe = probe_compile(timeout=30)
    assert not probe.ok
    assert probe.kind == "latex_error"
    assert "Undefined control sequence" in probe.detail


def test_probe_notes_elevated_privileges(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "miktex" / "tools"
    _install_fake_tools(root, monkeypatch)
    monkeypatch.setenv("FAKE_LATEX_EXIT", "1")
    monkeypatch.setenv(
        "FAKE_LATEX_STDERR", "latex: security risk: running with elevated privileges"
    )
    probe = probe_compile(timeout=30)
    assert not probe.ok
    assert "elevated" in probe.detail


def test_probe_dvisvgm_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fake_tools(tmp_path / "tools", monkeypatch)
    monkeypatch.setenv("FAKE_DVISVGM_EXIT", "2")
    probe = probe_compile(timeout=30)
    assert not probe.ok
    assert probe.kind == "dvisvgm_error"
