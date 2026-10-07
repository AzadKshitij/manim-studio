"""PreviewPanel adaptive scaling: fast while streaming, smooth once settled."""

from __future__ import annotations

from PySide6.QtGui import QImage

from manim_studio.editor.preview_panel import PreviewPanel


def _image(width: int = 64, height: int = 36) -> QImage:
    image = QImage(width, height, QImage.Format.Format_RGBA8888)
    image.fill(0xFF3366FF)
    return image


class TestAdaptiveScaling:
    def test_streamed_frames_scale_fast_then_settle_smooth(self, qtbot) -> None:
        panel = PreviewPanel()
        qtbot.addWidget(panel)
        panel.resize(400, 300)
        panel.show()
        assert panel._viewport.isVisible()
        calls: list[bool] = []
        panel._rescale = lambda *, fast: calls.append(fast)  # type: ignore[method-assign]

        panel.set_frame(_image())
        assert calls == [True]
        assert panel._settle_timer.isActive()

        qtbot.wait(panel.SETTLE_MS + 100)
        assert calls == [True, False]
        assert not panel._settle_timer.isActive()

    def test_hidden_viewport_defers_scaling_until_shown(self, qtbot) -> None:
        panel = PreviewPanel()
        qtbot.addWidget(panel)
        calls: list[bool] = []
        panel._rescale = lambda *, fast: calls.append(fast)  # type: ignore[method-assign]

        panel.set_frame(_image())
        assert calls == []  # never laid out/shown, so no pixmap work
        assert panel.current_image is not None
        assert not panel._settle_timer.isActive()

        panel.show()
        assert calls and all(fast is False for fast in calls)

    def test_resize_rescales_smoothly(self, qtbot) -> None:
        panel = PreviewPanel()
        qtbot.addWidget(panel)
        panel.resize(400, 300)
        panel.show()
        calls: list[bool] = []
        panel._rescale = lambda *, fast: calls.append(fast)  # type: ignore[method-assign]

        panel.set_frame(_image())
        panel.resize(520, 410)
        assert calls[-1] is False

    def test_clear_frame_stops_settle_timer(self, qtbot) -> None:
        panel = PreviewPanel()
        qtbot.addWidget(panel)
        panel.resize(400, 300)
        panel.show()
        panel.set_frame(_image())
        assert panel._settle_timer.isActive()
        panel.clear_frame()
        assert panel.current_image is None
        assert not panel._settle_timer.isActive()
