"""Generate the Manim Studio icon procedurally.

The icon is drawn with Pillow (a transitive runtime dependency) so the repository
needs no binary design assets and the output is deterministic:

    uv run python packaging/make_icon.py

The mark is an "S" for Studio on a dark Chalkboard tile, drawn as two cubic bezier
segments the way Manim builds every shape: the anchors, handles and control points
are left visible, as in a vector editor.  The upper curve is cyan, the lower green.
Below it sits a real timeline strip: ruler ticks, a progress track and the yellow
playhead.  Sizes under 48 px get a simplified drawing (bolder S, yellow tip dot, no
construction lines or timeline) so the silhouette stays readable in the taskbar and
title bar.

Two copies are written from the same drawing code:

* ``packaging/manim_studio.ico`` — build input for PyInstaller and Inno Setup.
* ``src/manim_studio/assets/manim_studio.ico`` — runtime asset the editor loads
  with :func:`manim_studio.editor.icon.load_icon` for the window/taskbar icon.

``scripts/build.ps1`` runs this automatically before PyInstaller.
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

OUTPUT = Path(__file__).with_name("manim_studio.ico")
ASSET_OUTPUT = (
    Path(__file__).resolve().parents[1] / "src" / "manim_studio" / "assets" / "manim_studio.ico"
)
SIZES = (16, 24, 32, 48, 64, 128, 256)
SIMPLE_BELOW = 48  # sizes under this get the simplified drawing
SUPERSAMPLE = 4

# Chalkboard theme colours (src/manim_studio/theme.py).
TILE_TOP = (27, 32, 41)
TILE_BOTTOM = (13, 16, 21)
TILE_EDGE = (58, 67, 82)
GUIDE = (150, 160, 180)
CYAN = (88, 196, 221)
GREEN = (131, 193, 103)
YELLOW = (255, 216, 102)
CORNER_RADIUS = 0.22  # of the tile size

Point = tuple[float, float]
Segment = tuple[Point, Point, Point, Point]

# The S as two cubic bezier segments in a 0..1 design space: (anchor, handle, handle,
# anchor).  The second segment is the first rotated 180 degrees about the centre, so the
# join at (0.5, 0.44) is smooth (collinear handles).
_UPPER: Segment = ((0.74, 0.27), (0.70, 0.06), (0.22, 0.10), (0.50, 0.44))
_LOWER: Segment = ((0.50, 0.44), (0.78, 0.78), (0.30, 0.82), (0.26, 0.61))


def _gradient(size: int) -> Image.Image:
    column = Image.new("RGB", (1, size))
    column.putdata(
        [
            tuple(
                round(TILE_TOP[i] + (TILE_BOTTOM[i] - TILE_TOP[i]) * y / (size - 1))
                for i in range(3)
            )
            for y in range(size)
        ]
    )
    return column.resize((size, size), Image.Resampling.NEAREST).convert("RGBA")


def _layer(size: int) -> tuple[Image.Image, ImageDraw.ImageDraw]:
    layer = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    return layer, ImageDraw.Draw(layer)


def _bezier(segment: Segment, steps: int = 160) -> list[Point]:
    (x0, y0), (x1, y1), (x2, y2), (x3, y3) = segment
    curve: list[Point] = []
    for i in range(steps + 1):
        t = i / steps
        u = 1 - t
        a, b, c, d = u**3, 3 * u * u * t, 3 * u * t * t, t**3
        curve.append((a * x0 + b * x1 + c * x2 + d * x3, a * y0 + b * y1 + c * y2 + d * y3))
    return curve


def _stroke(
    size: int, curve: list[Point], stroke: float, colour: tuple[int, int, int]
) -> Image.Image:
    """A round-capped thick line along ``curve`` (pixel coordinates)."""
    mask = Image.new("L", (size, size), 0)
    draw = ImageDraw.Draw(mask)
    half = stroke / 2
    for x, y in curve:
        draw.ellipse((x - half, y - half, x + half, y + half), fill=255)
    layer = Image.new("RGBA", (size, size), (*colour, 0))
    layer.putalpha(mask)
    return layer


def _construction(size: int, upper: Segment, lower: Segment) -> Image.Image:
    """Bezier construction lines: handle lines, hollow control points, square anchors."""
    layer, draw = _layer(size)
    line = max(1, round(size * 0.008))
    for anchor, handle in (
        (upper[0], upper[1]),
        (upper[3], upper[2]),
        (lower[0], lower[1]),
        (lower[3], lower[2]),
    ):
        draw.line((*anchor, *handle), fill=(*GUIDE, 190), width=line)
    ring = 0.022 * size
    for x, y in (upper[1], upper[2], lower[1], lower[2]):
        draw.ellipse(
            (x - ring, y - ring, x + ring, y + ring),
            fill=(*TILE_TOP, 255),
            outline=(*YELLOW, 255),
            width=max(1, round(size * 0.009)),
        )
    box = 0.024 * size
    for x, y in (upper[0], upper[3], lower[3]):
        draw.rectangle((x - box, y - box, x + box, y + box), fill=(*YELLOW, 255))
    return layer


def _timeline_strip(size: int) -> Image.Image:
    """The editor's timeline in miniature: ruler ticks, a progress track and the playhead."""
    layer, draw = _layer(size)
    left, right = 0.16 * size, 0.84 * size
    track_top, track_bottom = 0.845 * size, 0.875 * size
    playhead = left + (right - left) * 0.58
    draw.rounded_rectangle(
        (left, track_top, right, track_bottom), radius=0.015 * size, fill=(*TILE_EDGE, 255)
    )
    draw.rounded_rectangle(
        (left, track_top, playhead, track_bottom), radius=0.015 * size, fill=(*CYAN, 255)
    )
    # Ruler ticks above the track, every fifth one taller.
    ticks = 20
    for i in range(ticks + 1):
        x = left + (right - left) * i / ticks
        tall = i % 5 == 0
        top = (0.775 if tall else 0.795) * size
        draw.line(
            (x, top, x, 0.825 * size),
            fill=(*GUIDE, 255 if tall else 170),
            width=max(1, round(size * 0.007)),
        )
    # Playhead: a yellow line through the strip with a small head above it.
    draw.line(
        (playhead, 0.76 * size, playhead, 0.9 * size),
        fill=(*YELLOW, 255),
        width=max(1, round(size * 0.012)),
    )
    head = 0.022 * size
    draw.polygon(
        [
            (playhead - head, 0.745 * size),
            (playhead + head, 0.745 * size),
            (playhead, 0.775 * size),
        ],
        fill=(*YELLOW, 255),
    )
    return layer


def _render(px: int) -> Image.Image:
    """Draw the icon at ``px`` pixels square (supersampled, then reduced)."""
    simple = px < SIMPLE_BELOW
    s = px * SUPERSAMPLE

    tile = _gradient(s)
    mask = Image.new("L", (s, s), 0)
    radius = round(s * CORNER_RADIUS)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, s - 1, s - 1), radius=radius, fill=255)

    # Design space -> pixels.  The detailed icon shrinks the S to leave room for the strip.
    scale, top = (1.0, 0.05) if simple else (0.88, 0.085)
    y_scale = 1.1 if simple else scale

    def place(point: Point) -> Point:
        x, y = point
        return ((0.5 + (x - 0.5) * scale) * s, (top + (y - 0.08) * y_scale) * s)

    upper: Segment = (place(_UPPER[0]), place(_UPPER[1]), place(_UPPER[2]), place(_UPPER[3]))
    lower: Segment = (place(_LOWER[0]), place(_LOWER[1]), place(_LOWER[2]), place(_LOWER[3]))
    stroke = (0.105 if simple else 0.07) * s
    tile = Image.alpha_composite(tile, _stroke(s, _bezier(upper), stroke, CYAN))
    tile = Image.alpha_composite(tile, _stroke(s, _bezier(lower), stroke, GREEN))

    if simple:
        # A yellow dot on the upper tip stands in for the construction lines.
        tip, tdraw = _layer(s)
        tx, ty = upper[0]
        knob = 0.085 * s
        tdraw.ellipse((tx - knob, ty - knob, tx + knob, ty + knob), fill=(*YELLOW, 255))
        tile = Image.alpha_composite(tile, tip)
    else:
        tile = Image.alpha_composite(tile, _construction(s, upper, lower))
        tile = Image.alpha_composite(tile, _timeline_strip(s))
        # Fine edge so the tile holds its shape on dark taskbars.
        edge, edraw = _layer(s)
        edraw.rounded_rectangle(
            (0, 0, s - 1, s - 1), radius=radius, outline=(*TILE_EDGE, 160), width=max(2, s // 128)
        )
        tile = Image.alpha_composite(tile, edge)

    tile.putalpha(Image.composite(tile.getchannel("A"), Image.new("L", (s, s), 0), mask))
    return tile.resize((px, px), Image.Resampling.LANCZOS)


def main() -> int:
    frames = [_render(size) for size in SIZES]
    largest = frames[-1]
    others = frames[:-1]
    for output in (OUTPUT, ASSET_OUTPUT):
        output.parent.mkdir(parents=True, exist_ok=True)
        largest.save(
            output,
            format="ICO",
            sizes=[(size, size) for size in SIZES],
            append_images=others,
        )
        print(f"wrote {output} ({output.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
