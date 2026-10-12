"""The preview's status in the status bar: a coloured dot, one word, and details on hover."""

from __future__ import annotations

from PySide6.QtWidgets import QLabel, QWidget

from manim_studio.preview_status import StatusSummary
from manim_studio.theme import current_theme

__all__ = ["StatusChip"]


class StatusChip(QLabel):
    """Shows a :class:`~manim_studio.preview_status.StatusSummary` as one status word."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__("● Not running", parent)
        self.setObjectName("statusChip")

    def show_summary(self, summary: StatusSummary) -> None:
        colors = current_theme().colors
        tone_colors = {
            "ok": colors.ok,
            "busy": colors.accent,
            "warn": colors.warn,
            "err": colors.err,
            "accent": colors.accent,
            "muted": colors.muted,
        }
        self.setText(f"● {summary.text}")
        self.setStyleSheet(f"color: {tone_colors.get(summary.tone, colors.muted)};")
        self.setToolTip(summary.detail)
