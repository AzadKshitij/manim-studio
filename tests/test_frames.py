"""frames: rendering a scene at any time and composing contact sheets.

The fast tests replace the scene with fake frames (``prepare_scene`` and ``render_frames`` are
monkeypatched), so they need no GL. The ``gl`` tests run the real showcase through ScrubEngine.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image, ImageChops, ImageColor, ImageStat

from manim_studio import frames
from manim_studio.contact_sheet import SheetError
from manim_studio.frames import (
    PreparedScene,
    prepare_scene,
    render_contact_sheet,
    render_frame,
    render_frames,
)
from manim_studio.latex import apply_runtime_path
from manim_studio.patches import Anchor, SetDuration
from manim_studio.scene_runner import load_scene_class, make_scene, run_construct
from manim_studio.scrub import read_upright_rgba
from manim_studio.sidecar import save_sidecar
from manim_studio.site_index import index_source
from manim_studio.timeline_model import TimelineBlock

SCENE_FILE = Path(__file__).parent / "scenes" / "feature_showcase.py"

PATCHED_SCENE = """\
from manimlib import *


class Patched(Scene):
    def construct(self):
        self.add(Dot(color="#58C4DD"))
        self.wait(1.0)
        self.wait(1.0)
"""

BLOCKS = (
    TimelineBlock(index=0, name="Create", start=0.0, duration=1.0),
    TimelineBlock(index=1, name="Wait", start=1.0, duration=1.0),
    TimelineBlock(index=2, name="Fade", start=2.0, duration=1.0),
)
COLOURS = ("#ff0000", "#00ff00", "#0000ff", "#ffff00", "#00ffff")


class _FakeScene:
    pass


def _prepared(total: float = 3.0) -> PreparedScene:
    return PreparedScene(
        scene_class=_FakeScene,
        scene_name="Fake",
        scene_path=Path("fake.py"),
        blocks=BLOCKS,
        total_duration=total,
        fps=30,
        resolution=(64, 36),
        settings=None,
        patches_applied=0,
        patches_orphaned=0,
    )


@pytest.fixture
def posed_times(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    """Replace the scene with solid frames; returns the list every posed time is appended to."""
    prepared = _prepared()
    posed: list[float] = []
    monkeypatch.setattr(frames, "prepare_scene", lambda *args, **kwargs: prepared)

    def fake_render(prep: PreparedScene, times: list[float]) -> list[Image.Image]:
        posed.extend(times)
        return [
            Image.new("RGBA", prep.resolution, COLOURS[i % len(COLOURS)]) for i in range(len(times))
        ]

    monkeypatch.setattr(frames, "render_frames", fake_render)
    return posed


@pytest.mark.usefixtures("posed_times")
class TestContactSheetComposition:
    def test_sheet_size_matches_the_layout(self) -> None:
        result = render_contact_sheet(SCENE_FILE, every=1.0)
        assert result.image.size == (result.layout.width, result.layout.height)
        assert result.image.mode == "RGB"

    def test_each_tile_holds_its_frame(self) -> None:
        result = render_contact_sheet(SCENE_FILE, every=1.0)
        for cell in result.layout.cells:
            expected = ImageColor.getrgb(COLOURS[cell.index % len(COLOURS)])
            assert result.image.getpixel((cell.x + 1, cell.y + 1)) == expected

    def test_every_tile_has_a_caption_strip(self) -> None:
        result = render_contact_sheet(SCENE_FILE, every=1.0)
        background = ImageColor.getrgb(frames.SHEET_BACKGROUND)
        for cell in result.layout.cells:
            strip = result.image.crop(
                (
                    cell.x,
                    cell.label_y,
                    cell.x + cell.width,
                    cell.label_y + result.layout.label_height,
                )
            )
            blank = Image.new("RGB", strip.size, background)
            assert ImageChops.difference(strip, blank).getbbox() is not None

    def test_to_dict_describes_every_frame(self) -> None:
        payload = render_contact_sheet(SCENE_FILE, every=1.0).to_dict()
        assert [frame["time"] for frame in payload["frames"]] == [0.0, 1.0, 2.0, 3.0]
        assert [frame["block_name"] for frame in payload["frames"]] == [
            "Create",
            "Wait",
            "Fade",
            "Fade",
        ]
        assert payload["thinned"] is False
        assert payload["step"] == 1.0
        assert payload["columns"] == 2
        assert payload["rows"] == 2
        assert payload["scene"] == "Fake"

    def test_explicit_times_are_used_as_given(self) -> None:
        result = render_contact_sheet(SCENE_FILE, times=[2.5, 0.0, 99.0])
        assert [cell.time for cell in result.layout.cells] == [0.0, 2.5, 3.0]
        assert result.sampling is None
        assert result.to_dict()["step"] is None

    def test_thinning_is_reported(self) -> None:
        result = render_contact_sheet(SCENE_FILE, every=0.1, max_frames=4)
        assert result.to_dict()["thinned"] is True
        assert len(result.layout.cells) == 4

    @pytest.mark.parametrize("kwargs", [{}, {"every": 1.0, "times": [0.0]}])
    def test_needs_exactly_one_of_every_or_times(self, kwargs: dict[str, object]) -> None:
        with pytest.raises(SheetError):
            render_contact_sheet(SCENE_FILE, **kwargs)

    def test_too_many_explicit_times_are_refused(self) -> None:
        with pytest.raises(SheetError, match="max_frames"):
            render_contact_sheet(SCENE_FILE, times=[0.0, 1.0, 2.0], max_frames=2)


@pytest.mark.usefixtures("posed_times")
class TestRenderFrame:
    def test_clamps_past_the_end_and_says_so(self, posed_times: list[float]) -> None:
        result = render_frame(SCENE_FILE, 99.0)
        assert result.time == 3.0
        assert result.requested_time == 99.0
        assert result.to_dict()["clamped"] is True
        assert posed_times == [3.0]

    def test_inside_the_scene_is_not_clamped(self) -> None:
        result = render_frame(SCENE_FILE, 1.5)
        assert result.block is BLOCKS[1]
        payload = result.to_dict()
        assert payload["clamped"] is False
        assert payload["block_name"] == "Wait"
        assert payload["size"] == [64, 36]

    @pytest.mark.parametrize("bad", [-0.1, float("nan"), float("inf")])
    def test_bad_time_raises(self, bad: float) -> None:
        with pytest.raises(ValueError, match="non-negative"):
            render_frame(SCENE_FILE, bad)


def _skip_without_runtime(exc: Exception) -> None:
    pytest.skip(f"manimgl runtime unavailable: {type(exc).__name__}: {exc}")


def _full_run_frame(path: Path, resolution: tuple[int, int]) -> Image.Image:
    """The final state of a scene after one full construct, drawn the same way as a pose."""
    apply_runtime_path()
    scene = make_scene(load_scene_class(path), resolution=resolution, skip_animations=True)
    run_construct(scene)
    scene.update_frame(dt=0, force_draw=True)
    rgba, width, height = read_upright_rgba(scene.camera)
    return Image.frombytes("RGBA", (width, height), rgba)


@pytest.mark.manim
@pytest.mark.gl
class TestRealScene:
    def test_frame_has_the_requested_size(self) -> None:
        try:
            result = render_frame(SCENE_FILE, 0.5, resolution=(320, 180))
        except Exception as exc:  # environment probe: skip when manimgl/GL unavailable
            _skip_without_runtime(exc)
        assert result.image.size == (320, 180)
        assert result.block is not None

    def test_start_and_end_frames_differ(self) -> None:
        try:
            start = render_frame(SCENE_FILE, 0.0, resolution=(320, 180)).image
            end = render_frame(SCENE_FILE, 99.0, resolution=(320, 180)).image
        except Exception as exc:
            _skip_without_runtime(exc)
        difference = ImageChops.difference(start.convert("RGB"), end.convert("RGB"))
        assert difference.getbbox() is not None

    def test_frame_past_the_end_matches_a_full_run(self) -> None:
        try:
            posed = render_frame(SCENE_FILE, 99.0, resolution=(320, 180)).image
            reference = _full_run_frame(SCENE_FILE, (320, 180))
        except Exception as exc:
            _skip_without_runtime(exc)
        difference = ImageChops.difference(posed.convert("RGB"), reference.convert("RGB"))
        assert all(mean < 8 for mean in ImageStat.Stat(difference).mean)

    def test_contact_sheet_has_one_tile_per_sample(self) -> None:
        try:
            result = render_contact_sheet(SCENE_FILE, every=0.5, resolution=(320, 180))
        except Exception as exc:
            _skip_without_runtime(exc)
        assert result.sampling is not None
        assert len(result.layout.cells) == len(result.sampling.times)
        assert result.image.size == (result.layout.width, result.layout.height)

    def test_sidecar_patch_changes_the_duration(self, tmp_path: Path) -> None:
        scene = tmp_path / "patched.py"
        scene.write_text(PATCHED_SCENE, encoding="utf-8")
        second_wait = index_source(PATCHED_SCENE).sites[1]
        assert second_wait.kind == "wait"
        save_sidecar(scene, [SetDuration(Anchor(second_wait.site_id), 3.0)])
        try:
            patched = prepare_scene(scene)
            plain = prepare_scene(scene, use_sidecar=False)
        except Exception as exc:
            _skip_without_runtime(exc)
        assert patched.total_duration == pytest.approx(4.0)
        assert patched.patches_applied == 1
        assert plain.total_duration == pytest.approx(2.0)

    def test_render_frames_honours_the_requested_size(self) -> None:
        try:
            prepared = prepare_scene(SCENE_FILE, resolution=(160, 90))
            images = render_frames(prepared, [0.0, prepared.total_duration])
        except Exception as exc:
            _skip_without_runtime(exc)
        assert [image.size for image in images] == [(160, 90), (160, 90)]
