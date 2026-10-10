"""Offscreen tests for the Scintilla code editor wrapper."""

from __future__ import annotations

from pathlib import Path

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest

from manim_studio.editor.code_editor import (
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
    _editor_palette,
)
from manim_studio.theme import CHALKBOARD, PAPER

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


# Three multi-line statements, all longer than the sampled x window so the
# whole region is inside the selection.
_DARK = _editor_palette(CHALKBOARD)
_LIGHT = _editor_palette(PAPER)
_SELECTION_SOURCE = "grid = NumberPlane()\nself.play(grid.animate.shift(LEFT))\nself.wait()\n"


def _region_colours(editor: CodeEditor, *, x0: int = 70, x1: int = 125) -> dict[str, int]:
    """Colour histogram of the first three text lines of a rendered editor."""
    image = editor.grab().toImage()
    counts: dict[str, int] = {}
    for y in range(2, min(46, image.height())):
        for x in range(x0, min(x1, image.width())):
            name = image.pixelColor(x, y).name()
            counts[name] = counts.get(name, 0) + 1
    return counts


def _dominant_colour(editor: CodeEditor) -> str:
    counts = _region_colours(editor)
    return max(counts, key=lambda name: counts[name])


def _hex(rgb: int) -> str:
    return f"#{rgb:06x}"


class TestText:
    def test_round_trip(self, editor: CodeEditor) -> None:
        editor.set_text(SOURCE)
        assert editor.text() == SOURCE

    def test_crlf_normalized(self, editor: CodeEditor) -> None:
        editor.set_text("a = 1\r\nb = 2\r\n")
        assert editor.text() == "a = 1\nb = 2\n"

    def test_typed_newline_is_lf(self, editor: CodeEditor) -> None:
        editor.set_text("a = 1\n")
        editor.gotoPos(editor.textLength())
        QTest.keyClick(editor, Qt.Key.Key_Return)
        # Windows Scintilla would otherwise insert CRLF, desyncing the buffer
        # from ``text()`` and the Jedi caret offset.
        assert "\r" not in editor._raw_text()
        assert editor.text() == "a = 1\n\n"

    def test_caret_offset_maps_through_crlf(self, editor: CodeEditor) -> None:
        # A CRLF can still arrive via paste (bypassing set_text's normalisation);
        # the character offset must ignore the '\r' so it indexes ``text()``.
        editor.setText("x = 1\r\ny = gri")
        editor.gotoPos(editor.textLength())
        assert "\r" in editor._raw_text()
        text = editor.text()
        assert editor._char_position() == len(text)
        assert text[: editor._char_position()].endswith("y = gri")

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

    def test_theme_applies_selection_and_caret_alpha(self, editor: CodeEditor) -> None:
        editor.apply_theme(dark=True)
        assert editor.selAlpha() == _DARK["selection_alpha"]
        assert editor.additionalSelAlpha() == _DARK["selection_alpha"]
        assert editor.caretLineBackAlpha() == _DARK["caret_line_alpha"]
        editor.apply_theme(dark=False)
        assert editor.selAlpha() == _LIGHT["selection_alpha"]
        assert editor.caretLineBackAlpha() == _LIGHT["caret_line_alpha"]

    def test_selection_band_uses_dark_palette(self, editor: CodeEditor) -> None:
        editor.set_text(_SELECTION_SOURCE)
        editor.apply_theme(dark=True)
        editor.setSel(0, editor.textLength())
        # A dark-theme band, not Scintilla's opaque light-grey default.
        assert _dominant_colour(editor) == _hex(_DARK["selection_bg"])

    def test_selection_band_uses_light_palette(self, editor: CodeEditor) -> None:
        editor.set_text(_SELECTION_SOURCE)
        editor.apply_theme(dark=False)
        editor.setSel(0, editor.textLength())
        assert _dominant_colour(editor) == _hex(_LIGHT["selection_bg"])

    def test_selection_keeps_token_colours(self, editor: CodeEditor) -> None:
        editor.set_text(_SELECTION_SOURCE)
        editor.apply_theme(dark=True)
        editor.setSel(0, editor.textLength())
        colours = set(_region_colours(editor))
        # Default text and a syntax colour still paint inside the band: the
        # selection foreground is the token colour, not flattened.
        syntax = CHALKBOARD.syntax
        assert CHALKBOARD.colors.text in colours
        assert colours & {syntax.class_, syntax.builtin, syntax.function, syntax.number}


class TestCompletion:
    def test_member_dot_requests_completions(self, editor: CodeEditor, qtbot) -> None:
        editor.set_text("value.")
        editor.lineEnd()
        offsets: list[int] = []
        editor.completionsRequested.connect(lambda _source, offset: offsets.append(offset))
        editor._on_char_added(ord("."))
        qtbot.waitUntil(lambda: bool(offsets), timeout=2000)
        assert offsets[-1] == len(editor.text())

    def test_open_paren_requests_signatures(self, editor: CodeEditor) -> None:
        editor.set_text("greet(")
        editor.lineEnd()
        offsets: list[int] = []
        editor.signaturesRequested.connect(lambda _source, offset: offsets.append(offset))
        editor._on_char_added(ord("("))
        assert offsets == [len("greet(")]

    def test_requests_skipped_inside_comment(self, editor: CodeEditor) -> None:
        editor.set_text("value = 1  # note")
        editor.lineEnd()
        offsets: list[int] = []
        editor.completionsRequested.connect(lambda _source, offset: offsets.append(offset))
        editor._request_completions()
        assert offsets == []

    def test_requests_skipped_inside_string(self, editor: CodeEditor) -> None:
        editor.set_text('label = "some text')
        editor.lineEnd()
        offsets: list[int] = []
        editor.completionsRequested.connect(lambda _source, offset: offsets.append(offset))
        editor._request_completions()
        assert offsets == []

    def test_caret_guard_kinds(self, editor: CodeEditor) -> None:
        editor.set_text("x = 1")
        assert editor._caret_in_ignored_token("x = 1", 5) is False
        assert editor._caret_in_ignored_token('x = "abc"', 9) is True
        assert editor._caret_in_ignored_token("x = 1  # note", 13) is True

    def test_show_completions_popup(self, editor: CodeEditor) -> None:
        editor.set_text("val")
        editor.lineEnd()
        editor.show_completions(editor.current_position(), ["value", "validate"])
        assert editor.autoCActive() is True
        editor.autoCCancel()
        assert editor.autoCActive() is False

    def test_stale_completion_offset_ignored(self, editor: CodeEditor) -> None:
        editor.set_text("val")
        editor.lineEnd()
        editor.show_completions(0, ["value"])
        assert editor.autoCActive() is False

    def test_show_calltip(self, editor: CodeEditor) -> None:
        editor.set_text("greet(")
        editor.lineEnd()
        editor.show_calltip(editor.current_position(), "greet(name, excited)")
        assert editor.callTipActive() is True
        editor.callTipCancel()
        assert editor.callTipActive() is False

    def test_instant_list_includes_buffer_words(self, editor: CodeEditor) -> None:
        editor.set_text("grid = NumberPlane()\nvalue = 1\nx = gri")
        editor.gotoPos(editor.textLength())
        editor.set_completions(["Circle", "Square"])
        words = editor._instant_completions(member=False)
        assert "grid" in words and "Circle" in words
        assert editor._show_static_completions() is True
        assert editor.autoCActive() is True
        editor.autoCCancel()

    def test_member_context_omits_global_api_names(self, editor: CodeEditor) -> None:
        editor.set_text("grid = NumberPlane()\ngrid.pl")
        editor.gotoPos(editor.textLength())
        editor.set_completions(["Circle", "Square"])
        member_words = editor._instant_completions(member=True)
        all_words = editor._instant_completions(member=False)
        assert "Circle" not in member_words
        assert "grid" in member_words
        assert "Circle" in all_words and "grid" in all_words

    def test_instant_list_excludes_comment_and_string_words(self, editor: CodeEditor) -> None:
        editor.set_text(
            'grid = NumberPlane()  # the mobject moves\nlabel = "interpolate value"\ngrid.pl'
        )
        editor.set_completions([])
        words = editor._instant_completions(member=True)
        assert "grid" in words and "label" in words
        assert "the" not in words and "mobject" not in words
        assert "interpolate" not in words and "value" not in words

    def test_member_access_detection(self, editor: CodeEditor) -> None:
        editor.set_text("grid.pl")
        editor.gotoPos(editor.textLength())
        assert editor._in_member_access(editor.current_position()) is True
        editor.set_text("grid")
        editor.gotoPos(editor.textLength())
        assert editor._in_member_access(editor.current_position()) is False

    def test_completion_offset_is_character_based(self, editor: CodeEditor) -> None:
        text = "π = gri"
        editor.set_text(text)
        editor.gotoPos(editor.textLength())
        assert editor.current_position() == len(text.encode("utf-8"))
        assert editor._char_position() == len(text)
        offsets: list[int] = []
        editor.completionsRequested.connect(lambda _source, offset: offsets.append(offset))
        editor._request_completions()
        assert offsets == [len(text)]

    def test_offset_after_typed_enter_targets_caret(self, editor: CodeEditor) -> None:
        # Regression: typing Enter must not shift the Jedi offset past the caret
        # (the bug made ``self.a`` complete at the next line and return globals).
        editor.set_text("def f():\n    return 1\n")
        editor.go_to_line(2)
        editor.lineEnd()
        QTest.keyClick(editor, Qt.Key.Key_Return)
        QTest.keyClicks(editor, "    value")
        offsets: list[int] = []
        editor.completionsRequested.connect(lambda _source, offset: offsets.append(offset))
        editor._request_completions()
        text = editor.text()
        assert text == "def f():\n    return 1\n    value\n"
        assert offsets == [len(text) - 1]  # caret before the trailing newline
        assert text[: offsets[-1]].endswith("    value")


class TestInsertLineAt:
    def test_inserts_before_a_line_with_undo(self, editor: CodeEditor) -> None:
        editor.set_text("a = 1\nb = 2\nc = 3\n")
        editor.emptyUndoBuffer()
        editor.insert_line_at(2, "inserted = True")
        assert editor.text() == "a = 1\ninserted = True\nb = 2\nc = 3\n"
        assert editor.current_line() == 2
        editor.undo()
        assert editor.text() == "a = 1\nb = 2\nc = 3\n"

    def test_inserts_at_the_end(self, editor: CodeEditor) -> None:
        editor.set_text("a = 1\n")
        editor.insert_line_at(2, "b = 2")
        assert editor.text() == "a = 1\nb = 2\n"

    def test_inserts_into_an_empty_buffer(self, editor: CodeEditor) -> None:
        editor.set_text("")
        editor.insert_line_at(1, "a = 1")
        assert editor.text() == "a = 1\n"

    def test_appends_a_newline_when_the_file_lacks_one(self, editor: CodeEditor) -> None:
        editor.setText("a = 1")  # no trailing newline
        editor.insert_line_at(2, "b = 2")
        assert editor.text() == "a = 1\nb = 2\n"
