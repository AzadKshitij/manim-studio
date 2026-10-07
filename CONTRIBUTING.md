# Contributing to Manim Studio

Thanks for helping improve Manim Studio — a realtime desktop editor for
[ManimGL](https://github.com/3b1b/manim) scenes.

## Ground rules

The repository contract is [`AGENTS.md`](AGENTS.md). It is mandatory reading
before a non-trivial change. The highlights:

- **Pure core stays pure.** `timeline_model`, `tracer`, `ipc`, `filmstrip`,
  `templates`, `api_index`, `python_highlight` and `python_completion` must not
  import Qt, `manimlib` or `moderngl`.
- **One OpenGL context, one thread.** GL lives only in `manim_studio.preview_host`;
  the Qt GUI thread never touches OpenGL. The editor talks to the host over IPC.
- **Long work never runs on the GUI thread.** Use worker threads/processes and
  Qt signals.
- Never call `Scene.play()` from a file-watcher callback.

## Set up

```powershell
uv sync --all-groups
```

Python 3.13, [`uv`](https://docs.astral.sh/uv/), an OpenGL 3.3 context (preview,
traces, stills), and `ffmpeg` on `PATH` (video export only).

## The gate — required before every commit

```powershell
uv run ruff format src tests
uv run ruff check src tests
uv run mypy
uv run pytest
```

Fast inner loop (skips GL + real ffmpeg):

```powershell
uv run pytest -m "not gl and not slow"
```

Test rules: deterministic (no fixed sleeps, no wall-clock dependence), Qt tests
run offscreen (`QT_QPA_PLATFORM=offscreen`), GL/LaTeX tests **skip** rather than
fail when the environment cannot support them.

Add a feature? Extend [`tests/scenes/feature_showcase.py`](tests/scenes/feature_showcase.py)
and its assertions in the same commit.

## Commits and pull requests

- Conventional commits: `feat(scope): ...`, `fix(scope): ...`, `test(scope): ...`,
  `docs: ...`, `chore: ...`.
- Small, single-purpose commits. Do not commit generated media (`*.mp4`, `*.png`
  renders), `.venv`, or caches.
- Self-review `git diff --staged` before committing.
- Pull requests should fill in the template, link the issue they close, and pass
  the CI gate (`ruff format --check`, `ruff check`, `mypy`, `pytest`).

## Where things live

See the architecture table in [`AGENTS.md`](AGENTS.md). For scene authoring and
editor workflows, the in-repo skills live under [`.opencode/skills/`](.opencode/skills).

## License

By contributing you agree that your contributions are licensed under the
[MIT License](LICENSE).
