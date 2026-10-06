"""Offscreen tests for the Scintilla code editor wrapper."""

from __future__ import annotations

from pathlib import Path

import pytest
from PySide6.QtCore import Qt

from manim_editor.editor.code_editor import (
    PY_BUILTIN,
    PY_CLASSNAME,
    PY_COMMENT,
    PY_CONSTANT,
    PY_DEFNAME,
    PY_IDENTIFIER,
    PY_KEYWORD,
    PY_NUMBER,
    PY_STRING,
    CodeEditor,
)

pytestmark = pytest.mark.usefixtures("qtbot")

SOURCE = """from manimlib import *


def helper():
    return 42


class Demo(Scene):
    def construct(self):
        circle = Circle()
        self.play(ShowCreation(circle))
"""


@pytest.fixture
def editor(qtbot) -> CodeEditor:
    widget = CodeEditor()
    qtbot.addWidget(widget)
    widget.resize(600, 400)
    widget.show()
    return widget


class TestText:
    def test_round_trip(self, editor: CodeEditor) -> None:
        editor.set_text(SOURCE)
        assert editor.text() == SOURCE

    def test_crlf_normalized(self, editor: CodeEditor) -> None:
        editor.set_text("a = 1\r\nb = 2\r\n")
        assert editor.text() == "a = 1\nb = 2\n"

    def test_modification_lifecycle(self, editor: CodeEditor) -> None:
        editor.set_text("x = 1\n")
        assert editor.is_modified() is True
        editor.setSavePoint()
        assert editor.is_modified() is False
        editor.insertText(editor.textLength(), "y = 2\n")
        assert editor.is_modified() is True

    def test_cursor_tracking(self, editor: CodeEditor) -> None:
        editor.set_text("one\ntwo\nthree\n")
        editor.go_to_line(2)
        assert editor.current_line() == 2
        assert editor.current_column() == 1

    def test_go_to_line_clamps(self, editor: CodeEditor) -> None:
        editor.set_text("one")
        editor.go_to_line(999)
        assert editor.current_line() == 1


class TestFiles:
    def test_load_and_save(self, editor: CodeEditor, tmp_path: Path) -> None:
        path = tmp_path / "scene.py"
        path.write_text(SOURCE, encoding="utf-8")
        editor.load_file(path)
        assert editor.text() == SOURCE
        assert editor.is_modified() is False
        editor.insertText(editor.textLength(), "# trailing comment\n")
        editor.save_file(path)
        assert editor.is_modified() is False
        assert path.read_text(encoding="utf-8").endswith("# trailing comment\n")

    def test_save_file_creates_parents(self, editor: CodeEditor, tmp_path: Path) -> None:
        target = tmp_path / "a" / "b" / "scene.py"
        editor.set_text("x = 1\n")
        editor.save_file(target)
        assert target.is_file()


class TestMarkers:
    def test_error_marker_set_and_clear(self, editor: CodeEditor) -> None:
        editor.set_text(SOURCE)
        editor.set_error_marker(3, "boom")
        assert editor.error_line == 3
        editor.clear_error_marker()
        assert editor.error_line is None

    def test_out_of_range_ignored(self, editor: CodeEditor) -> None:
        editor.set_text("x = 1\n")
        editor.set_error_marker(999)
        assert editor.error_line is None


class TestSnippets:
    def test_insert_at_end(self, editor: CodeEditor) -> None:
        editor.set_text("class A:\n    def f(self):\n        pass\n")
        editor.gotoLine(2)
        editor.insert_snippet("if ready:\n    self.play(FadeIn(mob))\n")
        text = editor.text()
        assert "        if ready:\n            self.play(FadeIn(mob))" in text

    def test_dedent_common_indent(self, editor: CodeEditor) -> None:
        editor.set_text("def f(self):\n")
        editor.gotoLine(0)
        editor.insert_snippet("line_a:\n    line_b\n")
        assert "line_a:\n    line_b" in editor.text()


class TestCompletionAndTheme:
    def test_completions_configured(self, editor: CodeEditor) -> None:
        editor.set_text("Cir\n")
        editor.go_to_line(1)
        editor.set_completions(["Circle", "Square", "ShowCreation"])
        assert editor.trigger_completion() is True

    def test_trigger_without_completions(self, editor: CodeEditor) -> None:
        assert editor.trigger_completion() is False

    def test_themes_apply(self, editor: CodeEditor) -> None:
        editor.apply_theme(dark=True)
        editor.apply_theme(dark=False)

    def test_ctrl_s_emits_save_request(self, editor: CodeEditor, qtbot) -> None:
        with qtbot.waitSignal(editor.saveRequested, timeout=3000):
            qtbot.keyClick(editor, Qt.Key.Key_S, Qt.KeyboardModifier.ControlModifier)


class TestHighlighting:
    """End-to-end styling through the Pygments container-lexer path."""

    def test_token_styles_applied(self, editor: CodeEditor) -> None:
        source = (
            "from manimlib import *\n"
            "\n"
            "\n"
            "def helper(x):\n"
            '    """doc"""\n'
            "    credit = LEFT + 3.14  # note\n"
            "    print(True)\n"
            "\n"
            "\n"
            "class Demo(Scene):\n"
            "    pass\n"
        )
        editor.set_text(source)
        samples = {
            "from": PY_KEYWORD,
            "helper": PY_DEFNAME,
            "Demo": PY_CLASSNAME,
            "LEFT": PY_CONSTANT,
            "True": PY_CONSTANT,
            "3.14": PY_NUMBER,
            "# note": PY_COMMENT,
            "print": PY_BUILTIN,
            "credit": PY_IDENTIFIER,
        }
        for token, expected in samples.items():
            assert editor.styleAt(source.index(token)) == expected, token
        quote = source.index('"""')
        assert editor.styleAt(quote) == PY_STRING

    def test_edit_rehighlights_without_style_needed(self, editor: CodeEditor, qtbot) -> None:
        editor.set_text("a = 1\n")
        # Scintilla stamps new text with style 0 and may not emit styleNeeded;
        # the editor restyles on the coalesced modify timer instead.
        editor.insertText(editor.textLength(), "def added():\n    return 1\n")
        qtbot.wait(50)
        text = editor.text()
        assert editor.styleAt(text.index("def")) == PY_KEYWORD
        assert editor.styleAt(text.index("added")) == PY_DEFNAME

    def test_non_ascii_offsets_align(self, editor: CodeEditor) -> None:
        # A multi-byte char before the keyword shifts character and byte
        # offsets apart; styling must still land on the right span.
        source = 'name = "café"\nreturn name\n'
        editor.set_text(source)

        def byte_pos(needle: str, *, last: bool = False) -> int:
            index = source.rindex(needle) if last else source.index(needle)
            return len(source[:index].encode("utf-8"))

        assert editor.styleAt(byte_pos('"café"')) == PY_STRING
        assert editor.styleAt(byte_pos("return")) == PY_KEYWORD
        assert editor.styleAt(byte_pos("name", last=True)) == PY_IDENTIFIER

    def test_apply_theme_both_palettes(self, editor: CodeEditor) -> None:
        editor.set_text("x = 1\n")
        editor.apply_theme(dark=True)
        editor.apply_theme(dark=False)
        assert editor.styleAt(0) == PY_IDENTIFIER
