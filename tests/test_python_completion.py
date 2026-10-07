"""Tests for the Jedi-backed completion/signature helpers."""

from __future__ import annotations

from manim_studio.python_completion import (
    Completion,
    Signature,
    complete,
    offset_to_line_col,
    signatures,
)


class TestOffsetToLineCol:
    def test_first_line(self) -> None:
        assert offset_to_line_col("abc", 2) == (1, 2)

    def test_second_line(self) -> None:
        assert offset_to_line_col("abc\ndefg", 6) == (2, 2)

    def test_clamps_out_of_range(self) -> None:
        assert offset_to_line_col("ab", 99) == (1, 2)
        assert offset_to_line_col("ab", -5) == (1, 0)


class TestComplete:
    def test_member_completion_uses_full_name(self) -> None:
        source = "values = [1, 2, 3]\nvalues.ap"
        names = [item.insert for item in complete(source, len(source))]
        assert "append" in names

    def test_import_member_completion(self) -> None:
        source = "import os\nos.pa"
        names = [item.insert for item in complete(source, len(source))]
        assert "path" in names

    def test_empty_source_returns_nothing(self) -> None:
        assert complete("", 0) == ()

    def test_broken_source_does_not_raise(self) -> None:
        assert isinstance(complete("def f(:\n    ", 10), tuple)

    def test_results_are_completions(self) -> None:
        source = "values = [1]\nvalues.ap"
        assert all(isinstance(item, Completion) for item in complete(source, len(source)))


class TestSignatures:
    def test_def_signature(self) -> None:
        source = "def greet(name, excited):\n    pass\n\ngreet("
        found = signatures(source, len(source))
        assert found and isinstance(found[0], Signature)
        assert "greet(name, excited)" in found[0].label
        assert found[0].active_parameter == 0

    def test_no_call_returns_nothing(self) -> None:
        assert signatures("x = 1", 5) == ()

    def test_empty_source_returns_nothing(self) -> None:
        assert signatures("", 0) == ()
