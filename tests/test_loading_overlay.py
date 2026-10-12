"""LoadingOverlay: debounce, minimum on-screen time, veil, animation and placement."""

from __future__ import annotations

import pytest
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QWidget

from manim_studio.editor.loading_overlay import LoadingOverlay
from manim_studio.editor.preview_panel import PreviewPanel


class _Clock:
    """An injected monotonic clock the test moves by hand."""

    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def host(qtbot) -> QWidget:  # type: ignore[no-untyped-def]
    widget = QWidget()
    widget.resize(400, 300)
    qtbot.addWidget(widget)
    widget.show()
    return widget


class TestDebounce:
    def test_nothing_shows_before_the_delay(self, host: QWidget) -> None:
        overlay = LoadingOverlay(host, show_delay_ms=10_000)
        overlay.set_caption("Importing scene…")
        assert overlay.is_pending
        assert not overlay.is_showing

    def test_clearing_before_the_delay_never_shows(self, host: QWidget) -> None:
        overlay = LoadingOverlay(host, show_delay_ms=10_000)
        overlay.set_caption("Importing scene…")
        overlay.clear()
        assert not overlay.is_pending
        assert not overlay.is_showing

    def test_shows_once_loading_is_still_going(self, qtbot, host: QWidget) -> None:  # type: ignore[no-untyped-def]
        overlay = LoadingOverlay(host, show_delay_ms=0)
        overlay.set_caption("Starting preview…")
        qtbot.waitUntil(lambda: overlay.is_showing)
        assert overlay.isVisible()
        assert overlay.caption == "Starting preview…"


class TestMinimumVisibleTime:
    def test_hide_is_held_until_the_minimum_has_passed(self, qtbot, host: QWidget) -> None:  # type: ignore[no-untyped-def]
        clock = _Clock()
        overlay = LoadingOverlay(host, clock=clock, show_delay_ms=0, min_visible_ms=50)
        overlay.set_caption("Reloading scene…")
        qtbot.waitUntil(lambda: overlay.is_showing)
        clock.now += 0.01  # on screen for 10 ms of the 50 ms minimum
        overlay.clear()
        assert overlay.is_hiding
        assert overlay.is_showing
        qtbot.waitUntil(lambda: not overlay.is_showing)

    def test_hide_is_immediate_after_the_minimum(self, qtbot, host: QWidget) -> None:  # type: ignore[no-untyped-def]
        clock = _Clock()
        overlay = LoadingOverlay(host, clock=clock, show_delay_ms=0, min_visible_ms=50)
        overlay.set_caption("Reloading scene…")
        qtbot.waitUntil(lambda: overlay.is_showing)
        clock.now += 1.0
        overlay.clear()
        assert not overlay.is_showing
        assert not overlay.is_hiding

    def test_new_caption_cancels_a_pending_hide(self, qtbot, host: QWidget) -> None:  # type: ignore[no-untyped-def]
        clock = _Clock()
        overlay = LoadingOverlay(host, clock=clock, show_delay_ms=0, min_visible_ms=50)
        overlay.set_caption("Reloading scene…")
        qtbot.waitUntil(lambda: overlay.is_showing)
        overlay.clear()
        assert overlay.is_hiding
        overlay.set_caption("Rendering first frame…")
        assert not overlay.is_hiding
        assert overlay.is_showing
        assert overlay.caption == "Rendering first frame…"


class TestPainting:
    def test_spinner_advances_while_showing(self, qtbot, host: QWidget) -> None:  # type: ignore[no-untyped-def]
        overlay = LoadingOverlay(host, show_delay_ms=0)
        overlay.set_caption("Starting preview…")
        qtbot.waitUntil(lambda: overlay.angle > 0)
        assert overlay.is_animating

    def test_hiding_stops_the_animation(self, host: QWidget) -> None:
        overlay = LoadingOverlay(host, show_delay_ms=0, min_visible_ms=0)
        overlay._show_due()  # the debounce has elapsed; show immediately
        assert overlay.is_animating
        overlay.clear()
        assert not overlay.is_showing
        assert not overlay.is_animating

    def test_veil_follows_the_latest_caption(self, host: QWidget) -> None:
        overlay = LoadingOverlay(host, show_delay_ms=10_000)
        overlay.set_caption("Reloading scene…", veil=True)
        assert overlay.veil
        overlay.set_caption("Starting preview…")
        assert not overlay.veil

    def test_painting_produces_pixels(self, host: QWidget) -> None:
        overlay = LoadingOverlay(host, show_delay_ms=10_000)
        overlay.resize(200, 150)
        overlay.set_caption("Importing scene…", veil=True)
        overlay._show_due()
        assert not overlay.grab().isNull()


class TestPreviewPanelIntegration:
    def test_overlay_covers_the_body_below_the_header(self, qtbot) -> None:  # type: ignore[no-untyped-def]
        panel = PreviewPanel()
        qtbot.addWidget(panel)
        panel.resize(400, 300)
        panel.show()
        panel.show_loading("Starting preview…")
        overlay = panel.findChild(LoadingOverlay)
        body = panel.findChild(QWidget, "previewBody")
        assert overlay is not None and body is not None
        qtbot.waitUntil(lambda: overlay.is_showing)
        assert overlay.parentWidget() is body
        assert overlay.geometry() == body.rect()
        assert body.geometry().top() > 0  # the body sits below the header
        panel.hide_loading()

    def test_reload_dims_the_last_frame_but_cold_start_does_not(self, qtbot) -> None:  # type: ignore[no-untyped-def]
        panel = PreviewPanel()
        qtbot.addWidget(panel)
        overlay = panel.findChild(LoadingOverlay)
        assert overlay is not None
        panel.show_loading("Starting preview…")
        assert not overlay.veil
        panel.set_frame(QImage(8, 8, QImage.Format.Format_RGB32))
        panel.show_loading("Reloading scene…")
        assert overlay.veil
        panel.hide_loading()
