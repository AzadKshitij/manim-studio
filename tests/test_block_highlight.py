"""Highlighting the source of the block that "jump to code" lands on."""

from __future__ import annotations

from pathlib import Path

import pytest

from manim_studio.editor.code_editor import MARKER_BLOCK, MARKER_BLOCK_EDGE, CodeEditor
from manim_studio.editor.main_window import MainWindow
from manim_studio.site_index import block_line_range

SOURCE = """from manimlib import *


class Demo(Scene):
    def construct(self):
        circle = Circle()
        self.play(
            ShowCreation(circle),
            run_time=2,
        )
        self.wait(1)
        for i in range(2):
            self.play(circle.animate.shift(RIGHT))
"""
SCENE = Path(__file__).parent / "scenes" / "feature_showcase.py"


class TestBlockLineRange:
    def test_multi_line_call_spans_all_its_lines(self) -> None:
        assert block_line_range(SOURCE, 7) == (7, 10)

    def test_single_line_calls(self) -> None:
        assert block_line_range(SOURCE, 11) == (11, 11)
        assert block_line_range(SOURCE, 13) == (13, 13)

    def test_line_without_a_call_is_just_that_line(self) -> None:
        assert block_line_range(SOURCE, 6) == (6, 6)

    def test_unparseable_source_falls_back_to_the_line(self) -> None:
        assert block_line_range("self.play(", 1) == (1, 1)


@pytest.fixture
def editor(qtbot) -> CodeEditor:
    widget = CodeEditor()
    qtbot.addWidget(widget)
    widget.set_text(SOURCE)
    return widget


def _lines_with(editor: CodeEditor, marker: int) -> list[int]:
    mask = 1 << marker
    return [n + 1 for n in range(editor.lineCount()) if editor.markerGet(n) & mask]


class TestEditorHighlight:
    def test_marks_every_line_of_the_block(self, editor: CodeEditor) -> None:
        editor.highlight_lines(7, 10)
        assert editor.block_highlight == (7, 10)
        assert _lines_with(editor, MARKER_BLOCK) == [7, 8, 9, 10]
        assert _lines_with(editor, MARKER_BLOCK_EDGE) == [7, 8, 9, 10]

    def test_a_new_highlight_replaces_the_old_one(self, editor: CodeEditor) -> None:
        editor.highlight_lines(7, 10)
        editor.highlight_lines(11)
        assert _lines_with(editor, MARKER_BLOCK) == [11]

    def test_clear_removes_it(self, editor: CodeEditor) -> None:
        editor.highlight_lines(7, 10)
        editor.clear_block_highlight()
        assert editor.block_highlight is None
        assert _lines_with(editor, MARKER_BLOCK) == []

    def test_lines_are_clamped_to_the_document(self, editor: CodeEditor) -> None:
        editor.highlight_lines(12, 99)
        assert editor.block_highlight == (12, editor.lineCount())

    def test_it_fades_on_a_timer(self, editor: CodeEditor, qtbot) -> None:
        editor.highlight_lines(7, 10)
        assert editor._block_timer.isActive()
        editor._block_timer.timeout.emit()
        assert editor.block_highlight is None

    def test_editing_drops_the_highlight(self, editor: CodeEditor) -> None:
        editor.highlight_lines(7, 10)
        editor.insertText(0, "# note\n")
        assert editor.block_highlight is None
        assert _lines_with(editor, MARKER_BLOCK) == []

    def test_highlighted_lines_are_painted_with_the_accent_tint(self, editor: CodeEditor) -> None:
        from manim_studio.theme import CHALKBOARD

        editor.apply_theme(CHALKBOARD)
        editor.resize(700, 400)
        editor.show()

        def pixel(line: int) -> tuple[int, int, int]:
            position = editor.positionFromLine(line - 1)
            y = editor.pointYFromPosition(position) + editor.textHeight(line - 1) // 2
            colour = editor.grab().toImage().pixelColor(600, y)
            return colour.red(), colour.green(), colour.blue()

        plain = pixel(7)
        editor.highlight_lines(7, 10)
        tinted = pixel(7)
        assert tinted != plain
        # Tinted towards the accent: the blue channel rises the most on a dark editor.
        assert tinted[2] > plain[2]
        assert pixel(11) == plain  # lines outside the block are untouched


class TestJumpToCode:
    def test_jump_highlights_the_blocks_call(self, qtbot) -> None:
        window = MainWindow(restore_settings=False, auto_preview=False, completion=False)
        qtbot.addWidget(window)
        window.open_path(SCENE)
        line = 12  # any line; the call-line lookup is stubbed below
        window.events._call_lines = (line,)  # type: ignore[attr-defined]
        window._on_block_activated(0)
        assert window.editor.block_highlight is not None
        assert window.editor.block_highlight[0] <= line <= window.editor.block_highlight[1] or (
            window.editor.block_highlight == (line, line)
        )
        assert window.editor.current_line() == line
