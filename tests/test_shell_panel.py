"""Tests for the live-shell dock (output, prompt submission, exec results)."""

from __future__ import annotations

from manim_studio.editor.shell_panel import ShellPanel


class TestOutput:
    def test_append_reveals_and_records(self, qtbot) -> None:  # type: ignore[no-untyped-def]
        panel = ShellPanel()
        qtbot.addWidget(panel)
        panel.append("hello")
        assert panel.isVisible()
        assert "hello" in panel.output.toPlainText()

    def test_blank_append_is_ignored(self, qtbot) -> None:  # type: ignore[no-untyped-def]
        panel = ShellPanel()
        qtbot.addWidget(panel)
        panel.append("")
        assert panel.output.toPlainText() == ""
        assert not panel.isVisible()

    def test_exec_result_formats_stdout_value_error(self, qtbot) -> None:  # type: ignore[no-untyped-def]
        panel = ShellPanel()
        qtbot.addWidget(panel)
        panel.append_exec_result({"stdout": "hello\n", "result": 42, "error": "boom"})
        text = panel.output.toPlainText()
        assert "hello" in text
        assert "< 42" in text
        assert "! boom" in text


class TestPrompt:
    def test_submit_emits_line_and_echoes(self, qtbot) -> None:  # type: ignore[no-untyped-def]
        panel = ShellPanel()
        qtbot.addWidget(panel)
        seen: list[str] = []
        panel.lineSubmitted.connect(seen.append)
        panel.submit_text("print('hi')")
        assert seen == ["print('hi')"]
        assert ">>> print('hi')" in panel.output.toPlainText()
        assert panel.input.text() == ""

    def test_blank_submit_emits_nothing(self, qtbot) -> None:  # type: ignore[no-untyped-def]
        panel = ShellPanel()
        qtbot.addWidget(panel)
        seen: list[str] = []
        panel.lineSubmitted.connect(seen.append)
        panel.submit_text("   ")
        assert seen == []

    def test_focus_prompt_shows_panel(self, qtbot) -> None:  # type: ignore[no-untyped-def]
        panel = ShellPanel()
        qtbot.addWidget(panel)
        panel.focus_prompt()
        assert panel.isVisible()
