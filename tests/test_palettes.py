"""Scene colour palettes and editor layout presets (pure)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from manim_studio.layout_presets import LANDSCAPE, PORTRAIT, PRESETS, preset_for, suggest_layout
from manim_studio.palettes import (
    BUILTIN_PALETTES,
    MIN_CONTRAST,
    Palette,
    PaletteError,
    contrast_ratio,
    find_palette,
    load_palette_file,
    palette_from_dict,
    relative_luminance,
)


class TestContrast:
    def test_black_on_white_is_the_maximum(self) -> None:
        assert contrast_ratio("#000000", "#FFFFFF") == pytest.approx(21.0)

    def test_identical_colours_have_ratio_one(self) -> None:
        assert contrast_ratio("#336699", "#336699") == pytest.approx(1.0)

    def test_luminance_endpoints(self) -> None:
        assert relative_luminance("#000000") == 0.0
        assert relative_luminance("#FFFFFF") == pytest.approx(1.0)


class TestBuiltinPalettes:
    @pytest.mark.parametrize("palette", BUILTIN_PALETTES, ids=lambda p: p.key)
    def test_every_swatch_reads_on_its_background(self, palette: Palette) -> None:
        assert palette.low_contrast() == ()
        for colour in palette.swatches:
            assert contrast_ratio(colour, palette.background) >= MIN_CONTRAST

    def test_keys_are_unique(self) -> None:
        keys = [palette.key for palette in BUILTIN_PALETTES]
        assert len(keys) == len(set(keys))

    def test_find_palette(self) -> None:
        assert find_palette("ocean").title == "Ocean"
        with pytest.raises(PaletteError):
            find_palette("nope")

    def test_low_contrast_is_reported(self) -> None:
        dull = Palette("dull", "Dull", "#777777", ("#808080",))
        assert dull.low_contrast() == ("#808080",)


class TestUserPalettes:
    def test_round_trip_from_json_file(self, tmp_path: Path) -> None:
        path = tmp_path / "brand.json"
        path.write_text(
            json.dumps(
                {
                    "key": "brand",
                    "title": "Brand",
                    "background": "#101820",
                    "swatches": ["#ffffff", "#F2AA4C"],
                }
            ),
            encoding="utf-8",
        )
        palette = load_palette_file(path)
        assert palette == Palette("brand", "Brand", "#101820", ("#FFFFFF", "#F2AA4C"))

    @pytest.mark.parametrize(
        "data",
        [
            [],
            {"title": "No key", "background": "#000000", "swatches": ["#FFFFFF"]},
            {"key": "k", "background": "#000000", "swatches": []},
            {"key": "k", "background": "black", "swatches": ["#FFFFFF"]},
            {"key": "k", "background": "#000000", "swatches": ["white"]},
        ],
    )
    def test_invalid_palettes_are_rejected(self, data: object) -> None:
        with pytest.raises(PaletteError):
            palette_from_dict(data)

    def test_unreadable_file_is_a_palette_error(self, tmp_path: Path) -> None:
        bad = tmp_path / "bad.json"
        bad.write_text("{not json", encoding="utf-8")
        with pytest.raises(PaletteError):
            load_palette_file(bad)


class TestLayoutPresets:
    def test_landscape_and_portrait_are_both_offered(self) -> None:
        assert [preset.key for preset in PRESETS] == [LANDSCAPE, PORTRAIT]

    def test_portrait_gives_the_timeline_more_height_than_landscape(self) -> None:
        landscape = preset_for(LANDSCAPE)
        portrait = preset_for(PORTRAIT)
        assert portrait.vertical[1] > landscape.vertical[1]

    def test_unknown_preset_is_a_key_error(self) -> None:
        with pytest.raises(KeyError):
            preset_for("sideways")

    @pytest.mark.parametrize(
        ("width", "height", "expected"),
        [(1920, 1080, LANDSCAPE), (1080, 1920, PORTRAIT), (1080, 1080, LANDSCAPE)],
    )
    def test_suggestion_follows_orientation(self, width: int, height: int, expected: str) -> None:
        assert suggest_layout(width, height) == expected
