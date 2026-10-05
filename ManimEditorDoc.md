# Manim Editor

We're building a production-grade, simple and straight-forward Manim Editor for Windows, macOS and Linux (Ubuntu 24+). Left: code editor. Right: realtime preview. Bottom: timeline scrub. This is professional-grade software that needs to be stable, fully-tested, and real-world deployable. No hacks, no shortcuts - solid as a rock.

## Dev Workflow
- Ask questions when in doubt.
- Testing is of the utmost importance - use lots of unit tests (pytest, simple is good), run automated testing
- Setup a scene used for testing, which tests every single feature in the editor - editor, preview, timeline, and entire scene API
- Do things properly, this is production-grade and not a hack project
- Create AGENTS.md and relevant skills to support development, and contain concrete development guidelines
- Code style: ruff format + ruff check, typed Python 3.13, PySide6 signals/slots, full repo follows ManimGL conventions where applicable
- Create git repository, commit to local git repo as and when necessary. IMPORTANT: do a code review before committing, and make sure all changes comply with code style, production-grade quality standards, and have been properly tested/have unit tests that have passed where necessary
- Use uv to create the environment.

## Tech stack
- Python 3.13 and uv/pip for core app
- [PySide6==6.11.2](https://pypi.org/project/PySide6/) for editor shell (`QSplitter`, `QFileSystemWatcher`, `QtMultimedia.QMediaPlayer`), LGPL
- [pyside6-scintilla==5.6.3.7](https://pypi.org/project/pyside6-scintilla/) for code editor (MIT, Scintilla 5.6.3).
- [manimgl==1.7.2](https://pypi.org/project/manimgl/) for realtime scenes (3b1b/manim, not Manim CE)
- [moderngl==5.12.0](https://pypi.org/project/moderngl/) + [moderngl-window==3.1.1](https://pypi.org/project/moderngl-window/) for GPU preview (`pyglet`/`glfw` backend - no Qt6 backend exists)
- [IPython>=8.18](https://ipython.org/) for embedded live shell (`embed()`, `reload()`, `checkpoint_paste()`)
- ffmpeg for export, LaTeX optional for Tex

## Basic Architecture
- Python package for core (`tracer`, `preview-host`, `timeline-model`), executable entry for editor app
- Need to have editor to build videos - Stage 1: two windows (Qt editor + external GL window side-by-side). Stage 2: single window via FBO readback (`fbo.read()` -> `QImage` -> `QLabel`), stripped export path uses subprocess `manimgl -w`
- Simple scene model: `log.json` list of `{name, start, duration, index}` traced from `play()`/`wait()`, used to author timeline blocks
- Ability to "export" video - headless `manimgl -w --low_quality` / 1080p60 render without editing ability that we can distribute
- Editor needs to be fully controllable by AI agents - I should be able to ask you to build me a scene like CircleToSquare, and you should have all the tools available to do so without my intervention

## Live Preview
- Persistent OpenGL context, `scene.update_frame(dt)` at 60fps, no ffmpeg encode per edit
- Editor writes file -> `reload()` reuses window (<1s vs 4-5s cold start), `checkpoint_paste()` for `# comment` checkpoints
- Flags: `-e/--embed`, `--autoreload`, `-se LINE`, `-n NUM` for timeline jump, `-s` final frame only
- `QTimer(16ms)` drives embedded viewport in Stage 2, all GL on GUI thread, Tex compile in `QThread`
- One GL context, one thread. Never `Scene.play()` from watcher callback - queue via signal/slot

## Timeline
- Runtime trace of `play(run_time)` / `wait(duration)`, not static AST (handles loops/ifs)
- v1 read-only strip: custom `QWidget.paintEvent`, `x = start/total*width`, `QSlider` playhead, scrub low-q mp4 via `QMediaPlayer.setPosition()`
- v2 interactive: wheel zoom + `QScrollBar`, drag playhead, click block -> `manimgl -n <index>` live jump
- v3 live scrub deferred: `save_state()` per boundary + `anim.interpolate(alpha)` - breaks with `always_redraw`/updaters
- `QTimer(33ms)`: `player.position()` -> `timeline.setPlayhead()`. Drag pauses live loop

## Editor
- `ScintillaEdit` with Python lexer, line numbers, block selection, `QCompleter` for `manimlib` API
- `QFileSystemWatcher` + 500ms debounce on save, IPC via socket/stdin to live GL proc
- Native IME, accessibility, drag-drop, find/replace free via Qt - do not reimplement

## Behavior
- Scripting with Python (`manimlib` Scene API)
- Control mobjects and run in `construct()` loop, `play`/`add`/`remove`/`wait`/`save_state`/`undo`
- Spawn new mobjects/prefabs via editor templates
