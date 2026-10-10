"""Editor colour themes: data model, built-in themes and the theme-file format.

A theme is a small set of base colours plus a few metrics.  Everything else (hover,
pressed, selection, soft status fills) is derived in :func:`derive`, so a theme file
only has to carry the base values.  The same JSON is produced by the design-system
page (``docs/design/studio-design-system.html``) and read back by :func:`load_theme`.

Pure module: no Qt, no manimlib.  The Qt side lives in ``editor/styling.py``.
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Mapping
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import Any

__all__ = [
    "BUILTIN_THEMES",
    "CHALKBOARD",
    "LANE_COUNT",
    "THEME_FORMAT",
    "Derived",
    "Theme",
    "ThemeColors",
    "ThemeError",
    "ThemeMetrics",
    "ThemeSyntax",
    "contrast_ratio",
    "current_theme",
    "derive",
    "discover_themes",
    "ink_on",
    "load_theme",
    "mix",
    "normalize_hex",
    "save_theme",
    "set_current_theme",
    "theme_from_dict",
    "theme_to_dict",
    "themes_dir",
    "with_alpha",
]

THEME_FORMAT = "manim-studio-theme"
THEME_VERSION = 1
LANE_COUNT = 6

_HEX_RE = re.compile(r"^#?([0-9a-fA-F]{3}|[0-9a-fA-F]{6})$")


class ThemeError(ValueError):
    """A theme file could not be read."""


# ----------------------------------------------------------------------
# Colour helpers
# ----------------------------------------------------------------------


def normalize_hex(value: object) -> str | None:
    """Return ``#rrggbb`` (lower case) for a hex colour string, or ``None``."""
    if not isinstance(value, str):
        return None
    match = _HEX_RE.match(value.strip())
    if match is None:
        return None
    digits = match.group(1).lower()
    if len(digits) == 3:
        digits = "".join(ch * 2 for ch in digits)
    return f"#{digits}"


def _rgb(colour: str) -> tuple[int, int, int]:
    value = int(colour[1:], 16)
    return (value >> 16) & 0xFF, (value >> 8) & 0xFF, value & 0xFF


def _to_hex(red: float, green: float, blue: float) -> str:
    parts = (max(0, min(255, round(channel))) for channel in (red, green, blue))
    return "#" + "".join(f"{part:02x}" for part in parts)


def mix(first: str, second: str, amount: float) -> str:
    """Linear blend: ``amount`` 0 gives ``first``, 1 gives ``second``."""
    a, b = _rgb(first), _rgb(second)
    return _to_hex(*(x + (y - x) * amount for x, y in zip(a, b, strict=True)))


def with_alpha(colour: str, alpha: float) -> str:
    """CSS ``rgba(...)`` string, usable in Qt style sheets."""
    red, green, blue = _rgb(colour)
    return f"rgba({red}, {green}, {blue}, {round(alpha * 255)})"


def luminance(colour: str) -> float:
    """WCAG relative luminance."""

    def channel(value: int) -> float:
        scaled = value / 255
        return scaled / 12.92 if scaled <= 0.03928 else ((scaled + 0.055) / 1.055) ** 2.4

    red, green, blue = (channel(v) for v in _rgb(colour))
    return 0.2126 * red + 0.7152 * green + 0.0722 * blue


def contrast_ratio(first: str, second: str) -> float:
    """WCAG contrast ratio between two colours (1 to 21)."""
    high, low = sorted((luminance(first), luminance(second)), reverse=True)
    return (high + 0.05) / (low + 0.05)


def ink_on(background: str) -> str:
    """Near-black or white, whichever reads better on ``background``."""
    dark, light = "#0b0d10", "#ffffff"
    return dark if contrast_ratio(background, dark) >= contrast_ratio(background, light) else light


# ----------------------------------------------------------------------
# Model
# ----------------------------------------------------------------------


@dataclass(frozen=True)
class ThemeColors:
    bg: str
    surface: str
    raised: str
    editor: str
    preview: str
    border: str
    border_strong: str
    text: str
    muted: str
    faint: str
    accent: str
    on_accent: str
    playhead: str
    ok: str
    warn: str
    err: str
    icon: str
    icon_active: str
    icon_disabled: str


@dataclass(frozen=True)
class ThemeSyntax:
    keyword: str
    string: str
    number: str
    comment: str
    function: str
    class_: str
    builtin: str


@dataclass(frozen=True)
class ThemeMetrics:
    radius: int = 6
    control_height: int = 28
    density: float = 1.0
    font_size: int = 13
    editor_font_size: int = 14


@dataclass(frozen=True)
class Theme:
    name: str
    mode: str
    colors: ThemeColors
    syntax: ThemeSyntax
    lanes: tuple[str, ...]
    metrics: ThemeMetrics = field(default_factory=ThemeMetrics)

    @property
    def is_dark(self) -> bool:
        return self.mode == "dark"


@dataclass(frozen=True)
class Derived:
    """Colours computed from a theme's base colours (never stored in a theme file)."""

    hover: str
    pressed: str
    accent_hover: str
    accent_soft: str
    selection: str
    caret_line: str
    list_selection: str
    ring: str
    ok_soft: str
    warn_soft: str
    err_soft: str
    err_line: str


def derive(theme: Theme) -> Derived:
    c = theme.colors
    pole = "#ffffff" if theme.is_dark else "#000000"
    return Derived(
        hover=mix(c.raised, c.text, 0.07),
        pressed=mix(c.raised, c.text, 0.13),
        accent_hover=mix(c.accent, pole, 0.14),
        accent_soft=with_alpha(c.accent, 0.16),
        selection=mix(c.editor, c.accent, 0.28),
        caret_line=mix(c.editor, c.text, 0.05),
        list_selection=mix(c.surface, c.accent, 0.32),
        ring=with_alpha(c.accent, 0.55),
        ok_soft=with_alpha(c.ok, 0.15),
        warn_soft=with_alpha(c.warn, 0.15),
        err_soft=with_alpha(c.err, 0.15),
        err_line=with_alpha(c.err, 0.45),
    )


# ----------------------------------------------------------------------
# Built-in themes
# ----------------------------------------------------------------------

CHALKBOARD = Theme(
    name="Chalkboard",
    mode="dark",
    colors=ThemeColors(
        bg="#0f1217",
        surface="#161a21",
        raised="#1f242d",
        editor="#12151b",
        preview="#080a0d",
        border="#262c36",
        border_strong="#3a4352",
        text="#e4e8ee",
        muted="#9aa4b2",
        faint="#6b7586",
        accent="#58c4dd",
        on_accent="#07232b",
        playhead="#ffd866",
        ok="#83c167",
        warn="#f0a93c",
        err="#fc6255",
        icon="#c3cbd6",
        icon_active="#58c4dd",
        icon_disabled="#566073",
    ),
    syntax=ThemeSyntax(
        keyword="#f2907a",
        string="#a8d98c",
        number="#ffd866",
        comment="#6f7b8d",
        function="#58c4dd",
        class_="#8fb8ff",
        builtin="#c9a6f5",
    ),
    lanes=("#58c4dd", "#83c167", "#ffd866", "#fc6255", "#c59df0", "#ff9f68"),
)

PAPER = Theme(
    name="Paper",
    mode="light",
    colors=ThemeColors(
        bg="#e8ebf0",
        surface="#f5f6f8",
        raised="#ffffff",
        editor="#ffffff",
        preview="#14171c",
        border="#d3d8e0",
        border_strong="#b4bcc8",
        text="#1b1f27",
        muted="#505b6b",
        faint="#7c8696",
        accent="#0a6f8c",
        on_accent="#ffffff",
        playhead="#b87a00",
        ok="#2f7d32",
        warn="#a86400",
        err="#c62f25",
        icon="#3a4454",
        icon_active="#0a6f8c",
        icon_disabled="#9aa3b2",
    ),
    syntax=ThemeSyntax(
        keyword="#c2410c",
        string="#2f7d32",
        number="#8a5a00",
        comment="#6b7686",
        function="#0a6f8c",
        class_="#1f5fbf",
        builtin="#7c3aed",
    ),
    lanes=("#0a6f8c", "#3f8f3a", "#c79200", "#d4483b", "#7c5cc4", "#d97a2b"),
)

NIGHT_VIOLET = Theme(
    name="Night Violet",
    mode="dark",
    colors=ThemeColors(
        bg="#110f1a",
        surface="#181526",
        raised="#221e34",
        editor="#141120",
        preview="#09080f",
        border="#2b2640",
        border_strong="#413a5e",
        text="#e8e5f3",
        muted="#a29dbb",
        faint="#7a7596",
        accent="#a78bfa",
        on_accent="#1a0f3a",
        playhead="#ffc857",
        ok="#7dd3a0",
        warn="#f5b14c",
        err="#ff7a8a",
        icon="#c4bfd9",
        icon_active="#a78bfa",
        icon_disabled="#5f5a7d",
    ),
    syntax=ThemeSyntax(
        keyword="#ff8fb1",
        string="#9be3b5",
        number="#ffc857",
        comment="#7f7a9e",
        function="#a78bfa",
        class_="#7cc4ff",
        builtin="#f5a9e1",
    ),
    lanes=("#a78bfa", "#7cc4ff", "#7dd3a0", "#ffc857", "#ff8fb1", "#f5a9e1"),
    metrics=ThemeMetrics(radius=8),
)

DEEP_OCEAN = Theme(
    name="Deep Ocean",
    mode="dark",
    colors=ThemeColors(
        bg="#0b1220",
        surface="#101a2c",
        raised="#18243a",
        editor="#0d1626",
        preview="#060b14",
        border="#1f2d47",
        border_strong="#34476a",
        text="#dfe8f5",
        muted="#92a4c0",
        faint="#637596",
        accent="#5b8def",
        on_accent="#06122b",
        playhead="#ffb703",
        ok="#5ad28c",
        warn="#f5a524",
        err="#ff6b6b",
        icon="#b6c6de",
        icon_active="#7aa7ff",
        icon_disabled="#4d5f80",
    ),
    syntax=ThemeSyntax(
        keyword="#c792ea",
        string="#7fd6a4",
        number="#ffcb6b",
        comment="#6479a0",
        function="#82aaff",
        class_="#4cc9f0",
        builtin="#f78c6c",
    ),
    lanes=("#5b8def", "#4cc9f0", "#7fd6a4", "#ffcb6b", "#c792ea", "#f78c6c"),
    metrics=ThemeMetrics(radius=8),
)

EMBER = Theme(
    name="Ember",
    mode="dark",
    colors=ThemeColors(
        bg="#14100e",
        surface="#1c1613",
        raised="#281f1a",
        editor="#17120f",
        preview="#0a0807",
        border="#33271f",
        border_strong="#4d3b2e",
        text="#f0e6dc",
        muted="#b3a393",
        faint="#85766a",
        accent="#ff9f45",
        on_accent="#2b1400",
        playhead="#5eead4",
        ok="#8fd16a",
        warn="#ffd166",
        err="#ff6b5e",
        icon="#d6c8ba",
        icon_active="#ffb36b",
        icon_disabled="#6b5a4c",
    ),
    syntax=ThemeSyntax(
        keyword="#ff7b6b",
        string="#b5d96f",
        number="#ffd166",
        comment="#86766a",
        function="#ffa94d",
        class_="#6ec6ff",
        builtin="#d6a8ff",
    ),
    lanes=("#ff9f45", "#ffd166", "#8fd16a", "#5eead4", "#6ec6ff", "#d6a8ff"),
    metrics=ThemeMetrics(radius=6),
)

EVERGREEN = Theme(
    name="Evergreen",
    mode="dark",
    colors=ThemeColors(
        bg="#0c1210",
        surface="#121b18",
        raised="#1a2622",
        editor="#0e1512",
        preview="#060a09",
        border="#1f2e29",
        border_strong="#324a42",
        text="#e1ede7",
        muted="#94aba0",
        faint="#667d72",
        accent="#4fd1a1",
        on_accent="#052018",
        playhead="#f6d55c",
        ok="#7ddc7a",
        warn="#f0b429",
        err="#ff7a6b",
        icon="#bcd3c8",
        icon_active="#6ee7b7",
        icon_disabled="#4b6358",
    ),
    syntax=ThemeSyntax(
        keyword="#f08fa8",
        string="#a5d86e",
        number="#f6d55c",
        comment="#6b8379",
        function="#4fd1a1",
        class_="#7fc8f8",
        builtin="#c3a6f2",
    ),
    lanes=("#4fd1a1", "#7fc8f8", "#f6d55c", "#f08fa8", "#c3a6f2", "#f4a261"),
    metrics=ThemeMetrics(radius=6),
)

GRAPHITE = Theme(
    name="Graphite",
    mode="dark",
    colors=ThemeColors(
        bg="#111111",
        surface="#181818",
        raised="#222222",
        editor="#141414",
        preview="#070707",
        border="#2a2a2a",
        border_strong="#404040",
        text="#ebebeb",
        muted="#a3a3a3",
        faint="#757575",
        accent="#c6f432",
        on_accent="#121a00",
        playhead="#ff7ab6",
        ok="#6fdc8c",
        warn="#ffbf47",
        err="#ff6464",
        icon="#c8c8c8",
        icon_active="#c6f432",
        icon_disabled="#5a5a5a",
    ),
    syntax=ThemeSyntax(
        keyword="#ff7ab6",
        string="#b7e36a",
        number="#ffbf47",
        comment="#7d7d7d",
        function="#7cc7ff",
        class_="#c6a8ff",
        builtin="#ff9e64",
    ),
    lanes=("#c6f432", "#7cc7ff", "#ff7ab6", "#ffbf47", "#c6a8ff", "#ff9e64"),
    metrics=ThemeMetrics(radius=4),
)

DAYLIGHT = Theme(
    name="Daylight",
    mode="light",
    colors=ThemeColors(
        bg="#eef1f6",
        surface="#f7f9fc",
        raised="#ffffff",
        editor="#ffffff",
        preview="#12151b",
        border="#d6dce6",
        border_strong="#b7c1d1",
        text="#172033",
        muted="#4a566b",
        faint="#7a869b",
        accent="#2563eb",
        on_accent="#ffffff",
        playhead="#d97706",
        ok="#15803d",
        warn="#b45309",
        err="#c62828",
        icon="#364257",
        icon_active="#2563eb",
        icon_disabled="#9ba7bb",
    ),
    syntax=ThemeSyntax(
        keyword="#b91c8c",
        string="#15803d",
        number="#b45309",
        comment="#66738a",
        function="#2563eb",
        class_="#0e7490",
        builtin="#7c3aed",
    ),
    lanes=("#2563eb", "#15803d", "#d97706", "#dc2626", "#7c3aed", "#0e7490"),
    metrics=ThemeMetrics(radius=8),
)

SAND = Theme(
    name="Sand",
    mode="light",
    colors=ThemeColors(
        bg="#ece6dc",
        surface="#f6f1e8",
        raised="#fffdf8",
        editor="#fffdf8",
        preview="#1c1815",
        border="#d9cfbf",
        border_strong="#bfb19a",
        text="#2a2118",
        muted="#5c4f40",
        faint="#8a7b69",
        accent="#b4540a",
        on_accent="#ffffff",
        playhead="#0f766e",
        ok="#3f7d20",
        warn="#9a5b00",
        err="#b91c1c",
        icon="#4a3d2f",
        icon_active="#b4540a",
        icon_disabled="#a89b86",
    ),
    syntax=ThemeSyntax(
        keyword="#a3341f",
        string="#3f7d20",
        number="#8a5a00",
        comment="#7a6b58",
        function="#0f766e",
        class_="#1d5fa8",
        builtin="#7a3fb0",
    ),
    lanes=("#b4540a", "#3f7d20", "#0f766e", "#1d5fa8", "#7a3fb0", "#a3341f"),
    metrics=ThemeMetrics(radius=6),
)

BUILTIN_THEMES: dict[str, Theme] = {
    # dark
    "chalkboard": CHALKBOARD,
    "night-violet": NIGHT_VIOLET,
    "deep-ocean": DEEP_OCEAN,
    "ember": EMBER,
    "evergreen": EVERGREEN,
    "graphite": GRAPHITE,
    # light
    "paper": PAPER,
    "daylight": DAYLIGHT,
    "sand": SAND,
}

# ----------------------------------------------------------------------
# JSON (de)serialisation
# ----------------------------------------------------------------------

# Theme files use camelCase keys (shared with the design-system page).
_COLOR_KEYS = {
    "border_strong": "borderStrong",
    "on_accent": "onAccent",
    "icon_active": "iconActive",
    "icon_disabled": "iconDisabled",
}
_SYNTAX_KEYS = {"class_": "class"}
_METRIC_KEYS = {
    "control_height": "controlHeight",
    "font_size": "fontSize",
    "editor_font_size": "editorFontSize",
}
_METRIC_LIMITS: dict[str, tuple[float, float]] = {
    "radius": (0, 12),
    "control_height": (22, 36),
    "density": (0.75, 1.25),
    "font_size": (11, 16),
    "editor_font_size": (11, 18),
}


def theme_to_dict(theme: Theme) -> dict[str, Any]:
    """Serialise ``theme`` to the JSON shape used by theme files."""

    def rename(values: Mapping[str, Any], keys: Mapping[str, str]) -> dict[str, Any]:
        return {keys.get(name, name): value for name, value in values.items()}

    return {
        "format": THEME_FORMAT,
        "version": THEME_VERSION,
        "name": theme.name,
        "mode": theme.mode,
        "colors": rename(asdict(theme.colors), _COLOR_KEYS),
        "syntax": rename(asdict(theme.syntax), _SYNTAX_KEYS),
        "lanes": list(theme.lanes),
        "metrics": rename(asdict(theme.metrics), _METRIC_KEYS),
    }


def theme_from_dict(data: object, *, base: Theme = CHALKBOARD) -> Theme:
    """Build a theme from parsed JSON.

    Missing or invalid values fall back to ``base``, so a partial file still loads.
    Raises :class:`ThemeError` when the data is not a Manim Studio theme at all.
    """
    if not isinstance(data, dict) or data.get("format") != THEME_FORMAT:
        raise ThemeError(f'not a Manim Studio theme (missing "format": "{THEME_FORMAT}")')

    def merge(current: Any, given: object, keys: Mapping[str, str]) -> Any:
        if not isinstance(given, dict):
            return current
        updates: dict[str, str] = {}
        for attr in asdict(current):
            colour = normalize_hex(given.get(keys.get(attr, attr)))
            if colour is not None:
                updates[attr] = colour
        return replace(current, **updates)

    colors = merge(base.colors, data.get("colors"), _COLOR_KEYS)
    given_colors = data.get("colors")
    given_colors = given_colors if isinstance(given_colors, dict) else {}
    # Files written before icon colours existed: derive them from the file's own colours
    # (the base theme's icons could be the wrong polarity for a light/dark file).
    icon_defaults = {
        "icon": mix(colors.text, colors.bg, 0.2),
        "icon_active": colors.accent,
        "icon_disabled": mix(colors.text, colors.bg, 0.6),
    }
    colors = replace(
        colors,
        **{
            attr: value
            for attr, value in icon_defaults.items()
            if normalize_hex(given_colors.get(_COLOR_KEYS.get(attr, attr))) is None
        },
    )
    syntax = merge(base.syntax, data.get("syntax"), _SYNTAX_KEYS)
    lanes = list(base.lanes)
    given_lanes = data.get("lanes")
    if isinstance(given_lanes, list):
        for index, value in enumerate(given_lanes[:LANE_COUNT]):
            colour = normalize_hex(value)
            if colour is not None:
                lanes[index] = colour
    metrics = base.metrics
    given_metrics = data.get("metrics")
    if isinstance(given_metrics, dict):
        updates_m: dict[str, Any] = {}
        for attr, (low, high) in _METRIC_LIMITS.items():
            raw = given_metrics.get(_METRIC_KEYS.get(attr, attr))
            if isinstance(raw, int | float) and not isinstance(raw, bool):
                clamped = max(low, min(high, raw))
                updates_m[attr] = clamped if attr == "density" else round(clamped)
        metrics = replace(metrics, **updates_m)
    name = data.get("name")
    mode = data.get("mode")
    return Theme(
        name=name.strip()[:40] if isinstance(name, str) and name.strip() else "Imported theme",
        mode="light" if mode == "light" else "dark",
        colors=colors,
        syntax=syntax,
        lanes=tuple(lanes),
        metrics=metrics,
    )


def load_theme(path: str | Path) -> Theme:
    """Read a theme file.  Raises :class:`ThemeError` for unreadable or foreign files."""
    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError as exc:
        raise ThemeError(f"cannot read {path}: {exc}") from exc
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ThemeError(f"{path} is not valid JSON: {exc}") from exc
    return theme_from_dict(data)


def save_theme(theme: Theme, path: str | Path) -> None:
    Path(path).write_text(json.dumps(theme_to_dict(theme), indent=2) + "\n", encoding="utf-8")


def themes_dir() -> Path:
    """Folder scanned for user theme files (``MANIM_STUDIO_THEMES`` overrides it)."""
    override = os.environ.get("MANIM_STUDIO_THEMES")
    if override:
        return Path(override)
    return Path.home() / ".manim-studio" / "themes"


def discover_themes(directory: Path | None = None) -> dict[str, Path]:
    """Theme files in ``directory``, keyed by file stem (sorted)."""
    folder = directory if directory is not None else themes_dir()
    if not folder.is_dir():
        return {}
    return {path.stem: path for path in sorted(folder.glob("*.json"))}


# ----------------------------------------------------------------------
# Active theme
# ----------------------------------------------------------------------

_current: Theme = CHALKBOARD


def current_theme() -> Theme:
    """The theme the editor is painting with (Chalkboard until one is applied)."""
    return _current


def set_current_theme(theme: Theme) -> None:
    global _current
    _current = theme
