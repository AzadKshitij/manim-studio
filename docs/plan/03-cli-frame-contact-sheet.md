# Chunk 3 — `manim-studio frame` and `manim-studio contact-sheet`

Files: edit `src/manim_studio/cli.py`, `src/manim_studio/spawn.py`,
`src/manim_studio/frozen_entry.py`, `tests/test_cli.py`, `tests/test_spawn.py`,
`tests/test_frozen_entry.py`.

## 3.1 Commands (parser additions in `build_parser`, handlers `cmd_frame`, `cmd_contact_sheet`)

```
manim-studio frame FILE --at SECONDS [-o OUT.png] [--scene NAME]
                   [--resolution W H] [--no-sidecar] [--json]
manim-studio contact-sheet FILE [--every SECONDS | --times 0,1,2.5] [--from S] [--to S]
                   [-o OUT.png] [--scene NAME] [--columns N] [--max-frames N]
                   [--width PX] [--resolution W H] [--no-sidecar] [--json]
```

* `--at` is `type=float, required=True`. `--every` default `0.5`; `--every` and `--times` are a
  mutually exclusive group; `--from`/`--to` use `dest="start"` / `dest="end"` (`from` is a Python
  keyword). `--max-frames` default 48; `--width` default 1600 (dest `sheet_width`);
  `--resolution` is `type=int, nargs=2, metavar=("W","H"), default=None`.
* Handler shape = copy of `cmd_still`: `with _capture_scene_output():` around the render call
  (manimgl prints to stdout; stdout must carry only our output). Exceptions:
  `SceneLoadError`, `FrameError`, `SheetError`, `ValueError` → `print(f"error: {exc}",
  file=sys.stderr)`, return 1 (arguments the parser cannot catch, such as negative `--at`: return
  2). Any other `Exception` → `scene failed: {type}: {exc}` to stderr, return 1.
* Default output when `-o` omitted:
  `Path(tempfile.mkdtemp(prefix="manim-studio-")) / f"{scene_name}_t{time:.2f}.png"`
  (frame) or `f"{scene_name}_sheet.png"` (sheet). Create parent dirs for explicit `-o`.
  Save with `image.save(path)`; frames are saved as RGBA PNG, sheets as RGB PNG.
* Output: without `--json` print the output path as the only stdout line (like `still`).
  With `--json` print exactly one JSON document via `_print_json`:
  `{"output": "<abs path>", **result.to_dict()}`.
* Add one-line help strings: "render the scene at an exact time to a PNG" and "tile frames with
  timestamps and block names into one PNG".

## 3.2 `spawn.cli_command`

```python
def cli_command(args: Sequence[str]) -> tuple[str, tuple[str, ...]]:
    """(executable, argv[1:]) that runs `manim-studio <args>` as a child process."""
    if frozen():
        return sys.executable, tuple(args)
    return sys.executable, ("-m", "manim_studio", *args)
```
Add to `__all__`. (Frozen: `classify_invocation` passes non-flag args through to the CLI, and
`gui_entry` routes known subcommands — see 3.3.)

## 3.3 `frozen_entry.CLI_SUBCOMMANDS`

Add `"agent-init"`, `"contact-sheet"`, `"frame"`, `"mcp"` (the last two commands land in chunks 4
and 5; adding all four now keeps the guard test honest only if the parser has them — so add
`"contact-sheet"` and `"frame"` in this chunk and the others in their own chunks). The existing
guard test compares this set with the parser's subparsers; run it.

## 3.4 Tests

* Non-GL: `main(["frame", str(SCENE_FILE)])` without `--at` → `SystemExit(2)` (argparse);
  `main(["frame", broken_syntax_file, "--at", "1"])` → 1 and stderr contains `error`;
  `main(["frame", showcase, "--at", "-1"])` → 2; `contact-sheet` with `--every 0` → 2 (map
  `SheetError` for arguments to 2 — choose: SheetError from bad `--every`/`--times`/`--from/--to`
  → return 2; everything else 1); `--every` and `--times` together → `SystemExit(2)`.
  Monkeypatch `cli.render_frame`/`cli.render_contact_sheet`? They are imported lazily inside the
  handlers (`from manim_studio.frames import ...` inside the function) so patch
  `manim_studio.frames.render_frame` with a fake returning a `FrameResult`-like object; assert
  the JSON shape and that the PNG exists.
* `tests/test_spawn.py`: `cli_command(["frame","x.py"])` non-frozen →
  `(sys.executable, ("-m","manim_studio","frame","x.py"))`; frozen (monkeypatch `spawn.frozen`)
  → `(sys.executable, ("frame","x.py"))`.
* GL: `main(["frame", showcase, "--at", "0.5", "-o", out, "--resolution", "160", "90"])` → 0, PNG
  `160x90`; `contact-sheet ... --every 1 -o sheet.png --json` → JSON `frames` length ≥ 2.

## 3.5 Done when

fast tests + guard test green; manual: `uv run manim-studio frame tests/scenes/feature_showcase.py
--at 0.5 -o $env:TEMP\f.png` writes a PNG you can open; commit `feat(cli): frame and contact-sheet
commands`.
