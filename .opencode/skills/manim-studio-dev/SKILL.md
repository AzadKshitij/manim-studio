---
name: manim-studio-dev
description: Use when developing, extending, testing or debugging the Manim Studio codebase itself (src/manim_studio). Covers architecture rules, module boundaries, the verify loop, protocol changes and the commit protocol.
---

# Developing Manim Studio

Read `AGENTS.md` first — it is the contract. This skill is the practical workflow.

## Verify loop (run after every change)

```powershell
uv run ruff format src tests
uv run ruff check src tests
uv run mypy
uv run pytest -m "not gl and not slow"     # fast inner loop
uv run pytest                              # full suite including GL + slow
```

Never commit red. A feature without a test is not done.

## Module boundaries (hard rules)

- `timeline_model`, `tracer`, `ipc`, `templates`, `api_index`: stdlib only.
- `editor/*`: PySide6 only; **never** import `manimlib`/`moderngl`. All GL lives in
  `preview_host` (one context, one thread).
- `scene_runner`, `export`: `manimlib` imports are function-local so `sys.argv` is
  sanitized first (`manimlib_argv_guard`) and the CLI stays fast.
- Pure logic that involves time/IO must take an injectable clock or stream so it can
  be unit tested without Qt or a GPU (see `DebounceGate`, `TickThrottler`).

## Adding an editor feature

1. Put the decision logic in a pure module/class first and unit test it.
2. Add the Qt surface (widget/signal) and an offscreen smoke test in
   `tests/test_editor_smoke.py` or a dedicated file. Qt tests run with
   `QT_QPA_PLATFORM=offscreen` via `tests/conftest.py`.
3. If the feature needs GL or a real host process, add a `@pytest.mark.gl` test that
   spawns `manim_studio.preview_host` (see `tests/test_preview_host.py` for the
   peer harness) or `PreviewController` (see `tests/test_preview_controller.py`).
4. Extend `tests/scenes/feature_showcase.py` if a new scene API feature is involved;
   its trace is asserted in `tests/test_scene_runner.py`.

## Changing the protocol

1. Edit `src/manim_studio/ipc.py`; bump `PROTOCOL_VERSION` for breaking changes.
2. Update both sides and `docs/protocol.md`.
3. Extend `tests/test_ipc.py` (codec) and the host/controller integration tests.

## Qt pitfalls in this repo

- `QProcess` must never be destroyed while its child is running (fatal abort). Use
  `PreviewController.shutdown()` semantics: flush socket writes, `terminate()`, wait,
  then `kill()`.
- Do not block the GUI thread on rendering; hosts stream frames and the editor drops
  stale ones.
- File watcher events always become queued signals; never call scene APIs from the
  watcher callback.

## Commit protocol

Small commits, conventional messages (`feat(scope):`, `fix(scope):`, `test:` ...).
Before committing: run the verify loop, `git diff --staged` self-review for dead code,
debug prints and missing tests, and confirm no generated media is staged.
