"""The live shell's filters: a level and a search, with unread counted against the filter."""

from __future__ import annotations

import pytest

from manim_studio.editor.shell_panel import ShellPanel
from manim_studio.shell_log import Level, ShellLog, classify


class TestClassification:
    def test_error_lines_are_errors(self) -> None:
        assert classify("ERROR: scene crashed") == Level.ERROR
        assert classify("NameError: name 'x' is not defined") == Level.ERROR
        assert classify("Traceback (most recent call last):\n  File 'a.py'\nValueError: bad") == (
            Level.ERROR
        )
        assert classify("! division by zero") == Level.ERROR
        assert classify("open failed: demo.py: no such scene") == Level.ERROR

    def test_warnings_are_warnings(self) -> None:
        assert classify("WARNING: cache is cold") == Level.WARNING
        assert classify("DeprecationWarning: old api", Level.DEBUG) == Level.WARNING

    def test_editor_messages_default_to_info_and_host_output_to_debug(self) -> None:
        assert classify("< 42") == Level.INFO
        assert classify("Writing frame 12", Level.DEBUG) == Level.DEBUG

    def test_an_error_in_host_output_is_still_an_error(self) -> None:
        assert classify("ValueError: boom", Level.DEBUG) == Level.ERROR


class TestFilters:
    @staticmethod
    def _log() -> ShellLog:
        log = ShellLog()
        log.add("debug line", Level.DEBUG)
        log.add("info line")
        log.add("WARNING: careful")
        log.add("ERROR: broken")
        return log

    def test_each_filter_shows_its_level_and_everything_above(self) -> None:
        log = self._log()

        def texts(level: Level) -> list[str]:
            return [entry.text for entry in log.visible(level)]

        assert texts(Level.DEBUG) == [
            "debug line",
            "info line",
            "WARNING: careful",
            "ERROR: broken",
        ]
        assert texts(Level.INFO) == ["info line", "WARNING: careful", "ERROR: broken"]
        assert texts(Level.WARNING) == ["WARNING: careful", "ERROR: broken"]
        assert texts(Level.ERROR) == ["ERROR: broken"]

    def test_search_is_case_insensitive_and_combines_with_the_level(self) -> None:
        log = self._log()
        assert [e.text for e in log.visible(Level.DEBUG, "BROKEN")] == ["ERROR: broken"]
        assert [e.text for e in log.visible(Level.INFO, "line")] == ["info line"]

    def test_the_buffer_is_bounded_and_keeps_the_newest_lines(self) -> None:
        log = ShellLog(limit=3)
        for number in range(5):
            log.add(f"line {number}")
        assert [e.text for e in log.visible(Level.DEBUG)] == ["line 2", "line 3", "line 4"]


@pytest.fixture
def panel(qtbot) -> ShellPanel:  # type: ignore[no-untyped-def]
    shell = ShellPanel()
    qtbot.addWidget(shell)
    return shell


class TestShellPanel:
    def test_the_errors_filter_hides_routine_and_editor_lines(self, panel: ShellPanel) -> None:
        panel.append_output("routine")
        panel.append("note")
        panel.append("ERROR: broken")
        panel.set_filter(Level.ERROR)
        assert panel.output.toPlainText() == "ERROR: broken"
        panel.set_filter(Level.DEBUG)
        assert panel.output.toPlainText() == "routine\nnote\nERROR: broken"

    def test_clicking_a_filter_button_changes_the_view(self, panel: ShellPanel) -> None:
        panel.append_output("routine")
        panel.append("note")
        panel._filters.button(1).click()  # Info
        assert panel.level == Level.INFO
        assert panel.output.toPlainText() == "note"

    def test_the_search_narrows_the_view_and_clearing_restores_it(self, panel: ShellPanel) -> None:
        panel.append_output("frame 1 rendered")
        panel.append_output("frame 2 rendered")
        panel.append_output("cache cleared")
        panel.set_query("FRAME 2")
        assert panel.output.toPlainText() == "frame 2 rendered"
        panel.set_query("")
        assert panel.output.toPlainText().splitlines() == [
            "frame 1 rendered",
            "frame 2 rendered",
            "cache cleared",
        ]

    def test_unread_counts_only_the_lines_the_filter_shows(self, panel: ShellPanel) -> None:
        panel.hide()
        panel.set_filter(Level.ERROR)
        panel.append_output("routine")
        panel.append("note")
        assert panel.unread == 0
        panel.append("ERROR: broken")
        assert panel.unread == 1

    def test_a_filtered_out_line_is_kept_for_when_the_filter_opens(self, panel: ShellPanel) -> None:
        panel.set_filter(Level.ERROR)
        panel.append_output("routine")
        panel.set_filter(Level.DEBUG)
        assert panel.output.toPlainText() == "routine"
