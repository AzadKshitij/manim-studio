"""Editor panel arrangements for landscape and portrait scenes (pure, stdlib only).

Landscape keeps the code beside the preview with the timeline across the bottom.  Portrait
gives the preview the full height on the right and stacks the timeline under the code, so
a tall phone-shaped frame has room to show.  The layout itself is built by the editor;
this module holds the choices and the weights so they can be tested without Qt.
"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = ["LANDSCAPE", "PORTRAIT", "PRESETS", "LayoutPreset", "preset_for", "suggest_layout"]

LANDSCAPE = "landscape"
PORTRAIT = "portrait"


@dataclass(frozen=True, slots=True)
class LayoutPreset:
    """Splitter weights for one arrangement.

    ``horizontal`` is the split between the main columns, and ``vertical`` is the split
    between the code and the timeline area (landscape: above and below the full width;
    portrait: inside the left column).
    """

    key: str
    title: str
    horizontal: tuple[int, int]
    vertical: tuple[int, int]


PRESETS: tuple[LayoutPreset, ...] = (
    LayoutPreset(LANDSCAPE, "Landscape", horizontal=(3, 2), vertical=(4, 1)),
    LayoutPreset(PORTRAIT, "Portrait", horizontal=(1, 1), vertical=(3, 2)),
)


def preset_for(key: str) -> LayoutPreset:
    for preset in PRESETS:
        if preset.key == key:
            return preset
    raise KeyError(f"unknown layout preset {key!r}")


def suggest_layout(width: int, height: int) -> str:
    """The preset that suits a frame of this size."""
    return PORTRAIT if height > width else LANDSCAPE
