"""Vector icons for the timeline and toolbar buttons (no image assets).

Each glyph is a small SVG on a 24x24 grid, drawn with ``currentColor`` semantics:
:func:`glyph` renders it in the widget palette's text colour (and a dimmed variant
for the disabled state), so icons follow light and dark themes.  Buttons built with
:func:`icon_button` carry the full name in their tooltip and accessible name, because
an icon alone is not a label.
"""

from __future__ import annotations

from PySide6.QtCore import QByteArray, QSize, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPalette, QPixmap
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import QApplication, QToolButton, QWidget

__all__ = ["GLYPH_NAMES", "glyph", "icon_button"]

# Stroked shapes use the default stroke; ``F`` marks shapes that are filled instead.
_STROKE = 'fill="none" stroke="{c}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"'
_FILL = 'fill="{c}" stroke="none"'

_GLYPHS: dict[str, str] = {
    # transport ---------------------------------------------------------------
    "play": f'<polygon points="7,4 20,12 7,20" {_FILL}/>',
    "pause": f'<rect x="6" y="4" width="4" height="16" rx="1" {_FILL}/>'
    f'<rect x="14" y="4" width="4" height="16" rx="1" {_FILL}/>',
    "start": f'<rect x="4" y="4" width="2.5" height="16" rx="1" {_FILL}/>'
    f'<polygon points="20,5 9,12 20,19" {_FILL}/>',
    "end": f'<rect x="17.5" y="4" width="2.5" height="16" rx="1" {_FILL}/>'
    f'<polygon points="4,5 15,12 4,19" {_FILL}/>',
    "step_back": f'<polygon points="18,5 8,12 18,19" {_FILL}/><path d="M5 5v14" {_STROKE}/>',
    "step_forward": f'<polygon points="6,5 16,12 6,19" {_FILL}/><path d="M19 5v14" {_STROKE}/>',
    "loop": f'<path d="M17 2l4 4-4 4M3 11V9a4 4 0 0 1 4-4h14M7 22l-4-4 4-4M21 13v2a4 4 0 0 1-4 4H3" {_STROKE}/>',  # noqa: E501
    # view --------------------------------------------------------------------
    "fit": f'<path d="M3 12h18M7 8l-4 4 4 4M17 8l4 4-4 4" {_STROKE}/>',
    "fit_selection": f'<path d="M4 8V4h4M16 4h4v4M20 16v4h-4M8 20H4v-4" {_STROKE}/>'
    f'<rect x="9" y="9" width="6" height="6" rx="1" {_STROKE}/>',
    "zoom_in": f'<circle cx="11" cy="11" r="7" {_STROKE}/>'
    f'<path d="M21 21l-4.3-4.3M11 8v6M8 11h6" {_STROKE}/>',
    "zoom_out": f'<circle cx="11" cy="11" r="7" {_STROKE}/>'
    f'<path d="M21 21l-4.3-4.3M8 11h6" {_STROKE}/>',
    # editing -----------------------------------------------------------------
    "undo": f'<path d="M9 14L4 9l5-5M4 9h10.5a5.5 5.5 0 0 1 0 11H11" {_STROKE}/>',
    "redo": f'<path d="M15 14l5-5-5-5M20 9H9.5a5.5 5.5 0 0 0 0 11H13" {_STROKE}/>',
    "delete": f'<path d="M3 6h18M8 6V4h8v2M19 6l-1 14H6L5 6M10 11v6M14 11v6" {_STROKE}/>',
    "reset": f'<path d="M3 12a9 9 0 1 0 3-6.7M3 4v5h5" {_STROKE}/>',
    "hold": f'<circle cx="12" cy="13" r="8" {_STROKE}/><path d="M12 9v4l2.5 2M9 2h6" {_STROKE}/>',
    "jump_to_code": f'<path d="M8 6L3 12l5 6M16 6l5 6-5 6M14 4l-4 16" {_STROKE}/>',
    # edits -------------------------------------------------------------------
    "apply": f'<path d="M20 6L9 17l-5-5" {_STROKE}/>',
    "diff": f'<rect x="3" y="3" width="18" height="18" rx="2" {_STROKE}/>'
    f'<path d="M8 8v6M5 11h6M13 16h6" {_STROKE}/>',
    "discard_orphans": f'<path d="M18.8 12.3l1.7-1.7a5 5 0 0 0-7-7l-1.7 1.7M5.2 11.7L3.5 13.4a5 5 0 0 0 7 7l1.7-1.7M8 2v3M2 8h3M16 19v3M19 16h3" {_STROKE}/>',  # noqa: E501
    "discard_all": f'<circle cx="12" cy="12" r="9" {_STROKE}/>'
    f'<path d="M15 9l-6 6M9 9l6 6" {_STROKE}/>',
    "orphans": f'<path d="M12 3l10 18H2zM12 10v5M12 18.5v.5" {_STROKE}/>',
    # app ---------------------------------------------------------------------
    "render": f'<rect x="2" y="5" width="20" height="14" rx="2" {_STROKE}/>'
    f'<path d="M7 5v14M17 5v14M2 12h20" {_STROKE}/>',
    "reload": f'<path d="M21 12a9 9 0 1 1-3-6.7M21 4v5h-5" {_STROKE}/>',
    "stop": f'<rect x="6" y="6" width="12" height="12" rx="1.5" {_FILL}/>',
}

GLYPH_NAMES: tuple[str, ...] = tuple(sorted(_GLYPHS))
_ICON_PX = 18


def _render(name: str, color: QColor, size: int) -> QPixmap:
    svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" '
        f'width="{size}" height="{size}">{_GLYPHS[name].replace("{c}", color.name())}</svg>'
    )
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    # ``QColor.name()`` has no alpha, so dimming (the disabled state) is applied here.
    painter.setOpacity(color.alphaF())
    QSvgRenderer(QByteArray(svg.encode("utf-8"))).render(painter)
    painter.end()
    return pixmap


def glyph(name: str, color: QColor | None = None) -> QIcon:
    """A themed icon for ``name`` (normal and dimmed disabled pixmaps, 1x and 2x)."""
    if name not in _GLYPHS:
        raise KeyError(f"unknown glyph {name!r}; available: {GLYPH_NAMES}")
    if color is None:
        app = QApplication.instance()
        color = (
            app.palette().color(QPalette.ColorRole.ButtonText)  # type: ignore[attr-defined]
            if app is not None
            else QColor(0x33, 0x33, 0x33)
        )
    disabled = QColor(color)
    disabled.setAlphaF(0.35)
    icon = QIcon()
    for size in (_ICON_PX, _ICON_PX * 2):
        icon.addPixmap(_render(name, color, size), QIcon.Mode.Normal)
        icon.addPixmap(_render(name, disabled, size), QIcon.Mode.Disabled)
    return icon


def icon_button(
    name: str,
    tooltip: str,
    parent: QWidget | None = None,
    *,
    checkable: bool = False,
) -> QToolButton:
    """An icon-only tool button; ``tooltip`` is the full name (and the accessible name)."""
    button = QToolButton(parent)
    button.setIcon(glyph(name))
    button.setIconSize(QSize(_ICON_PX, _ICON_PX))
    button.setToolTip(tooltip)
    button.setAccessibleName(tooltip.split("  (")[0].split(" (")[0])
    button.setCheckable(checkable)
    button.setAutoRaise(True)
    button.setProperty("glyph", name)
    return button
