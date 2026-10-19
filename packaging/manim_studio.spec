# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for Manim Studio — see ADR 0005.

Builds one onedir bundle that backs both distribution channels (portable zip and
Inno Setup installer)::

    dist/ManimStudio/
        ManimStudio.exe     windowed GUI (double-click target)
        manim-studio.exe    console CLI, and the worker exe the frozen parent
                            re-invokes for preview-host / export children
        _internal/          Python runtime, PySide6, manimlib (+ its data files
                            and sources), dist-info metadata, native libraries

Two analyses share the build: the console entry and the windowed entry have
different ``__main__`` modules, so each EXE gets its own scripts/PKG while the
(collected) binaries and data are merged once.

Run from the repository root (``scripts/build.ps1`` does this plus the gate and
smoke tests)::

    uv run pyinstaller --noconfirm --clean packaging/manim_studio.spec
"""

from __future__ import annotations

import re
import sysconfig
import textwrap
import tomllib
from pathlib import Path

from PyInstaller.utils.hooks import collect_all, collect_data_files, collect_submodules

SPEC_DIR = Path(SPECPATH)
PROJECT_ROOT = SPEC_DIR.parent
SRC_DIR = PROJECT_ROOT / "src"
ICON_FILE = SPEC_DIR / "manim_studio.ico"
ENTRY_GUI = SPEC_DIR / "entrypoints" / "gui_entry.py"
ENTRY_CLI = SPEC_DIR / "entrypoints" / "cli_entry.py"

with (PROJECT_ROOT / "pyproject.toml").open("rb") as _handle:
    APP_VERSION = str(tomllib.load(_handle)["project"]["version"])


def _version_resource() -> str | None:
    """Write and return a Windows version-resource file for both executables."""
    match = re.match(r"(\d+)\.(\d+)\.(\d+)", APP_VERSION)
    if match is None:
        return None
    parts = (int(match.group(1)), int(match.group(2)), int(match.group(3)), 0)
    path = PROJECT_ROOT / "build" / "pyinstaller" / "file_version_info.txt"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        textwrap.dedent(f"""\
            VSVersionInfo(
              ffi=FixedFileInfo(
                filevers={parts}, prodvers={parts},
                mask=63, flags=0, OS=0x40004, fileType=1, subtype=0, date=(0, 0)
              ),
              kids=[
                StringFileInfo([
                  StringTable(
                    '040904B0',
                    [
                      StringStruct('CompanyName', 'Manim Studio Team'),
                      StringStruct('FileDescription', 'Manim Studio'),
                      StringStruct('FileVersion', '{APP_VERSION}'),
                      StringStruct('InternalName', 'manim-studio'),
                      StringStruct('LegalCopyright',
                                   'Copyright (C) 2026 Manim Studio Team. MIT License.'),
                      StringStruct('OriginalFilename', 'ManimStudio.exe'),
                      StringStruct('ProductName', 'Manim Studio'),
                      StringStruct('ProductVersion', '{APP_VERSION}')
                    ]
                  )
                ]),
                VarFileInfo([VarStruct('Translation', [1033, 1200])])
              ]
            )
            """),
        encoding="utf-8",
    )
    return str(path)


def _data_files() -> list[tuple[str, str]]:
    """Data files that module analysis can never discover on its own."""
    datas: list[tuple[str, str]] = []

    # manimlib: runtime data (default_config.yml, shaders, tex templates) resolved
    # through get_manim_dir() == dirname(manimlib.__file__), plus the .py sources
    # that api_index AST-scans in frozen builds (find_manimlib_path).  Located via
    # find_spec so the spec never imports manimlib (its __init__ pulls in GL).
    import importlib.util

    spec = importlib.util.find_spec("manimlib")
    assert spec is not None and spec.submodule_search_locations, "manimlib not installed"
    manimlib_root = Path(next(iter(spec.submodule_search_locations))).resolve()
    keep_suffixes = {".py", ".yml", ".yaml", ".glsl", ".json", ".txt", ".md"}
    for path in manimlib_root.rglob("*"):
        if not path.is_file() or "__pycache__" in path.parts:
            continue
        if path.suffix.lower() not in keep_suffixes:
            continue
        relative_dir = path.parent.relative_to(manimlib_root)
        datas.append((str(path), str(Path("manimlib") / relative_dir)))

    # moderngl-window ships shaders/fonts it loads at runtime.
    datas.extend(collect_data_files("moderngl_window"))

    # The window/taskbar icon the editor loads via manim_studio.editor.icon.
    for asset in sorted((SRC_DIR / "manim_studio" / "assets").glob("*")):
        if asset.is_file():
            datas.append((str(asset), "manim_studio/assets"))

    # Every installed *.dist-info: manimlib/__init__ calls
    # pkg_resources.get_distribution("manimgl") at import time, and that lookup
    # scans *.dist-info directories on sys.path — which in a frozen process is
    # the _internal contents directory where these land.
    site_packages = Path(sysconfig.get_paths()["purelib"])
    for dist_info in sorted(site_packages.glob("*.dist-info")):
        for path in sorted(dist_info.rglob("*")):
            if path.is_file():
                datas.append((str(path), dist_info.name))
    return datas


def _extras() -> tuple[list[str], list[tuple[str, str]], list[tuple[str, str]]]:
    """Hidden imports and native libraries that static analysis cannot see."""
    hiddenimports = [
        *collect_submodules("pyglet"),  # selects GL/media backends at runtime
        *collect_submodules("IPython"),  # live shell
        *collect_submodules("jedi"),  # shell completions via entry points
        *collect_submodules("parso"),
        *collect_submodules("manimlib"),  # defensive: scene API surface
        *collect_submodules("mcp"),  # agent surface: `manim-studio mcp` (ADR 0013)
        *collect_submodules("mcp_types"),
    ]
    binaries: list[tuple[str, str]] = []
    datas: list[tuple[str, str]] = []
    for package in ("pyside6_scintilla", "manimpango"):  # .pyd/.dll inside packages
        package_datas, package_binaries, package_hidden = collect_all(package)
        datas.extend(package_datas)
        binaries.extend(package_binaries)
        hiddenimports.extend(package_hidden)
    return hiddenimports, binaries, datas


def _merge(*tocs: list[tuple[str, str, str]]) -> list[tuple[str, str, str]]:
    """De-duplicate two analyses' TOCs by (destination, typecode).

    Both analyses run against the same environment, so identical destinations
    resolve to identical sources; keeping the first is enough.
    """
    merged: dict[tuple[str, str], tuple[str, str, str]] = {}
    for toc in tocs:
        for entry in toc:
            merged[(entry[0], entry[2])] = entry
    return list(merged.values())


_hiddenimports, _binaries, _extra_datas = _extras()
_datas = _data_files() + _extra_datas
_icon = str(ICON_FILE) if ICON_FILE.is_file() else None
_version = _version_resource()

_analysis_options = {
    "pathex": [str(SRC_DIR)],
    "hookspath": [],
    "hooksconfig": {},
    "runtime_hooks": [],
    "excludes": [],
    "noarchive": False,
}

a_gui = Analysis(
    [str(ENTRY_GUI)],
    binaries=list(_binaries),
    datas=list(_datas),
    hiddenimports=list(_hiddenimports),
    **_analysis_options,
)
a_cli = Analysis(
    [str(ENTRY_CLI)],
    binaries=list(_binaries),
    datas=list(_datas),
    hiddenimports=list(_hiddenimports),
    **_analysis_options,
)

pyz_gui = PYZ(a_gui.pure)
pyz_cli = PYZ(a_cli.pure)

gui_exe = EXE(
    pyz_gui,
    a_gui.scripts,
    exclude_binaries=True,
    name="ManimStudio",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,  # windowed: no console on double-click, no flash for children
    disable_windowed_traceback=False,
    icon=_icon,
    version=_version,
)
cli_exe = EXE(
    pyz_cli,
    a_cli.scripts,
    exclude_binaries=True,
    name="manim-studio",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,  # full CLI for terminals and AI agents
    disable_windowed_traceback=False,
    icon=_icon,
    version=_version,
)

coll = COLLECT(
    gui_exe,
    cli_exe,
    _merge(a_gui.binaries, a_cli.binaries),
    _merge(a_gui.datas, a_cli.datas),
    strip=False,
    upx=False,
    name="ManimStudio",
)
