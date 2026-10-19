"""The scene settings dialog: presets, rotation, palettes and what it hands back (Qt)."""

from __future__ import annotations

import pytest

from manim_studio.editor.scene_settings_dialog import SceneSettingsDialog
from manim_studio.palettes import BUILTIN_PALETTES, find_palette
from manim_studio.scene_settings import SceneSettings


@pytest.fixture
def dialog(qtbot) -> SceneSettingsDialog:
    widget = SceneSettingsDialog(
        None,
        scene_name="Intro",
        settings=SceneSettings(),
        palettes=BUILTIN_PALETTES,
    )
    qtbot.addWidget(widget)
    return widget


def _choose(combo, key: str) -> None:
    combo.setCurrentIndex(combo.findData(key))


def test_opens_with_the_settings_it_was_given(dialog: SceneSettingsDialog) -> None:
    assert dialog.settings() == SceneSettings()
    assert dialog._preset_combo.currentData() == "1080p"
    assert not dialog.use_as_default()


def test_choosing_a_preset_sets_the_size(dialog: SceneSettingsDialog) -> None:
    _choose(dialog._preset_combo, "portrait-1080p")
    settings = dialog.settings()
    assert (settings.width, settings.height) == (1080, 1920)
    assert settings.orientation == "portrait"


def test_editing_the_size_switches_to_custom(dialog: SceneSettingsDialog) -> None:
    dialog._width.setValue(900)
    assert dialog._preset_combo.currentData() == "custom"
    assert dialog.settings().width == 900


def test_rotate_swaps_width_and_height(dialog: SceneSettingsDialog) -> None:
    dialog._rotate.click()
    settings = dialog.settings()
    assert (settings.width, settings.height) == (1080, 1920)
    assert dialog._preset_combo.currentData() == "portrait-1080p"


def test_palette_fills_background_and_swatches(dialog: SceneSettingsDialog) -> None:
    ocean = find_palette("ocean")
    _choose(dialog._palette_combo, "ocean")
    settings = dialog.settings()
    assert settings.palette == "ocean"
    assert settings.background_color == ocean.background
    assert settings.swatches == ocean.swatches


def test_no_palette_clears_the_swatches(dialog: SceneSettingsDialog) -> None:
    _choose(dialog._palette_combo, "ocean")
    _choose(dialog._palette_combo, "")
    settings = dialog.settings()
    assert settings.palette == ""
    assert settings.swatches == ()


def test_frame_rate_and_opacity_are_part_of_the_result(dialog: SceneSettingsDialog) -> None:
    dialog._fps.setValue(60)
    dialog._opacity.setValue(0.5)
    settings = dialog.settings()
    assert settings.fps == 60
    assert settings.background_opacity == pytest.approx(0.5)


def test_use_as_default_is_reported(dialog: SceneSettingsDialog) -> None:
    dialog._default_check.setChecked(True)
    assert dialog.use_as_default() is True
