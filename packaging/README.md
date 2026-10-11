# packaging/

Windows distribution for Manim Studio (see ADR 0005). Everything here is build
input; everything produced lands in `dist/` at the repo root.

| file | purpose |
| --- | --- |
| `manim_studio.spec` | PyInstaller build: one onedir bundle, two exes (`ManimStudio.exe` windowed GUI, `manim-studio.exe` console CLI/worker). |
| `entrypoints/gui_entry.py` | Windowed entry (`frozen_entry.gui_entry`). |
| `entrypoints/cli_entry.py` | Console entry (`frozen_entry.cli_entry`). |
| `installer.iss` | Inno Setup 6 script (per-user install, no admin). |
| `make_icon.py` | Procedural icon generator (`uv run python packaging/make_icon.py`). Also writes the background-less `docs/assets/logo-transparent.{png,ico,svg}`. |
| `manim_studio.ico` | Generated icon (committed so the installer builds offline). The generator also writes the runtime copy at `src/manim_studio/assets/manim_studio.ico`. |

Build with `scripts\build.ps1` from the repo root — never by invoking PyInstaller
or ISCC by hand (the script pins `--distpath dist --workpath build`, the version
define, and the smoke tests).
