"""Scene settings: validation, camera config and the managed source block (pure)."""

from __future__ import annotations

import math
import sys
import types
from unittest import mock

import pytest

from manim_studio.scene_settings import (
    BLOCK_BEGIN,
    BLOCK_END,
    RESOLUTION_PRESETS,
    SceneSettings,
    SettingsConflictError,
    SettingsError,
    apply_block,
    has_block,
    normalize_hex,
    remove_block,
    remove_class_block,
    render_block,
    resolution_preset_for,
)

PORTRAIT = SceneSettings(width=1080, height=1920, background_color="#1e1e2e")
SOURCE = """class Intro(Scene):
    def construct(self) -> None:
        self.wait(1.0)


class Other(Scene):
    pass
"""


TWO_CLASSES = "class Intro(Scene):\n    pass\n\n\nclass Other(Scene):\n    pass\n"


class _FakeCamera:
    """Stands in for manimlib's Camera: only the uniform the pixel fix touches."""

    def __init__(self) -> None:
        self.uniforms: dict[str, tuple[float, float, float]] = {}
        self._reset()

    def _reset(self) -> None:
        self.uniforms["frame_rescale_factors"] = (2 / 14.2, 0.25, 1.0)

    def refresh_uniforms(self) -> None:
        self._reset()

    def get_aspect_ratio(self) -> float:
        return 1080 / 1920


class _FakeScene:
    """Stands in for manimlib's Scene: records what the generated __init__ passes up."""

    def __init__(self, **kwargs: object) -> None:
        self.received = kwargs
        self.camera = _FakeCamera()


def _run_class(source: str, name: str) -> type:
    """Execute ``source`` with a stub Scene and manimlib constants; return the class.

    No manimlib import is performed, so these tests stay pure and fast.
    """
    constants = types.ModuleType("manimlib.constants")
    constants.FRAME_HEIGHT = 8.0  # type: ignore[attr-defined]
    namespace: dict[str, object] = {"Scene": _FakeScene}
    with mock.patch.dict(sys.modules, {"manimlib.constants": constants}):
        exec(compile(source, "<scene>", "exec"), namespace)
    return namespace[name]  # type: ignore[return-value]


def _build(cls: type) -> object:
    """Construct a stub scene with the manimlib constants its __init__ imports."""
    constants = types.ModuleType("manimlib.constants")
    constants.FRAME_HEIGHT = 8.0  # type: ignore[attr-defined]
    with mock.patch.dict(sys.modules, {"manimlib.constants": constants}):
        return cls()


class TestValidation:
    def test_defaults_are_landscape_1080p(self) -> None:
        settings = SceneSettings()
        assert (settings.width, settings.height, settings.fps) == (1920, 1080, 30)
        assert settings.orientation == "landscape"
        assert settings.aspect_label == "16:9"
        assert not settings.needs_pixel_fix

    @pytest.mark.parametrize("value", [0, 63, 4097, -5, 1080.0, True])
    def test_sides_must_be_whole_numbers_in_range(self, value: object) -> None:
        with pytest.raises(SettingsError):
            SceneSettings(width=value)  # type: ignore[arg-type]

    @pytest.mark.parametrize("value", [0, 121, 29.5])
    def test_fps_must_be_in_range(self, value: object) -> None:
        with pytest.raises(SettingsError):
            SceneSettings(fps=value)  # type: ignore[arg-type]

    @pytest.mark.parametrize("value", ["red", "#12345", "#GGGGGG", 123])
    def test_colours_must_be_six_digit_hex(self, value: object) -> None:
        with pytest.raises(SettingsError):
            SceneSettings(background_color=value)  # type: ignore[arg-type]

    def test_colours_are_normalised_to_upper_case(self) -> None:
        assert SceneSettings(background_color="#1e1e2e").background_color == "#1E1E2E"

    @pytest.mark.parametrize("value", [-0.1, 1.5, True])
    def test_opacity_must_be_between_zero_and_one(self, value: object) -> None:
        with pytest.raises(SettingsError):
            SceneSettings(background_opacity=value)  # type: ignore[arg-type]

    def test_too_many_swatches_are_rejected(self) -> None:
        with pytest.raises(SettingsError):
            SceneSettings(swatches=("#FFFFFF",) * 13)

    def test_settings_are_immutable(self) -> None:
        settings = SceneSettings()
        with pytest.raises(AttributeError):
            settings.width = 800  # type: ignore[misc]

    def test_normalize_hex_rejects_short_form(self) -> None:
        with pytest.raises(SettingsError):
            normalize_hex("#FFF")


class TestGeometry:
    def test_portrait_frame_keeps_manimgl_height(self) -> None:
        assert PORTRAIT.orientation == "portrait"
        assert PORTRAIT.aspect_label == "9:16"
        assert PORTRAIT.frame_shape == (4.5, 8.0)
        assert PORTRAIT.needs_pixel_fix

    def test_landscape_frame_matches_manimgl_default_width(self) -> None:
        width, height = SceneSettings().frame_shape
        assert height == 8.0
        assert math.isclose(width, 8.0 * 16 / 9, rel_tol=1e-6)

    def test_square_is_neither_portrait_nor_landscape(self) -> None:
        assert SceneSettings(width=1080, height=1080).orientation == "square"

    def test_swapped_turns_landscape_into_portrait(self) -> None:
        swapped = SceneSettings().swapped()
        assert (swapped.width, swapped.height) == (1080, 1920)
        assert swapped.orientation == "portrait"

    def test_camera_config_carries_every_setting(self) -> None:
        config = PORTRAIT.camera_config()
        assert config == {
            "resolution": (1080, 1920),
            "fps": 30,
            "background_color": "#1E1E2E",
            "background_opacity": 1.0,
            "frame_config": {"frame_shape": (4.5, 8.0)},
        }

    def test_preset_lookup(self) -> None:
        preset = resolution_preset_for(1080, 1920)
        assert preset is not None and preset.key == "portrait-1080p"
        assert resolution_preset_for(999, 999) is None
        assert len({preset.key for preset in RESOLUTION_PRESETS}) == len(RESOLUTION_PRESETS)


class TestSerialisation:
    def test_round_trip(self) -> None:
        settings = SceneSettings(
            width=720,
            height=1280,
            fps=60,
            background_color="#101010",
            background_opacity=0.5,
            palette="ocean",
            swatches=("#FFFFFF", "#ff0000"),
        )
        assert SceneSettings.from_dict(settings.to_dict()) == settings

    def test_missing_keys_take_defaults(self) -> None:
        assert SceneSettings.from_dict({"width": 720}) == SceneSettings(width=720)

    def test_from_dict_rejects_non_objects(self) -> None:
        with pytest.raises(SettingsError):
            SceneSettings.from_dict([1, 2])

    def test_from_dict_rejects_bad_swatch_list(self) -> None:
        with pytest.raises(SettingsError):
            SceneSettings.from_dict({"swatches": "#FFFFFF"})


class TestManagedBlock:
    def test_block_is_inserted_inside_the_named_class(self) -> None:
        result = apply_block(SOURCE, "Intro", PORTRAIT)
        assert has_block(result)
        scene = _build(_run_class(result, "Intro"))
        config = scene.received["camera_config"]  # type: ignore[attr-defined]
        assert config["resolution"] == (1080, 1920)
        assert config["background_color"] == "#1E1E2E"

    def test_other_classes_are_untouched(self) -> None:
        result = apply_block(SOURCE, "Intro", PORTRAIT)
        other = _run_class(result, "Other")
        assert "__init__" not in vars(other)

    def test_applying_twice_is_idempotent(self) -> None:
        once = apply_block(SOURCE, "Intro", PORTRAIT)
        assert apply_block(once, "Intro", PORTRAIT) == once
        assert once.count(BLOCK_BEGIN) == 1 and once.count(BLOCK_END) == 1

    def test_reapplying_new_settings_replaces_the_block(self) -> None:
        first = apply_block(SOURCE, "Intro", PORTRAIT)
        landscape = apply_block(first, "Intro", SceneSettings())
        assert "1080, 1920" not in landscape
        assert landscape.count(BLOCK_BEGIN) == 1

    def test_remove_block_restores_the_original_source(self) -> None:
        assert remove_block(apply_block(SOURCE, "Intro", PORTRAIT)) == SOURCE

    def test_portrait_block_installs_the_pixel_fix(self) -> None:
        result = apply_block(SOURCE, "Intro", PORTRAIT)
        assert "def __init__(self, **kwargs) -> None:" in result
        assert "refresh_uniforms" in result

    def test_landscape_block_has_no_pixel_fix(self) -> None:
        result = apply_block(SOURCE, "Intro", SceneSettings())
        assert "def __init__(self, **kwargs) -> None:" in result
        assert "refresh_uniforms" not in result

    def test_pixel_fix_adjusts_horizontal_rescale_factor(self) -> None:
        scene = _build(_run_class(apply_block(SOURCE, "Intro", PORTRAIT), "Intro"))
        scene.camera.refresh_uniforms()
        factors = scene.camera.uniforms["frame_rescale_factors"]
        assert math.isclose(factors[0], 2.0 / (8.0 * 9 / 16))
        assert factors[1] == 0.25  # the vertical factor is manimgl's and stays as it was

    def test_palette_swatches_are_written_as_a_tuple(self) -> None:
        settings = SceneSettings(swatches=("#FFFFFF", "#FF0000"))
        result = apply_block(SOURCE, "Intro", settings)
        intro = _run_class(result, "Intro")
        assert intro.STUDIO_PALETTE == ("#FFFFFF", "#FF0000")  # type: ignore[attr-defined]

    def test_an_existing_init_is_a_conflict(self) -> None:
        source = SOURCE.replace(
            "class Intro(Scene):\n",
            "class Intro(Scene):\n"
            "    def __init__(self, **kwargs):\n        super().__init__(**kwargs)\n",
        )
        with pytest.raises(SettingsConflictError, match="__init__"):
            apply_block(source, "Intro", PORTRAIT)

    def test_a_user_setup_method_is_left_alone(self) -> None:
        source = SOURCE.replace(
            "class Intro(Scene):\n",
            "class Intro(Scene):\n    def setup(self):\n        pass\n",
        )
        result = apply_block(source, "Intro", PORTRAIT)
        assert "def setup(self):" in result
        assert has_block(result)

    def test_docstring_stays_first(self) -> None:
        source = SOURCE.replace(
            "class Intro(Scene):\n",
            'class Intro(Scene):\n    """Opening shot."""\n',
        )
        result = apply_block(source, "Intro", PORTRAIT)
        intro = _run_class(result, "Intro")
        assert intro.__doc__ == "Opening shot."

    def test_two_space_indentation_is_respected(self) -> None:
        source = "class Intro(Scene):\n  def construct(self):\n    pass\n"
        result = apply_block(source, "Intro", SceneSettings())
        assert f"\n  {BLOCK_BEGIN}" in result
        _run_class(result, "Intro")

    def test_decorated_first_statement_keeps_its_decorator(self) -> None:
        source = "class Intro(Scene):\n    @staticmethod\n    def helper():\n        return 1\n"
        result = apply_block(source, "Intro", SceneSettings())
        intro = _run_class(result, "Intro")
        assert intro.helper() == 1

    def test_crlf_line_endings_are_preserved(self) -> None:
        result = apply_block(SOURCE.replace("\n", "\r\n"), "Intro", PORTRAIT)
        assert "\r\n" in result and "\n" not in result.replace("\r\n", "")

    def test_missing_class_is_an_error(self) -> None:
        with pytest.raises(SettingsError, match="no class named 'Nope'"):
            apply_block(SOURCE, "Nope", PORTRAIT)

    def test_syntax_error_is_reported_without_writing(self) -> None:
        with pytest.raises(SettingsError, match="syntax error"):
            apply_block("class Intro(Scene:\n", "Intro", PORTRAIT)

    def test_damaged_markers_refuse_to_guess(self) -> None:
        broken = apply_block(SOURCE, "Intro", PORTRAIT).replace(BLOCK_END, "")
        with pytest.raises(SettingsError, match="damaged"):
            apply_block(broken, "Intro", PORTRAIT)

    def test_single_line_class_body_is_refused(self) -> None:
        with pytest.raises(SettingsError, match="own lines"):
            apply_block("class Intro(Scene): pass\n", "Intro", SceneSettings())

    def test_render_block_is_column_zero(self) -> None:
        assert render_block(SceneSettings()).startswith(BLOCK_BEGIN)

    def test_each_class_keeps_its_own_block(self) -> None:
        once = apply_block(TWO_CLASSES, "Intro", PORTRAIT)
        both = apply_block(once, "Other", SceneSettings(width=720, height=1280))
        assert both.count(BLOCK_BEGIN) == 2
        again = apply_block(both, "Intro", SceneSettings())
        assert again.count(BLOCK_BEGIN) == 2
        assert "(720, 1280)" in again

    def test_removing_one_class_block_leaves_the_other(self) -> None:
        once = apply_block(TWO_CLASSES, "Intro", PORTRAIT)
        both = apply_block(once, "Other", SceneSettings(width=720, height=1280))
        removed = remove_class_block(both, "Intro")
        assert removed.count(BLOCK_BEGIN) == 1
        assert "(720, 1280)" in removed
        assert "(1080, 1920)" not in removed

    def test_removing_a_class_without_a_block_changes_nothing(self) -> None:
        assert remove_class_block(TWO_CLASSES, "Intro") == TWO_CLASSES
