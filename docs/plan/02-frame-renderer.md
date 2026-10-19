# Chunk 2 — `frames.py`: render a scene at any time, build a contact sheet image

Rules: may import `scene_runner`, `scrub`, `patches`, `sidecar`, `patch_runtime`, `tracer`,
`contact_sheet`, `export` (public helpers), PIL. `manimlib` only inside functions (lazily, via the
existing helpers). Files: edit `src/manim_studio/export.py`; create `src/manim_studio/frames.py`,
`tests/test_frames.py`; add an AGENTS.md architecture-map row for `frames.py`.

## 2.0 Mini-refactor first (own commit `refactor(export): make the sidecar resolution helpers public`)

In `export.py` rename `_resolve_patches` → `resolve_patches`, `_resolve_settings` →
`resolve_settings`, `_effective_settings` → `effective_settings` (definitions at ~219-265, call
sites ~147-149; nothing else in `src/` or `tests/` uses them — re-grep to confirm). Add the three
names to `export.__all__` if it exists. Behaviour unchanged. Run the gate.

## 2.1 SPIKE before writing frames.py (10 minutes, do not skip, do not commit)

Write a throwaway script in the scratchpad dir that: loads `tests/scenes/feature_showcase.py`
through `load_scene_class`, builds `blocks` via `TraceRecorder` exactly as in
`run_trace`, builds a FRESH scene with `make_scene(..., skip_animations=True,
resolution=(320, 180))`, wraps it in `ScrubEngine(scene, blocks)`, then for `t in (0.0,
total/2, total)`: `engine.pose(t)`; `scene.update_frame(dt=0, force_draw=True)`;
`rgba, w, h = read_upright_rgba(scene.camera)`; `Image.frombytes("RGBA", (w, h), rgba).save(...)`.
Confirm: (a) works with `window=None` (no window), (b) frames at the three times differ,
(c) the `t=total` frame is visually the same as `render_still` output. If `read_upright_rgba`
fails headless, fall back to `scene.get_image()`. Record the finding in one line in the chunk's
commit message body. If NEITHER works headless, stop and report to the owner (fallback would be
host-based rendering via `preview_host`; that is a design change).

## 2.2 `frames.py` public API (exact)

```python
class FrameError(RuntimeError): ...   # a scene failed while posing; message includes the line

DEFAULT_RESOLUTION = (854, 480)
SHEET_FRAME_RESOLUTION = (640, 360)

@dataclass(frozen=True, slots=True)
class PreparedScene:
    scene_class: type
    scene_name: str
    blocks: tuple[TimelineBlock, ...]
    total_duration: float
    fps: int
    resolution: tuple[int, int]        # final render size (settings win, see below)
    settings: SceneSettings | None
    patches_applied: int
    patches_orphaned: int

@dataclass(frozen=True, slots=True)
class FrameResult:
    image: Image.Image                 # RGBA, upright
    time: float                        # time actually posed (clamped)
    requested_time: float
    block: TimelineBlock | None
    prepared: PreparedScene
    def to_dict(self) -> dict[str, Any]   # JSON: time, requested_time, clamped(bool), block_index,
                                          # block_name, total_duration, size [w,h], scene,
                                          # patches_applied, patches_orphaned

@dataclass(frozen=True, slots=True)
class SheetResult:
    image: Image.Image                 # RGB sheet
    layout: SheetLayout
    sampling: Sampling | None          # None when explicit `times` were given
    prepared: PreparedScene
    def to_dict(self) -> dict[str, Any]   # frames: [{index,time,block_index,block_name}],
                                          # columns, rows, size, thinned, step, total_duration, scene

def prepare_scene(path: str | Path, scene_name: str | None = None, *,
                  resolution: tuple[int, int] | None = None, fps: int | None = None,
                  use_sidecar: bool = True) -> PreparedScene
def render_frames(prepared: PreparedScene, times: Sequence[float]) -> list[Image.Image]
def render_frame(path, at: float, scene_name=None, *, resolution=None, use_sidecar=True) -> FrameResult
def render_contact_sheet(path, scene_name=None, *, every: float | None = 0.5,
                         times: Sequence[float] | None = None, start: float = 0.0,
                         end: float | None = None, columns: int | None = None,
                         max_frames: int = 48, sheet_width: int = 1600,
                         resolution: tuple[int, int] | None = None,
                         use_sidecar: bool = True) -> SheetResult
```

## 2.3 `prepare_scene` — exact steps (mirror `preview_host.load`, lines ~542-595)

1. `apply_runtime_path()` (from `manim_studio.latex`); `path = Path(path)`.
2. `check = check_scene_file(path)`; if not ok → `raise SceneLoadError(check.error or "scene check
   failed", line=check.error_line)` (same as `run_trace`).
3. If `use_sidecar`: `patches = export.resolve_patches(path, None, True)`,
   `settings = export.resolve_settings(path, scene_name, None, True)`; else `()` / `None`.
   If `resolution`/`fps` were passed and `settings` is not None →
   `settings = export.effective_settings(settings, resolution, fps)`.
   Render size = `(settings.width, settings.height)` if settings else
   `resolution or DEFAULT_RESOLUTION`.
4. `base = path.read_text(encoding="utf-8")`; `result = apply_patches(base, patches)`;
   `runtime = StudioRuntime() if result.changed else None`.
5. Inside `with manimlib_argv_guard():` →
   `module = load_scene_module(path, source=result.text if result.changed else None,
   runtime=runtime)`; `scene_class = select_scene_class(module, scene_name, path=path)`;
   `scene = make_scene(scene_class, resolution=size, fps=fps, settings=settings,
   skip_animations=True)`; `recorder = TraceRecorder(record_events=False)`;
   `recorder.attach(scene)`; `try: run_construct(scene); snapshot = recorder.snapshot()
   finally: recorder.detach()`.
6. `blocks = snapshot.blocks`; `total = max((b.end for b in blocks), default=0.0)`;
   `fps_value = int(scene.camera.fps)`. Return `PreparedScene(... patches_applied=len(result.applied),
   patches_orphaned=len(result.orphaned))`. Never leave GL scenes referenced longer than needed.

## 2.4 `render_frames` — exact steps

* `times` sorted copy is NOT assumed; process in the given order but remember each result by
  index. Clamp each requested time into `[0, prepared.total_duration]`.
* Inside `manimlib_argv_guard()`: build ONE fresh scene
  `scene = make_scene(prepared.scene_class, resolution=prepared.resolution, fps=prepared.fps,
  settings=prepared.settings, skip_animations=True)`.
* If `prepared.blocks` is empty (scene with no play/wait): `run_construct(scene)` once, grab one
  image, return that same image (copied) for every time.
* Else `engine = ScrubEngine(scene, prepared.blocks)`; for each time:
  `engine.pose(t)` (wrap `ScrubError` → `FrameError(str(exc))`),
  `scene.update_frame(dt=0, force_draw=True)`,
  `rgba, w, h = read_upright_rgba(scene.camera)`,
  `Image.frombytes("RGBA", (w, h), rgba)` (already a copy of the bytes).
* Catch scene exceptions other than `ScrubError` and re-raise `FrameError` using
  `format_scene_error(exc, path)` → message `f"{err.message} (line {err.line})"` when a line is
  known. (Keep `path` on `PreparedScene` as `scene_path: Path` for this — add the field.)
* Performance note (record in ADR, do NOT optimise now): each pose replays the scene from t=0, so
  N frames = N replays; the 48-frame cap keeps this to seconds for typical scenes. Optimisation
  path later: `ScrubEngine.stream_frames` single pass.

## 2.5 `render_frame` / `render_contact_sheet`

* `render_frame`: `prepared = prepare_scene(...)`; `t = min(max(at, 0), total)`;
  `image = render_frames(prepared, [t])[0]`; block = `contact_sheet.block_at_time(...)`.
  `at < 0` or NaN → `ValueError("--at must be a non-negative number")`.
* `render_contact_sheet`: exactly one of `every` / `times` (if both None or both given →
  `SheetError`). Frames render at `resolution or SHEET_FRAME_RESOLUTION` (settings still win).
  `sampling = sample_times(total, every, start=, end=, max_frames=)` or
  `times = parse-like clamp`; `layout = plan_sheet(times, blocks, frame_size, columns=,
  max_width=sheet_width)`; render frames; composite:
  `sheet = Image.new("RGB", (layout.width, layout.height), "#1e1e1e")`; for each cell paste the
  frame (`frame.convert("RGB").resize((cell.width, cell.height), Image.LANCZOS)`) at
  `(cell.x, cell.y)`; draw a 1px `#3c3c3c` rectangle border around the image; draw
  `cell.label` at `(cell.x + 2, cell.label_y + 3)` fill `#d4d4d4` with
  `ImageDraw.Draw(sheet)`. Font: `try: ImageFont.load_default(size=14)` `except TypeError:
  ImageFont.load_default()` (older Pillow has no `size`).
  Pillow is a transitive dependency of manimgl; add `"pillow>=10.1"` to `pyproject.toml`
  dependencies (explicit is safer) and run `uv lock`.

## 2.6 Tests (`tests/test_frames.py`)

Non-GL (run in the fast loop; monkeypatch `frames.render_frames` to return solid-colour
`Image.new("RGBA", (64, 36), "#ff0000")` objects and `frames.prepare_scene` to return a hand-made
`PreparedScene` with three `TimelineBlock`s): contact sheet size equals `layout.width/height`;
cell pixel at `(cell.x+1, cell.y+1)` is the frame colour; label strip pixel row is not the
background colour somewhere (text drawn); `to_dict` shapes; `render_frame` clamps `at` beyond
total and reports `clamped`; both/neither of every/times raises.
GL (`@pytest.mark.manim` + `@pytest.mark.gl`, skip on environment failure like
`test_scene_runner.TestRenderStill`): `render_frame(SCENE_FILE, 0.0, resolution=(320,180))` size
`(320,180)`; frames at `0.0` and `total` differ (`ImageChops.difference(...).getbbox()` is not
None); `render_frame` at `total` ≈ `render_still` output (mean abs diff small, < 8/255 per
channel); contact sheet of the showcase every 0.5 has `len(cells) == len(times)`.
Patched-source test (GL): write a copy of a tiny scene + a sidecar with a `SetDuration` patch
(see `tests/test_sidecar.py` / `test_patches.py` for how to construct one) and assert
`prepared.total_duration` equals the patched duration.

## 2.7 Done when

fast tests green, GL tests green locally (or skip with reason), spike conclusion recorded,
commits `refactor(export): ...` then `feat(frames): render a scene at any time`.
