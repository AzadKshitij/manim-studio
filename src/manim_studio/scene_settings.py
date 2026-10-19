"""Per-scene render settings and the managed block that mirrors them into scene source.

A scene's settings say how it renders: pixel size, frame rate, background and the colours
offered to the scene.  They are stored in ``<scene>.studio.json`` (see
:mod:`manim_studio.sidecar`).  "Apply settings to source" also writes them into a managed
block inside the scene class, so a plain ``manimgl`` run renders the same thing.

Only the text between the block markers is ever rewritten; code the user wrote outside
the block is left alone, and a class that already defines the names the block needs is
reported as a conflict instead of being overwritten.

Pure module: stdlib only (AGENTS.md section 2).
"""

from __future__ import annotations

import ast
import math
import re
import textwrap
from dataclasses import dataclass, replace
from typing import Any

__all__ = [
    "BLOCK_BEGIN",
    "BLOCK_END",
    "DEFAULT_BACKGROUND",
    "DEFAULT_FPS",
    "FRAME_HEIGHT",
    "RESOLUTION_PRESETS",
    "ResolutionPreset",
    "SceneSettings",
    "SettingsConflictError",
    "SettingsError",
    "apply_block",
    "has_block",
    "normalize_hex",
    "remove_block",
    "remove_class_block",
    "render_block",
    "resolution_preset_for",
]

MIN_SIDE = 64
MAX_SIDE = 4096
MIN_FPS = 1
MAX_FPS = 120
MAX_SWATCHES = 12
DEFAULT_WIDTH = 1920
DEFAULT_HEIGHT = 1080
DEFAULT_FPS = 30
DEFAULT_BACKGROUND = "#333333"
# manimgl's default frame height (``sizes.frame_height`` in default_config.yml).
FRAME_HEIGHT = 8.0
# manimgl's frame uniforms are built for 16:9 pixels; other aspects need the correction.
_DEFAULT_ASPECT = 16 / 9

BLOCK_BEGIN = "# --- manim-studio:scene-settings (managed; edit via Scene > Scene Settings) ---"
BLOCK_END = "# --- end manim-studio:scene-settings ---"

_HEX = re.compile(r"#[0-9A-Fa-f]{6}")
_BLOCK_RE = re.compile(
    r"^[ \t]*"
    + re.escape(BLOCK_BEGIN)
    + r"[ \t]*\n.*?^[ \t]*"
    + re.escape(BLOCK_END)
    + r"[ \t]*\n(?:[ \t]*\n)?",
    re.MULTILINE | re.DOTALL,
)


class SettingsError(ValueError):
    """Raised for settings that cannot be rendered, or source that cannot take the block."""


class SettingsConflictError(SettingsError):
    """Raised when the scene class already defines a name the managed block needs."""


def normalize_hex(value: object) -> str:
    """Return ``value`` as an upper-case ``#RRGGBB`` colour, or raise :class:`SettingsError`."""
    if not isinstance(value, str) or not _HEX.fullmatch(value.strip()):
        raise SettingsError(f"colour must look like #RRGGBB, not {value!r}")
    return value.strip().upper()


def _check_int(name: str, value: object, low: int, high: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        raise SettingsError(f"{name} must be a whole number from {low} to {high}, not {value!r}")


@dataclass(frozen=True, slots=True)
class ResolutionPreset:
    """A named pixel size offered in the Scene menu."""

    key: str
    title: str
    width: int
    height: int


RESOLUTION_PRESETS: tuple[ResolutionPreset, ...] = (
    ResolutionPreset("1080p", "1080p landscape (16:9)", 1920, 1080),
    ResolutionPreset("720p", "720p landscape (16:9)", 1280, 720),
    ResolutionPreset("480p", "480p landscape (16:9)", 854, 480),
    ResolutionPreset("4k", "4K landscape (16:9)", 3840, 2160),
    ResolutionPreset("portrait-1080p", "1080p portrait (9:16)", 1080, 1920),
    ResolutionPreset("portrait-720p", "720p portrait (9:16)", 720, 1280),
    ResolutionPreset("square", "Square (1:1)", 1080, 1080),
    ResolutionPreset("feed-4:5", "Feed portrait (4:5)", 1080, 1350),
)


def resolution_preset_for(width: int, height: int) -> ResolutionPreset | None:
    """The preset with exactly this pixel size, if one is offered."""
    for preset in RESOLUTION_PRESETS:
        if (preset.width, preset.height) == (width, height):
            return preset
    return None


@dataclass(frozen=True, slots=True)
class SceneSettings:
    """What one scene renders with.  Immutable; build a new one to change a value."""

    width: int = DEFAULT_WIDTH
    height: int = DEFAULT_HEIGHT
    fps: int = DEFAULT_FPS
    background_color: str = DEFAULT_BACKGROUND
    background_opacity: float = 1.0
    palette: str = ""
    swatches: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _check_int("width", self.width, MIN_SIDE, MAX_SIDE)
        _check_int("height", self.height, MIN_SIDE, MAX_SIDE)
        _check_int("fps", self.fps, MIN_FPS, MAX_FPS)
        opacity = self.background_opacity
        if isinstance(opacity, bool) or not isinstance(opacity, (int, float)):
            raise SettingsError(f"background_opacity must be a number, not {opacity!r}")
        if not 0.0 <= float(opacity) <= 1.0:
            raise SettingsError(f"background_opacity must be between 0 and 1, not {opacity!r}")
        if not isinstance(self.palette, str):
            raise SettingsError("palette must be the name of a palette")
        swatches = tuple(normalize_hex(item) for item in self.swatches)
        if len(swatches) > MAX_SWATCHES:
            raise SettingsError(f"at most {MAX_SWATCHES} swatches are allowed")
        object.__setattr__(self, "background_color", normalize_hex(self.background_color))
        object.__setattr__(self, "background_opacity", float(opacity))
        object.__setattr__(self, "swatches", swatches)

    @property
    def aspect_ratio(self) -> float:
        return self.width / self.height

    @property
    def orientation(self) -> str:
        if self.width == self.height:
            return "square"
        return "portrait" if self.height > self.width else "landscape"

    @property
    def aspect_label(self) -> str:
        divisor = math.gcd(self.width, self.height)
        return f"{self.width // divisor}:{self.height // divisor}"

    @property
    def frame_shape(self) -> tuple[float, float]:
        """The world-units frame: height stays at manimgl's default, width follows the aspect."""
        return (round(FRAME_HEIGHT * self.aspect_ratio, 6), FRAME_HEIGHT)

    @property
    def needs_pixel_fix(self) -> bool:
        """True when the resolution is not 16:9 and manimgl's horizontal scale must be fixed."""
        return not math.isclose(self.aspect_ratio, _DEFAULT_ASPECT)

    def with_size(self, width: int, height: int) -> SceneSettings:
        return replace(self, width=width, height=height)

    def swapped(self) -> SceneSettings:
        """The same settings turned 90 degrees (landscape <-> portrait)."""
        return replace(self, width=self.height, height=self.width)

    def camera_config(self) -> dict[str, Any]:
        """The keyword dict manimgl's ``Camera`` understands for these settings."""
        return {
            "resolution": (self.width, self.height),
            "fps": self.fps,
            "background_color": self.background_color,
            "background_opacity": self.background_opacity,
            "frame_config": {"frame_shape": self.frame_shape},
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "width": self.width,
            "height": self.height,
            "fps": self.fps,
            "background_color": self.background_color,
            "background_opacity": self.background_opacity,
            "palette": self.palette,
            "swatches": list(self.swatches),
        }

    @classmethod
    def from_dict(cls, data: object) -> SceneSettings:
        """Read settings written by :meth:`to_dict`; missing keys take the defaults."""
        if not isinstance(data, dict):
            raise SettingsError("scene settings must be a JSON object")
        defaults = cls()
        swatches = data.get("swatches", [])
        if not isinstance(swatches, list):
            raise SettingsError("'swatches' must be a list of colours")
        return cls(
            width=data.get("width", defaults.width),
            height=data.get("height", defaults.height),
            fps=data.get("fps", defaults.fps),
            background_color=data.get("background_color", defaults.background_color),
            background_opacity=data.get("background_opacity", defaults.background_opacity),
            palette=data.get("palette", defaults.palette),
            swatches=tuple(swatches),
        )


# The aspect correction sits inside the generated __init__ and is only emitted for non-16:9
# sizes: manimgl's frame uniforms assume 16:9 pixels, so other resolutions need the horizontal
# scale fixed.  Written at the __init__ body's indentation (four spaces).
_PIXEL_FIX: tuple[str, ...] = (
    "    # manimgl scales x by the 16:9 frame width; use this resolution's aspect ratio.",
    "    from manimlib.constants import FRAME_HEIGHT",
    "    camera = self.camera",
    "    draw_uniforms = camera.refresh_uniforms",
    "",
    "    def refresh_uniforms() -> None:",
    "        draw_uniforms()",
    '        factors = list(camera.uniforms["frame_rescale_factors"])',
    "        factors[0] = 2.0 / (FRAME_HEIGHT * camera.get_aspect_ratio())",
    '        camera.uniforms["frame_rescale_factors"] = tuple(factors)',
    "",
    "    camera.refresh_uniforms = refresh_uniforms",
)


def render_block(settings: SceneSettings) -> str:
    """The managed block for ``settings`` at column 0; :func:`apply_block` indents it.

    The block is an ``__init__`` override, so plain ``manimgl`` runs use these settings.  A
    camera_config that a caller passes in (the editor, the export launcher) still wins.
    """
    frame_width, frame_height = settings.frame_shape
    lines = [BLOCK_BEGIN]
    if settings.swatches:
        quoted = ", ".join(f'"{colour}"' for colour in settings.swatches)
        lines += [f"STUDIO_PALETTE = ({quoted},)", ""]
    lines += [
        "def __init__(self, **kwargs) -> None:",
        "    # Scene settings from Manim Studio (managed block; change them in the Scene menu).",
        '    kwargs["camera_config"] = {',
        f'        "resolution": ({settings.width}, {settings.height}),',
        f'        "fps": {settings.fps},',
        f'        "background_color": "{settings.background_color}",',
        f'        "background_opacity": {settings.background_opacity!r},',
        f'        "frame_config": {{"frame_shape": ({frame_width!r}, {frame_height!r})}},',
        '        **kwargs.get("camera_config", {}),',
        "    }",
        "    super().__init__(**kwargs)",
    ]
    if settings.needs_pixel_fix:
        lines += ["", *_PIXEL_FIX]
    lines.append(BLOCK_END)
    return "\n".join(lines) + "\n"


def has_block(source: str) -> bool:
    return BLOCK_BEGIN in source


def remove_block(source: str) -> str:
    """``source`` without its managed block; raises if the markers are unbalanced."""
    if source.count(BLOCK_BEGIN) != source.count(BLOCK_END):
        raise SettingsError(
            "the managed settings block is damaged: one of its marker lines is missing"
        )
    return _BLOCK_RE.sub("", source)


def _remove_class_block_lf(text: str, scene_name: str) -> str:
    """Remove the managed block inside class ``scene_name`` from LF-only ``text``."""
    if text.count(BLOCK_BEGIN) != text.count(BLOCK_END):
        raise SettingsError(
            "the managed settings block is damaged: one of its marker lines is missing"
        )
    if BLOCK_BEGIN not in text:
        return text
    cls = _find_class(_parse(text), scene_name)
    last_line = cls.end_lineno or cls.lineno
    spans = [
        match.span()
        for match in _BLOCK_RE.finditer(text)
        if cls.lineno <= text.count("\n", 0, match.start()) + 1 <= last_line
    ]
    for start, stop in reversed(spans):
        text = text[:start] + text[stop:]
    return text


def remove_class_block(source: str, scene_name: str) -> str:
    """``source`` without the managed block inside class ``scene_name``; others stay."""
    crlf = "\r\n" in source
    text = _remove_class_block_lf(source.replace("\r\n", "\n"), scene_name)
    return text.replace("\n", "\r\n") if crlf else text


def _parse(text: str) -> ast.Module:
    try:
        return ast.parse(text)
    except SyntaxError as exc:
        raise SettingsError(f"the scene has a syntax error at line {exc.lineno}") from exc


def _find_class(tree: ast.Module, scene_name: str) -> ast.ClassDef:
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == scene_name:
            return node
    raise SettingsError(f"no class named {scene_name!r} in this file")


def _defined_names(cls: ast.ClassDef) -> set[str]:
    names: set[str] = set()
    for node in cls.body:
        if isinstance(node, ast.Assign):
            names.update(t.id for t in node.targets if isinstance(t, ast.Name))
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names.add(node.target.id)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            names.add(node.name)
    return names


def _needed_names(settings: SceneSettings) -> set[str]:
    needed = {"__init__"}
    if settings.swatches:
        needed.add("STUDIO_PALETTE")
    return needed


def _insert_point(lines: list[str], cls: ast.ClassDef) -> tuple[int, str]:
    """The 0-based line index to insert at, and the indentation the class body uses."""
    first = cls.body[0]
    if (
        isinstance(first, ast.Expr)
        and isinstance(first.value, ast.Constant)
        and isinstance(first.value.value, str)
    ):
        # After a docstring; the docstring's own line gives the body indentation.
        assert first.end_lineno is not None
        return first.end_lineno, _leading_space(lines[first.lineno - 1])
    start = min([first.lineno, *(d.lineno for d in getattr(first, "decorator_list", []))])
    if start == cls.lineno:
        raise SettingsError(f"write the body of {cls.name} on its own lines first")
    return start - 1, _leading_space(lines[start - 1])


def _leading_space(line: str) -> str:
    return line[: len(line) - len(line.lstrip(" \t"))]


def apply_block(source: str, scene_name: str, settings: SceneSettings) -> str:
    """Return ``source`` with the managed block for ``settings`` inside ``scene_name``.

    Replaces an existing block in place, so applying twice gives the same text.  Raises
    :class:`SettingsConflictError` when the class already defines a clashing name.
    """
    crlf = "\r\n" in source
    text = _remove_class_block_lf(source.replace("\r\n", "\n"), scene_name)
    cls = _find_class(_parse(text), scene_name)
    clash = sorted(_needed_names(settings) & _defined_names(cls))
    if clash:
        raise SettingsConflictError(
            f"{scene_name} already defines {', '.join(clash)}; "
            "move that code out, then apply the settings again"
        )
    lines = text.splitlines(keepends=True)
    index, indent = _insert_point(lines, cls)
    block = textwrap.indent(render_block(settings), indent) + "\n"
    result = "".join(lines[:index]) + block + "".join(lines[index:])
    _parse(result)
    return result.replace("\n", "\r\n") if crlf else result
