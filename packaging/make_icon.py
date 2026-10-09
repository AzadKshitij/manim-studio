"""Generate the Manim Studio icon procedurally.

The icon is drawn with Pillow (a transitive runtime dependency) so the repository
needs no binary design assets and the output is deterministic:

    uv run python packaging/make_icon.py

The mark is the signature Manim move, a circle morphing into a square, frozen at
the playhead on a dark Chalkboard tile: round and cyan before it, square and green
after it.  Three lane blocks sit below, as on the editor's timeline.  Sizes under
48 px get a simplified drawing (bolder shape, no lanes or edge) so the silhouette
stays readable in the taskbar and title bar.

Two copies are written from the same drawing code:

* ``packaging/manim_studio.ico`` — build input for PyInstaller and Inno Setup.
* ``src/manim_studio/assets/manim_studio.ico`` — runtime asset the editor loads
  with :func:`manim_studio.editor.icon.load_icon` for the window/taskbar icon.

``scripts/build.ps1`` runs this automatically before PyInstaller.
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageChops, ImageDraw

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
CYAN = (88, 196, 221)
GREEN = (131, 193, 103)
PURPLE = (197, 157, 240)
YELLOW = (255, 216, 102)
CORNER_RADIUS = 0.22  # of the tile size


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


def _render(px: int) -> Image.Image:
    """Draw the icon at ``px`` pixels square (supersampled, then reduced)."""
    simple = px < SIMPLE_BELOW
    s = px * SUPERSAMPLE

    tile = _gradient(s)
    mask = Image.new("L", (s, s), 0)
    radius = round(s * CORNER_RADIUS)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, s - 1, s - 1), radius=radius, fill=255)

    def box(cx: float, cy: float, half: float) -> tuple[float, float, float, float]:
        return (cx * s - half * s, cy * s - half * s, cx * s + half * s, cy * s + half * s)

    cx, cy = 0.5, (0.52 if simple else 0.47)
    half = 0.28 if simple else 0.26
    stroke = max(1, round(s * (0.085 if simple else 0.05)))

    # The morph, frozen at the playhead: round on the left (the circle), square on the
    # right (the square it becomes).  Cyan to green marks the change.
    x0, y0, x1, y1 = box(cx, cy, half)

    def d_mask(inset: float) -> Image.Image:
        """A disc on the left joined to a square on the right, shrunk by ``inset``."""
        mask = Image.new("L", (s, s), 0)
        draw = ImageDraw.Draw(mask)
        left, top, right, bottom = x0 + inset, y0 + inset, x1 - inset, y1 - inset
        draw.ellipse((left, top, left + (bottom - top), bottom), fill=255)
        draw.rectangle(((left + right) / 2 - 1, top, right, bottom), fill=255)
        return mask

    inner = d_mask(stroke)
    ring = ImageChops.subtract(d_mask(0), inner)
    alpha = ImageChops.lighter(ring, inner.point(lambda v: v * 46 // 255))

    def tinted(colour: tuple[int, int, int]) -> Image.Image:
        layer = Image.new("RGBA", (s, s), (*colour, 0))
        layer.putalpha(alpha)
        return layer

    right_half = Image.new("L", (s, s), 0)
    ImageDraw.Draw(right_half).rectangle((round(cx * s), 0, s, s), fill=255)
    shape = Image.composite(tinted(GREEN), tinted(CYAN), right_half)
    tile = Image.alpha_composite(tile, shape)

    # Playhead, at the seam between the two halves.
    head, hdraw = _layer(s)
    top = 0.12 if simple else 0.10
    bottom = 0.88 if simple else 0.80
    width = max(1, round(s * (0.06 if simple else 0.034)))
    x = cx * s
    hdraw.line((x, top * s, x, bottom * s), fill=(*YELLOW, 255), width=width)
    knob = (0.065 if simple else 0.055) * s
    hdraw.ellipse((x - knob, top * s - knob, x + knob, top * s + knob), fill=(*YELLOW, 255))
    tile = Image.alpha_composite(tile, head)

    if not simple:
        # Timeline lanes: three blocks, as on the editor's timeline.
        lanes, ldraw = _layer(s)
        lane_top, lane_h = 0.835, 0.06
        for left, right, colour in (
            (0.18, 0.42, CYAN),
            (0.44, 0.63, GREEN),
            (0.65, 0.82, PURPLE),
        ):
            ldraw.rounded_rectangle(
                (left * s, lane_top * s, right * s, (lane_top + lane_h) * s),
                radius=round(s * 0.018),
                fill=(*colour, 255),
            )
        tile = Image.alpha_composite(tile, lanes)

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
