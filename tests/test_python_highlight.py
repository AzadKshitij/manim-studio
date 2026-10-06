"""Tests for the Pygments-powered tokenizer used by the code editor."""

from __future__ import annotations

from manim_editor.python_highlight import (
    Span,
    TokenKind,
    highlight_spans,
    representative_token,
)


def kinds_for(text: str) -> dict[str, list[TokenKind]]:
    """Map each distinct token text to the kinds it was classified as."""
    result: dict[str, list[TokenKind]] = {}
    for start, end, kind in highlight_spans(text):
        result.setdefault(text[start:end], []).append(kind)
    return result


def span_of(text: str, needle: str) -> Span:
    for span in highlight_spans(text):
        if text[span[0] : span[1]] == needle:
            return span
    raise AssertionError(f"no span for {needle!r} in {text!r}")


class TestKinds:
    def test_keywords_and_statements(self) -> None:
        kinds = kinds_for("from manimlib import *\ndef f():\n    return 1\n")
        assert kinds["from"] == [TokenKind.KEYWORD]
        assert kinds["import"] == [TokenKind.KEYWORD]
        assert kinds["def"] == [TokenKind.KEYWORD]
        assert kinds["return"] == [TokenKind.KEYWORD]

    def test_function_and_class_names(self) -> None:
        kinds = kinds_for("def helper():\n    pass\n\n\nclass Demo(Scene):\n    pass\n")
        assert kinds["helper"] == [TokenKind.FUNCTION]
        assert kinds["Demo"] == [TokenKind.CLASS]

    def test_call_site_pascal_case_is_class(self) -> None:
        kinds = kinds_for("circle = Circle()\n")
        assert kinds["Circle"] == [TokenKind.CLASS]

    def test_constants(self) -> None:
        kinds = kinds_for("x = LEFT + True\n")
        assert kinds["True"] == [TokenKind.CONSTANT]
        assert kinds["LEFT"] == [TokenKind.CONSTANT]

    def test_builtins(self) -> None:
        kinds = kinds_for("print(len(items))\n")
        assert kinds["print"] == [TokenKind.BUILTIN]
        assert kinds["len"] == [TokenKind.BUILTIN]

    def test_decorator_includes_at_sign(self) -> None:
        kinds = kinds_for("@staticmethod\ndef f():\n    pass\n")
        assert kinds["@staticmethod"] == [TokenKind.DECORATOR]

    def test_numbers_strings_comments_operators(self) -> None:
        kinds = kinds_for('value = 3.14 + 1  # note\nname = "hi"\n')
        assert kinds["3.14"] == [TokenKind.NUMBER]
        assert kinds["1"] == [TokenKind.NUMBER]
        assert kinds["# note"] == [TokenKind.COMMENT]
        assert kinds['"hi"'] == [TokenKind.STRING]
        assert kinds["+"] == [TokenKind.OPERATOR]

    def test_lowercase_name_stays_name(self) -> None:
        kinds = kinds_for("credit = value\n")
        assert kinds["credit"] == [TokenKind.NAME]
        assert kinds["value"] == [TokenKind.NAME]


class TestMultilineAndFStrings:
    def test_triple_quoted_string_is_one_span(self) -> None:
        text = 'x = """first\nsecond"""\n'
        span = span_of(text, '"""first\nsecond"""')
        assert span[2] is TokenKind.STRING
        assert text[span[0] : span[1]].count("\n") == 1

    def test_f_string_interpolation_is_tokenised(self) -> None:
        text = 'msg = f"v={value}"\n'
        kinds = kinds_for(text)
        # The literal pieces merge into one string run; the interpolation is
        # still a name so it can be styled separately.
        assert kinds['f"v={'] == [TokenKind.STRING]
        assert kinds["value"] == [TokenKind.NAME]


class TestSpanInvariants:
    def test_spans_are_ordered_and_in_range(self) -> None:
        text = 'from manimlib import *\n\nmsg = f"{x}"  # c\n'
        spans = highlight_spans(text)
        assert spans
        previous_end = 0
        for start, end, _kind in spans:
            assert 0 <= start < end <= len(text)
            assert start >= previous_end
            previous_end = end

    def test_empty_text_has_no_spans(self) -> None:
        assert highlight_spans("") == []

    def test_representative_tokens_exist_for_every_kind(self) -> None:
        for kind in TokenKind:
            assert representative_token(kind) is not None
