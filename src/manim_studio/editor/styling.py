"""Turn a :class:`~manim_studio.theme.Theme` into a Qt palette and style sheet."""

from __future__ import annotations

from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication

from manim_studio.theme import Theme, derive, set_current_theme

__all__ = ["apply_theme", "build_palette", "build_stylesheet"]

_ROLE = QPalette.ColorRole
_GROUP = QPalette.ColorGroup


def build_palette(theme: Theme) -> QPalette:
    """Application palette for ``theme`` (Fusion draws everything not styled by QSS)."""
    c = theme.colors
    d = derive(theme)
    palette = QPalette()
    for role, colour in (
        (_ROLE.Window, c.bg),
        (_ROLE.WindowText, c.text),
        (_ROLE.Base, c.editor),
        (_ROLE.AlternateBase, c.surface),
        (_ROLE.ToolTipBase, c.raised),
        (_ROLE.ToolTipText, c.text),
        (_ROLE.Text, c.text),
        (_ROLE.Button, c.raised),
        (_ROLE.ButtonText, c.text),
        (_ROLE.BrightText, c.err),
        (_ROLE.Link, c.accent),
        (_ROLE.Highlight, d.list_selection),
        (_ROLE.HighlightedText, c.text),
        (_ROLE.PlaceholderText, c.faint),
        (_ROLE.Light, c.border_strong),
        (_ROLE.Midlight, c.border),
        (_ROLE.Mid, c.border),
        (_ROLE.Dark, c.bg),
        (_ROLE.Shadow, c.preview),
    ):
        palette.setColor(role, QColor(colour))
    for role in (_ROLE.WindowText, _ROLE.Text, _ROLE.ButtonText):
        palette.setColor(_GROUP.Disabled, role, QColor(c.faint))
    return palette


def build_stylesheet(theme: Theme) -> str:
    """Style sheet for the widgets Fusion renders poorly on a custom palette.

    Combo boxes, spin boxes and check boxes are left to Fusion on purpose: styling
    their border in QSS removes the native arrows and indicators.
    """
    c = theme.colors
    d = derive(theme)
    m = theme.metrics
    radius = m.radius
    small = round(radius * 0.66)
    large = round(radius * 1.6)
    pad = round(12 * m.density)
    gap = round(8 * m.density)
    control = m.control_height - 2  # QSS min-height excludes the 1px border
    return f"""
QToolTip {{
    background: {c.raised}; color: {c.text}; border: 1px solid {c.border_strong};
    border-radius: {small}px; padding: 4px 8px;
}}
QMenuBar {{ background: {c.bg}; color: {c.muted}; padding: 0 {gap}px; }}
QMenuBar::item {{ background: transparent; padding: 4px {gap}px; border-radius: {small}px; }}
QMenuBar::item:selected {{ background: {c.raised}; color: {c.text}; }}
QMenu {{
    background: {c.surface}; color: {c.text}; border: 1px solid {c.border_strong};
    border-radius: {large}px; padding: 4px;
}}
QMenu::item {{ padding: 5px {pad * 2}px 5px {pad}px; border-radius: {small}px; }}
QMenu::item:selected {{ background: {d.accent_soft}; color: {c.text}; }}
QMenu::item:disabled {{ color: {c.faint}; }}
QMenu::separator {{ height: 1px; background: {c.border}; margin: 4px {gap}px; }}
QToolBar {{
    background: {c.surface}; border: none; border-bottom: 1px solid {c.border};
    spacing: {gap}px; padding: 4px {pad}px;
}}
QToolBar::separator {{ background: {c.border}; width: 1px; margin: 4px 2px; }}
QToolButton {{
    background: transparent; color: {c.text}; border: 1px solid transparent;
    border-radius: {radius}px; padding: 3px {gap}px;
}}
QToolButton:hover {{ background: {c.raised}; }}
QToolButton:pressed {{ background: {d.pressed}; }}
QToolButton:checked {{ background: {d.accent_soft}; border-color: {d.ring}; }}
QToolButton:disabled {{ color: {c.faint}; }}
QPushButton {{
    background: {c.raised}; color: {c.text}; border: 1px solid {c.border_strong};
    border-radius: {radius}px; padding: 0 {pad}px; min-height: {control}px; min-width: 56px;
}}
QPushButton:hover {{ background: {d.hover}; }}
QPushButton:pressed {{ background: {d.pressed}; }}
QPushButton:focus {{ border-color: {c.accent}; }}
QPushButton:default {{
    background: {c.accent}; color: {c.on_accent}; border-color: transparent; font-weight: 600;
}}
QPushButton:default:hover {{ background: {d.accent_hover}; }}
QPushButton:disabled {{ color: {c.faint}; background: {c.surface}; border-color: {c.border}; }}
QLineEdit, QPlainTextEdit, QTextEdit {{
    background: {c.editor}; color: {c.text}; border: 1px solid {c.border_strong};
    border-radius: {radius}px; padding: 2px {gap}px;
    selection-background-color: {d.selection}; selection-color: {c.text};
}}
QLineEdit {{ min-height: {control - 4}px; }}
QLineEdit:focus, QPlainTextEdit:focus, QTextEdit:focus {{ border-color: {c.accent}; }}
QLineEdit:read-only {{ color: {c.muted}; }}
QScrollBar:vertical {{ background: transparent; width: 12px; margin: 0; }}
QScrollBar:horizontal {{ background: transparent; height: 12px; margin: 0; }}
QScrollBar::handle {{ background: {c.border_strong}; border-radius: 4px; margin: 2px; }}
QScrollBar::handle:vertical {{ min-height: 28px; }}
QScrollBar::handle:horizontal {{ min-width: 28px; }}
QScrollBar::handle:hover {{ background: {c.faint}; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}
QSplitter::handle {{ background: {c.bg}; }}
QSplitter::handle:horizontal {{ width: 4px; }}
QSplitter::handle:vertical {{ height: 4px; }}
QSplitter::handle:hover {{ background: {c.accent}; }}
QDockWidget {{ color: {c.muted}; }}
QDockWidget::title {{
    background: {c.surface}; padding: 6px {pad}px; border-bottom: 1px solid {c.border};
}}
QStatusBar {{ background: {c.surface}; color: {c.muted}; border-top: 1px solid {c.border}; }}
QStatusBar::item {{ border: none; }}
QStatusBar QLabel {{ padding: 0 {gap // 2}px; }}
QProgressBar {{
    background: {c.editor}; border: 1px solid {c.border}; border-radius: {small}px;
    text-align: center; color: {c.text};
}}
QProgressBar::chunk {{ background: {c.accent}; border-radius: {small}px; }}
"""


def apply_theme(app: QApplication, theme: Theme) -> None:
    """Make ``theme`` the active theme for the whole application."""
    set_current_theme(theme)
    app.setStyle("Fusion")
    app.setPalette(build_palette(theme))
    font = app.font()
    font.setPixelSize(theme.metrics.font_size)
    app.setFont(font)
    app.setStyleSheet(build_stylesheet(theme))
