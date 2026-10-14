"""The error banner over the preview: what it says, what its buttons do, when it clears."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import QPushButton, QWidget

from manim_studio.editor.error_banner import ErrorBanner
from manim_studio.editor.preview_panel import PreviewPanel


def _button(banner: QWidget, text: str) -> QPushButton:
    matches = [b for b in banner.findChildren(QPushButton) if b.text() == text]
    assert len(matches) == 1, text
    return matches[0]


class TestBanner:
    def test_hidden_until_an_error_is_shown(self, qtbot) -> None:  # type: ignore[no-untyped-def]
        banner = ErrorBanner()
        qtbot.addWidget(banner)
        assert banner.isHidden()

    def test_names_the_line_and_the_message(self, qtbot) -> None:  # type: ignore[no-untyped-def]
        banner = ErrorBanner()
        qtbot.addWidget(banner)
        banner.show_error("NameError: name 'x' is not defined", 42)
        assert banner.message == "Line 42 — NameError: name 'x' is not defined"
        assert banner.line == 42
        assert not banner.isHidden()

    def test_a_message_without_a_line_offers_no_go_to_line(self, qtbot) -> None:  # type: ignore[no-untyped-def]
        banner = ErrorBanner()
        qtbot.addWidget(banner)
        banner.show_error("LaTeX failed", None)
        assert banner.message == "LaTeX failed"
        assert banner.line is None
        assert _button(banner, "Go to line").isHidden()

    def test_go_to_line_emits_the_line(self, qtbot) -> None:  # type: ignore[no-untyped-def]
        banner = ErrorBanner()
        qtbot.addWidget(banner)
        banner.show_error("boom", 7)
        with qtbot.waitSignal(banner.gotoLineRequested) as blocker:
            _button(banner, "Go to line").click()
        assert blocker.args == [7]

    def test_reload_emits(self, qtbot) -> None:  # type: ignore[no-untyped-def]
        banner = ErrorBanner()
        qtbot.addWidget(banner)
        banner.show_error("boom", 7)
        with qtbot.waitSignal(banner.reloadRequested):
            _button(banner, "Reload").click()

    def test_clear_hides_it(self, qtbot) -> None:  # type: ignore[no-untyped-def]
        banner = ErrorBanner()
        qtbot.addWidget(banner)
        banner.show_error("boom", 7)
        banner.clear()
        assert banner.isHidden()
        assert banner.line is None

    def test_a_long_message_is_elided_rather_than_widening_the_window(self, qtbot) -> None:  # type: ignore[no-untyped-def]
        banner = ErrorBanner()
        qtbot.addWidget(banner)
        banner.show_error("x" * 2000, 1)
        assert banner.minimumSizeHint().width() < 800


class TestPanel:
    def test_the_panel_forwards_go_to_line_and_reload(self, qtbot) -> None:  # type: ignore[no-untyped-def]
        panel = PreviewPanel()
        qtbot.addWidget(panel)
        banner = panel.findChild(ErrorBanner)
        assert banner is not None
        panel.show_error("boom", 5)
        with qtbot.waitSignal(panel.errorGotoLineRequested) as blocker:
            _button(banner, "Go to line").click()
        assert blocker.args == [5]
        with qtbot.waitSignal(panel.errorReloadRequested):
            _button(banner, "Reload").click()

    def test_clear_error_hides_the_banner(self, qtbot) -> None:  # type: ignore[no-untyped-def]
        panel = PreviewPanel()
        qtbot.addWidget(panel)
        banner = panel.findChild(ErrorBanner)
        assert banner is not None
        panel.show_error("boom", 5)
        panel.clear_error()
        assert banner.isHidden()

    def test_the_banner_sits_above_the_body(self, qtbot) -> None:  # type: ignore[no-untyped-def]
        panel = PreviewPanel()
        qtbot.addWidget(panel)
        panel.resize(480, 360)
        panel.show()
        panel.show_error("boom", 5)
        banner = panel.findChild(ErrorBanner)
        body = panel.findChild(QWidget, "previewBody")
        assert banner is not None and body is not None
        panel.layout().activate()
        assert banner.geometry().bottom() < body.geometry().top()


class TestWindow:
    """The banner's buttons act on the open editor: the caret moves, the preview reloads."""

    def test_go_to_line_moves_the_caret(self, qtbot, tmp_path) -> None:  # type: ignore[no-untyped-def]
        import shutil

        from manim_studio.editor.main_window import MainWindow

        scene = tmp_path / "demo.py"
        shutil.copy(Path(__file__).parent / "scenes" / "feature_showcase.py", scene)
        window = MainWindow(restore_settings=False, auto_preview=False, completion=False)
        qtbot.addWidget(window)
        assert window.open_path(scene)
        window.preview_panel.show_error("boom", 3)
        banner = window.preview_panel.findChild(ErrorBanner)
        assert banner is not None
        _button(banner, "Go to line").click()
        assert window.editor.current_line() == 3

    def test_reload_restarts_the_preview(self, qtbot, monkeypatch) -> None:  # type: ignore[no-untyped-def]
        from manim_studio.editor.main_window import MainWindow

        calls: list[bool] = []
        monkeypatch.setattr(MainWindow, "reload_preview", lambda self, **_kw: calls.append(True))
        window = MainWindow(restore_settings=False, auto_preview=False, completion=False)
        qtbot.addWidget(window)
        window.preview_panel.show_error("boom", None)
        banner = window.preview_panel.findChild(ErrorBanner)
        assert banner is not None
        _button(banner, "Reload").click()
        assert calls == [True]
