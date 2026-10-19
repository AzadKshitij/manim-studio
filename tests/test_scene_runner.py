"""Tests for the headless scene runner and the feature-showcase scene."""

from __future__ import annotations

import itertools
from pathlib import Path

import pytest

from manim_studio.scene_runner import (
    SceneLoadError,
    check_scene_file,
    discover_scenes,
    load_scene_class,
    make_scene,
    manimlib_argv_guard,
    render_still,
    run_construct,
    run_trace,
)
from manim_studio.timeline_model import load_log, save_log

SCENE_FILE = Path(__file__).parent / "scenes" / "feature_showcase.py"

MULTI_SCENE_SOURCE = '''
"""Two scenes for selection tests."""

from manimlib import Scene


class AlphaScene(Scene):
    def construct(self):
        self.wait(1)


class BetaScene(Scene):
    def construct(self):
        self.wait(2)
'''


def _write(tmp_path: Path, name: str, source: str) -> Path:
    path = tmp_path / name
    path.write_text(source, encoding="utf-8")
    return path


class TestDiscoverScenes:
    def test_showcase_is_discovered(self) -> None:
        scenes = discover_scenes(SCENE_FILE)
        assert [s.name for s in scenes] == ["FeatureShowcase"]
        assert scenes[0].lineno > 0
        assert "InteractiveScene" in scenes[0].bases

    def test_multiple_scenes_in_source_order(self, tmp_path: Path) -> None:
        path = _write(tmp_path, "multi.py", MULTI_SCENE_SOURCE)
        assert [s.name for s in discover_scenes(path)] == ["AlphaScene", "BetaScene"]

    def test_no_scenes(self, tmp_path: Path) -> None:
        path = _write(tmp_path, "empty.py", "x = 1\n")
        assert discover_scenes(path) == ()

    def test_syntax_error_raises(self, tmp_path: Path) -> None:
        path = _write(tmp_path, "broken.py", "class Foo(Scene:\n")
        with pytest.raises(SceneLoadError):
            discover_scenes(path)

    def test_attribute_base_is_recognized(self, tmp_path: Path) -> None:
        source = "import manimlib as ml\n\n\nclass MyScene(ml.Scene):\n    pass\n"
        path = _write(tmp_path, "attr.py", source)
        assert [s.name for s in discover_scenes(path)] == ["MyScene"]


class TestCheckSceneFile:
    def test_showcase_passes(self) -> None:
        result = check_scene_file(SCENE_FILE)
        assert result.ok
        assert [s.name for s in result.scenes] == ["FeatureShowcase"]
        assert result.error is None

    def test_syntax_error_is_reported_not_raised(self, tmp_path: Path) -> None:
        path = _write(tmp_path, "broken.py", "def f(:\n")
        result = check_scene_file(path)
        assert not result.ok
        assert result.error is not None and result.error_line == 1

    def test_no_scene_class_reports_error(self, tmp_path: Path) -> None:
        path = _write(tmp_path, "empty.py", "x = 1\n")
        result = check_scene_file(path)
        assert not result.ok
        assert result.error == "no Scene class found"

    def test_color_trap_warning_for_circle(self, tmp_path: Path) -> None:
        source = (
            "from manimlib import *\n\n\n"
            "class Trapped(Scene):\n"
            "    def construct(self):\n"
            "        circle = Circle(color=BLUE)\n"
            "        self.add(circle)\n"
        )
        result = check_scene_file(_write(tmp_path, "trapped.py", source))
        assert result.ok
        assert len(result.warnings) == 1
        assert "stroke_color" in result.warnings[0]

    def test_no_warning_with_stroke_color(self, tmp_path: Path) -> None:
        source = (
            "from manimlib import *\n\n\n"
            "class Fine(Scene):\n"
            "    def construct(self):\n"
            "        circle = Circle(stroke_color=BLUE)\n"
            "        square = Square(color=YELLOW)\n"
            "        self.add(circle, square)\n"
        )
        result = check_scene_file(_write(tmp_path, "fine.py", source))
        assert result.ok
        assert result.warnings == ()

    def test_dot_color_trap_warning(self, tmp_path: Path) -> None:
        source = (
            "from manimlib import *\n\n\n"
            "class Dotty(Scene):\n"
            "    def construct(self):\n"
            "        self.add(Dot(color=RED))\n"
        )
        result = check_scene_file(_write(tmp_path, "dotty.py", source))
        assert result.ok and result.warnings

    def test_missing_file_is_reported(self, tmp_path: Path) -> None:
        result = check_scene_file(tmp_path / "nope.py")
        assert not result.ok and result.error


class TestLoadSceneClass:
    def test_single_scene_auto_selected(self) -> None:
        cls = load_scene_class(SCENE_FILE)
        assert cls.__name__ == "FeatureShowcase"

    def test_explicit_name(self, tmp_path: Path) -> None:
        path = _write(tmp_path, "multi.py", MULTI_SCENE_SOURCE)
        assert load_scene_class(path, "BetaScene").__name__ == "BetaScene"

    def test_ambiguous_without_name(self, tmp_path: Path) -> None:
        path = _write(tmp_path, "multi.py", MULTI_SCENE_SOURCE)
        with pytest.raises(SceneLoadError, match="multiple scenes"):
            load_scene_class(path)

    def test_unknown_name(self, tmp_path: Path) -> None:
        path = _write(tmp_path, "multi.py", MULTI_SCENE_SOURCE)
        with pytest.raises(SceneLoadError, match="not found"):
            load_scene_class(path, "GammaScene")

    def test_import_error_wrapped(self, tmp_path: Path) -> None:
        path = _write(tmp_path, "bad_import.py", "import does_not_exist_xyz\n")
        with pytest.raises(SceneLoadError, match="ModuleNotFoundError"):
            load_scene_class(path)


class TestShowcaseCoverage:
    """The showcase must keep exercising every editor feature."""

    @pytest.mark.parametrize(
        "marker",
        [
            "self.add(",
            "self.remove(",
            "self.play(",
            "self.wait(",
            "save_state()",
            "self.undo()",
            "self.redo()",
            "always_redraw",
            "add_updater",
            "for ",
            "if ",
            "VGroup(",
            "Text(",
            "RegularPolygon(",
            "Dot(",
            "Square(",
            "Circle(",
            "self.frame.animate",
            "# checkpoint",
            "Tex(",
        ],
    )
    def test_marker_present(self, marker: str) -> None:
        assert marker in SCENE_FILE.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def trace_result():
    """Trace the showcase once for all integration assertions."""
    try:
        return run_trace(SCENE_FILE)
    except Exception as exc:  # environment probe: skip when manimgl/GL unavailable
        pytest.skip(f"manimgl runtime unavailable: {type(exc).__name__}: {exc}")


@pytest.mark.manim
class TestRunTrace:
    def test_scene_identity(self, trace_result) -> None:
        assert trace_result.scene_name == "FeatureShowcase"
        assert trace_result.resolution == (854, 480)
        assert trace_result.fps == 30

    def test_blocks_are_sequential_and_timed(self, trace_result) -> None:
        blocks = trace_result.snapshot.blocks
        # 13 blocks without LaTeX, 15 with it (the Tex section is optional).
        assert len(blocks) >= 12
        assert [b.index for b in blocks] == list(range(len(blocks)))
        for previous, current in itertools.pairwise(blocks):
            assert current.start == pytest.approx(previous.end)
        assert trace_result.snapshot.total_duration == pytest.approx(blocks[-1].end)

    def test_requested_run_times_survive_trace(self, trace_result) -> None:
        wait_durations = [b.duration for b in trace_result.snapshot.blocks if b.name == "Wait"]
        assert wait_durations
        for duration in wait_durations:
            assert any(abs(duration - expected) < 1e-9 for expected in (0.1, 0.15, 0.2, 0.25)), (
                duration
            )

    def test_expected_animation_names(self, trace_result) -> None:
        names = [b.name for b in trace_result.snapshot.blocks]
        assert "ShowCreation" in names
        assert "Rotate" in names
        assert "Transform" in names
        assert any("FadeIn" in name and "Indicate" in name for name in names)
        assert any(name.startswith("Animate(") for name in names)

    def test_scene_api_stats(self, trace_result) -> None:
        stats = trace_result.snapshot.stats
        assert stats["play"] >= 10
        assert stats["wait"] >= 3
        assert stats["add"] >= 4
        assert stats["remove"] >= 2
        assert stats["save_state"] >= 1
        assert stats["undo"] == 1
        assert stats["redo"] == 1

    def test_events_cover_api(self, trace_result) -> None:
        kinds = {event.kind for event in trace_result.snapshot.events}
        assert {"add", "remove", "save_state", "undo", "redo"} <= kinds

    def test_log_round_trip(self, trace_result, tmp_path: Path) -> None:
        path = tmp_path / "log.json"
        save_log(path, trace_result.snapshot.blocks)
        loaded = load_log(path)
        assert loaded.blocks == trace_result.snapshot.blocks


@pytest.mark.manim
@pytest.mark.gl
class TestRenderStill:
    def test_still_is_written(self, tmp_path: Path) -> None:
        target = tmp_path / "showcase.png"
        try:
            written = render_still(SCENE_FILE, output=target, resolution=(320, 180))
        except Exception as exc:  # environment probe: skip when manimgl/GL unavailable
            pytest.skip(f"manimgl runtime unavailable: {type(exc).__name__}: {exc}")
        assert written == target and target.is_file()

        from PIL import Image

        with Image.open(target) as image:
            assert image.size == (320, 180)

    def test_still_shows_the_final_frame_not_a_transparent_canvas(self, tmp_path: Path) -> None:
        # Regression: the still used to read the framebuffer before anything drew it, giving a
        # fully transparent PNG (docs/adr/0013). It must match a construct followed by a draw.
        from PIL import Image, ImageChops, ImageStat

        from manim_studio.latex import apply_runtime_path
        from manim_studio.scrub import read_upright_rgba

        target = tmp_path / "showcase.png"
        try:
            render_still(SCENE_FILE, output=target, resolution=(320, 180))
            apply_runtime_path()
            with manimlib_argv_guard():
                scene = make_scene(
                    load_scene_class(SCENE_FILE), resolution=(320, 180), skip_animations=True
                )
                run_construct(scene)
                scene.update_frame(dt=0, force_draw=True)
                rgba, width, height = read_upright_rgba(scene.camera)
        except Exception as exc:  # environment probe: skip when manimgl/GL unavailable
            pytest.skip(f"manimgl runtime unavailable: {type(exc).__name__}: {exc}")

        with Image.open(target) as still:
            still_rgba = still.convert("RGBA")
        assert still_rgba.getchannel("A").getextrema()[1] == 255
        reference = Image.frombytes("RGBA", (width, height), rgba)
        difference = ImageChops.difference(still_rgba.convert("RGB"), reference.convert("RGB"))
        assert all(mean < 8 for mean in ImageStat.Stat(difference).mean)


class TestPatchedSourceLoading:
    """Timeline patches run as in-memory source while ``__file__`` stays the real path."""

    def test_executes_the_given_source_not_the_file(self, tmp_path: Path) -> None:
        from manim_studio.scene_runner import load_scene_module

        scene = tmp_path / "patched_scene.py"
        scene.write_text("VALUE = 1\n", encoding="utf-8")
        module = load_scene_module(scene, source="VALUE = 2\n")
        assert module.VALUE == 2
        assert Path(module.__file__).resolve() == scene.resolve()

    def test_tracebacks_keep_the_real_filename(self, tmp_path: Path) -> None:
        from manim_studio.scene_runner import load_scene_module

        scene = tmp_path / "patched_scene2.py"
        scene.write_text("def f():\n    return 1\n", encoding="utf-8")
        module = load_scene_module(scene, source="def f():\n    return 2\n")
        assert Path(module.f.__code__.co_filename).resolve() == scene.resolve()

    def test_future_flags_do_not_leak_into_user_scenes(self, tmp_path: Path) -> None:
        from manim_studio.scene_runner import SceneLoadError, load_scene_module

        scene = tmp_path / "patched_scene3.py"
        scene.write_text("x = 1\n", encoding="utf-8")
        # scene_runner itself uses ``from __future__ import annotations``.  If that
        # leaked into the compiled user code this undefined annotation would stay a
        # lazy string; evaluating it (a NameError) proves the user's code is compiled
        # with its own flags.
        with pytest.raises(SceneLoadError, match="NameError"):
            load_scene_module(scene, source="def f(a: Undefined):\n    return a\n")

    def test_runtime_is_exposed_and_reset_per_construct(self, tmp_path: Path) -> None:
        from manim_studio.patch_runtime import StudioRuntime
        from manim_studio.scene_runner import load_scene_module

        scene = tmp_path / "patched_scene4.py"
        scene.write_text("x = 1\n", encoding="utf-8")
        source = (
            "class Thing:\n"
            "    def construct(self):\n"
            "        return [__studio__.dur('a', {1: 9.0}, 1.0) for _ in range(2)]\n"
        )
        runtime = StudioRuntime()
        module = load_scene_module(scene, source=source, runtime=runtime)
        assert module.__studio__ is runtime
        assert module.Thing().construct() == [1.0, 9.0]
        # A second run starts counting from zero again.
        assert module.Thing().construct() == [1.0, 9.0]

    def test_syntax_errors_become_scene_load_errors(self, tmp_path: Path) -> None:
        from manim_studio.scene_runner import SceneLoadError, load_scene_module

        scene = tmp_path / "patched_scene5.py"
        scene.write_text("x = 1\n", encoding="utf-8")
        with pytest.raises(SceneLoadError):
            load_scene_module(scene, source="def broken(:\n")
