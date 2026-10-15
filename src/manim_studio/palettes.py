"""Scene colour palettes: a background plus swatches a scene can draw with (pure, stdlib only).

These are scene colours, not editor colours; the editor's look is :mod:`manim_studio.theme`
(ADR 0008).  A palette is copied into a scene's settings when it is picked, so the scene
keeps working if the palette file is later changed or removed.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from manim_studio.scene_settings import SettingsError, normalize_hex

__all__ = [
    "BUILTIN_PALETTES",
    "MIN_CONTRAST",
    "Palette",
    "PaletteError",
    "contrast_ratio",
    "find_palette",
    "load_palette_file",
    "palette_from_dict",
    "relative_luminance",
    "user_palettes_dir",
]

# WCAG 2.1 non-text contrast: strokes and fills must stand out from the background.
MIN_CONTRAST = 3.0


class PaletteError(ValueError):
    """Raised for palette data that cannot be used."""


def relative_luminance(colour: str) -> float:
    """WCAG relative luminance of a ``#RRGGBB`` colour, 0 (black) to 1 (white)."""
    channels = []
    for start in (1, 3, 5):
        value = int(colour[start : start + 2], 16) / 255
        channels.append(value / 12.92 if value <= 0.03928 else ((value + 0.055) / 1.055) ** 2.4)
    red, green, blue = channels
    return 0.2126 * red + 0.7152 * green + 0.0722 * blue


def contrast_ratio(first: str, second: str) -> float:
    """WCAG contrast ratio between two colours, from 1 (identical) to 21 (black on white)."""
    lighter, darker = sorted((relative_luminance(first), relative_luminance(second)), reverse=True)
    return (lighter + 0.05) / (darker + 0.05)


@dataclass(frozen=True, slots=True)
class Palette:
    """A named background and the swatches meant to read clearly on it."""

    key: str
    title: str
    background: str
    swatches: tuple[str, ...]

    def low_contrast(self) -> tuple[str, ...]:
        """Swatches that fall below :data:`MIN_CONTRAST` on the background."""
        return tuple(
            colour
            for colour in self.swatches
            if contrast_ratio(colour, self.background) < MIN_CONTRAST
        )


BUILTIN_PALETTES: tuple[Palette, ...] = (
    Palette(
        "chalkboard",
        "Chalkboard",
        "#2B3A33",
        ("#F2F2F2", "#F4D35E", "#7FD1AE", "#F28B82", "#8AB4F8"),
    ),
    Palette(
        "paper",
        "Paper",
        "#FAF7F0",
        ("#1F2933", "#2F6DB5", "#B4530A", "#15803D", "#7C3AED"),
    ),
    Palette(
        "ocean",
        "Ocean",
        "#0B1F33",
        ("#E6F1FF", "#64CCC5", "#FFD166", "#EF476F", "#7FB8E6"),
    ),
    Palette(
        "ember",
        "Ember",
        "#1A1210",
        ("#FFF3E0", "#FF7F11", "#FFCA3A", "#E8604C", "#C4A1FF"),
    ),
    Palette(
        "midnight",
        "Midnight",
        "#1E1E2E",
        ("#CDD6F4", "#89B4FA", "#F38BA8", "#A6E3A1", "#F9E2AF"),
    ),
    Palette(
        "mono",
        "Mono",
        "#111111",
        ("#FFFFFF", "#BBBBBB", "#888888", "#DDDDDD", "#666666"),
    ),
)


def find_palette(key: str, palettes: tuple[Palette, ...] = BUILTIN_PALETTES) -> Palette:
    for palette in palettes:
        if palette.key == key:
            return palette
    raise PaletteError(f"unknown palette {key!r}")


def palette_from_dict(data: object) -> Palette:
    """Read a palette from its JSON form: ``{"key", "title", "background", "swatches"}``."""
    if not isinstance(data, dict):
        raise PaletteError("a palette must be a JSON object")
    key = data.get("key")
    title = data.get("title", key)
    swatches = data.get("swatches")
    if not isinstance(key, str) or not key:
        raise PaletteError("a palette needs a non-empty 'key'")
    if not isinstance(title, str):
        raise PaletteError("a palette 'title' must be text")
    if not isinstance(swatches, list) or not swatches:
        raise PaletteError("a palette needs a non-empty 'swatches' list")
    try:
        background = normalize_hex(data.get("background"))
        colours = tuple(normalize_hex(item) for item in swatches)
    except SettingsError as exc:
        raise PaletteError(str(exc)) from exc
    return Palette(key, title, background, colours)


def load_palette_file(path: str | Path) -> Palette:
    """Read one palette JSON file; raises :class:`PaletteError` on any problem."""
    target = Path(path)
    try:
        raw: Any = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PaletteError(f"cannot read {target.name}: {exc}") from exc
    return palette_from_dict(raw)


def user_palettes_dir() -> Path:
    """Where user palettes live: ``~/.manim-studio/palettes``."""
    return Path.home() / ".manim-studio" / "palettes"
