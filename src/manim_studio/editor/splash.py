"""Startup splash screen showing the logo while the editor loads."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPainter, QPixmap
from PySide6.QtWidgets import QSplashScreen

from manim_studio import APP_NAME

__all__ = ["create_splash", "logo_path"]

LOGO_FILENAME = "logo.png"
SPLASH_SIZE = 320


def logo_path() -> Path:
    """Return the packaged logo image."""
    return Path(__file__).resolve().parents[1] / "assets" / LOGO_FILENAME


def create_splash() -> QSplashScreen | None:
    """Build the splash from the logo, or ``None`` when the asset is missing."""
    logo = QPixmap(str(logo_path()))
    if logo.isNull():
        return None
    logo = logo.scaled(
        SPLASH_SIZE,
        SPLASH_SIZE,
        Qt.AspectRatioMode.KeepAspectRatio,
        Qt.TransformationMode.SmoothTransformation,
    )
    pad = 24
    canvas = QPixmap(logo.width() + 2 * pad, logo.height() + 2 * pad + 28)
    canvas.fill(QColor("#1e1f22"))
    painter = QPainter(canvas)
    painter.drawPixmap(pad, pad, logo)
    painter.setPen(QColor("#d8d8d8"))
    painter.drawText(
        canvas.rect().adjusted(0, 0, 0, -10),
        Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignBottom,
        f"{APP_NAME} is starting…",
    )
    painter.end()
    splash = QSplashScreen(canvas, Qt.WindowType.WindowStaysOnTopHint)
    return splash
