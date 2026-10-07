"""Generate the Manim Studio icon procedurally.

The icon is drawn with Pillow (a transitive runtime dependency) so the repository
needs no binary design assets and the output is deterministic:

    uv run python packaging/make_icon.py

Two copies are written from the same master image:

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
    Path(__file__).resolve().parents[1]
    / "src"
    / "manim_studio"
    / "assets"
    / "manim_studio.ico"
)
SIZES = (16, 24, 32, 48, 64, 128, 256)
MASTER = 256
SUPERSAMPLE = 4

TOP_COLOR = (99, 102, 241)  # indigo-500
BOTTOM_COLOR = (67, 56, 202)  # indigo-700
CORNER_RADIUS = 0.22  # of the tile size


def _gradient_background(size: int) -> Image.Image:
    column = Image.new("RGB", (1, size))
    column.putdata(
        [
            (
                round(TOP_COLOR[0] + (BOTTOM_COLOR[0] - TOP_COLOR[0]) * y / (size - 1)),
                round(TOP_COLOR[1] + (BOTTOM_COLOR[1] - TOP_COLOR[1]) * y / (size - 1)),
                round(TOP_COLOR[2] + (BOTTOM_COLOR[2] - TOP_COLOR[2]) * y / (size - 1)),
            )
            for y in range(size)
        ]
    )
    return column.resize((size, size), Image.Resampling.NEAREST)


def _render_master() -> Image.Image:
    size = MASTER * SUPERSAMPLE
    tile = _gradient_background(size)

    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        (0, 0, size - 1, size - 1), radius=round(size * CORNER_RADIUS), fill=255
    )
    tile.putalpha(mask)

    # White play triangle, optically centred slightly to the right.
    draw = ImageDraw.Draw(tile)
    left = round(size * 0.40)
    right = round(size * 0.74)
    top = round(size * 0.27)
    bottom = round(size * 0.73)
    draw.polygon([(left, top), (left, bottom), (right, size // 2)], fill=(255, 255, 255, 255))

    return tile.resize((MASTER, MASTER), Image.Resampling.LANCZOS)


def main() -> int:
    image = _render_master()
    sizes = [(size, size) for size in SIZES]
    for output in (OUTPUT, ASSET_OUTPUT):
        output.parent.mkdir(parents=True, exist_ok=True)
        image.save(output, format="ICO", sizes=sizes)
        print(f"wrote {output} ({output.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
