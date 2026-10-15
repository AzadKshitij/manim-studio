"""A portrait frame keeps its shape in the preview and can fit a tall, narrow panel (Qt)."""

from __future__ import annotations

import pytest
from PySide6.QtGui import QImage

from manim_studio.editor.preview_panel import PreviewPanel


def _portrait(width: int = 108, height: int = 192) -> QImage:
    image = QImage(width, height, QImage.Format.Format_RGBA8888)
    image.fill(0xFF3366FF)
    return image


@pytest.fixture
def panel(qtbot) -> PreviewPanel:
    widget = PreviewPanel()
    qtbot.addWidget(widget)
    return widget


def test_a_portrait_frame_is_letterboxed_in_a_wide_panel(panel: PreviewPanel) -> None:
    panel.resize(800, 500)
    panel.show()
    panel.set_frame(_portrait())
    pixmap = panel._viewport.pixmap()
    assert pixmap is not None and not pixmap.isNull()
    assert pixmap.width() <= panel._viewport.width()
    assert pixmap.height() <= panel._viewport.height()
    assert pixmap.width() / pixmap.height() == pytest.approx(9 / 16, abs=0.01)
    assert pixmap.width() < panel._viewport.width()  # pillarboxed, not stretched


def test_a_tall_narrow_panel_can_hold_a_portrait_preview(panel: PreviewPanel) -> None:
    panel.resize(260, 700)
    panel.show()
    panel.set_frame(_portrait())
    assert panel._viewport.minimumWidth() < 320
    assert panel._viewport.width() < 320
