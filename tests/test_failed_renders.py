"""A failed export or preview render names the failure and opens the live shell."""

from __future__ import annotations

import pytest

from manim_studio.editor.main_window import MainWindow


@pytest.fixture
def window(qtbot) -> MainWindow:  # type: ignore[no-untyped-def]
    win = MainWindow(restore_settings=False, auto_preview=False, completion=False)
    qtbot.addWidget(win)
    return win


def test_a_failed_export_opens_the_shell_and_says_so(window: MainWindow) -> None:
    window._on_export_finished(2)
    assert not window.shell.isHidden()
    assert "export failed (exit 2)" in window.shell.output.toPlainText()


def test_a_finished_export_leaves_the_shell_closed(window: MainWindow) -> None:
    window._on_export_finished(0)
    assert window.shell.isHidden()


def test_a_failed_preview_render_opens_the_shell(window: MainWindow) -> None:
    window._on_preview_video_failed(1)
    assert not window.shell.isHidden()
    assert "preview render failed (exit 1)" in window.shell.output.toPlainText()
