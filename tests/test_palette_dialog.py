"""CommandPalette popup: filtering, keyboard and mouse use, disabled rows and recency."""

from __future__ import annotations

from collections.abc import Callable

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel, QListWidget, QWidget

from manim_studio.command_palette import ACTIONS, TIMELINE, PaletteEntry
from manim_studio.editor.palette_dialog import CommandPalette, PaletteItem


def _items(calls: list[str]) -> list[PaletteItem]:
    def runner(name: str) -> Callable[[], object]:
        return lambda: calls.append(name)

    return [
        PaletteItem(
            PaletteEntry("a:reload", "Reload preview", ACTIONS, shortcut="F5"),
            runner("reload"),
        ),
        PaletteItem(
            PaletteEntry("a:export", "Export", ACTIONS, enabled=False, reason="open a scene first"),
            runner("export"),
        ),
        PaletteItem(
            PaletteEntry("t:play", "Play / pause", TIMELINE, shortcut="Space"),
            runner("play"),
        ),
    ]


@pytest.fixture
def host(qtbot) -> QWidget:  # type: ignore[no-untyped-def]
    widget = QWidget()
    widget.resize(900, 700)
    qtbot.addWidget(widget)
    widget.show()
    return widget


@pytest.fixture
def calls() -> list[str]:
    return []


@pytest.fixture
def palette(qtbot, host: QWidget, calls: list[str]) -> CommandPalette:  # type: ignore[no-untyped-def]
    dialog = CommandPalette(host, lambda: _items(calls))
    qtbot.addWidget(dialog)
    return dialog


def _titles(palette: CommandPalette) -> list[str]:
    return [entry.title for entry in palette.shown]


class TestOpening:
    def test_lists_every_command_when_opened_empty(self, palette: CommandPalette) -> None:
        palette.open_palette()
        assert palette.isVisible()
        assert _titles(palette) == ["Reload preview", "Export", "Play / pause"]

    def test_prefix_becomes_the_starting_query(self, palette: CommandPalette) -> None:
        palette.open_palette("@")  # blocks: none in this window
        assert palette.input.text() == "@"
        assert palette.shown == ()

    def test_commands_are_fetched_fresh_each_time(
        self, qtbot, host: QWidget, calls: list[str]
    ) -> None:  # type: ignore[no-untyped-def]
        available: list[PaletteItem] = []
        dialog = CommandPalette(host, lambda: list(available))
        qtbot.addWidget(dialog)
        dialog.open_palette()
        assert dialog.shown == ()
        available.extend(_items(calls))
        dialog.open_palette()
        assert len(dialog.shown) == 3


class TestFiltering:
    def test_typing_filters_and_selects_the_best_match(self, palette: CommandPalette) -> None:
        palette.open_palette()
        palette.input.setText("reload")
        assert _titles(palette) == ["Reload preview"]
        assert palette.current_entry is not None
        assert palette.current_entry.key == "a:reload"

    def test_scope_prefix_narrows_the_list(self, palette: CommandPalette) -> None:
        palette.open_palette()
        palette.input.setText("?")  # only entries that have a shortcut
        assert set(_titles(palette)) == {"Reload preview", "Play / pause"}


class TestKeyboard:
    def test_up_and_down_move_and_clamp(self, qtbot, palette: CommandPalette) -> None:  # type: ignore[no-untyped-def]
        palette.open_palette()
        qtbot.keyClick(palette, Qt.Key.Key_Down)
        assert palette.current_entry is not None
        assert palette.current_entry.key == "a:export"
        qtbot.keyClick(palette, Qt.Key.Key_Down)
        qtbot.keyClick(palette, Qt.Key.Key_Down)  # clamped at the last row
        assert palette.current_entry is not None
        assert palette.current_entry.key == "t:play"
        qtbot.keyClick(palette, Qt.Key.Key_Up)
        qtbot.keyClick(palette, Qt.Key.Key_Up)
        qtbot.keyClick(palette, Qt.Key.Key_Up)  # clamped at the first row
        assert palette.current_entry is not None
        assert palette.current_entry.key == "a:reload"

    def test_enter_runs_the_selection_and_closes(
        self, qtbot, palette: CommandPalette, calls: list[str]
    ) -> None:  # type: ignore[no-untyped-def]
        palette.open_palette()
        qtbot.keyClick(palette.input, Qt.Key.Key_Return)
        assert calls == ["reload"]
        assert not palette.isVisible()

    def test_escape_closes_without_running(
        self, qtbot, palette: CommandPalette, calls: list[str]
    ) -> None:  # type: ignore[no-untyped-def]
        palette.open_palette()
        qtbot.keyClick(palette, Qt.Key.Key_Escape)
        assert calls == []
        assert not palette.isVisible()

    def test_disabled_entry_does_not_run_and_says_why(
        self, qtbot, palette: CommandPalette, calls: list[str]
    ) -> None:  # type: ignore[no-untyped-def]
        palette.open_palette()
        palette.input.setText("export")
        qtbot.keyClick(palette.input, Qt.Key.Key_Return)
        assert calls == []
        assert palette.isVisible()
        footer = palette.findChild(QLabel, "paletteFooter")
        assert footer is not None
        assert "unavailable: open a scene first" in footer.text()


class TestMouseAndRecency:
    def test_clicking_a_row_runs_it(self, palette: CommandPalette, calls: list[str]) -> None:
        palette.open_palette()
        rows = palette.findChild(QListWidget, "paletteList")
        assert rows is not None
        rows.itemClicked.emit(rows.item(2))
        assert calls == ["play"]

    def test_recently_run_commands_come_first_on_an_empty_query(
        self, qtbot, palette: CommandPalette, calls: list[str]
    ) -> None:  # type: ignore[no-untyped-def]
        palette.open_palette()
        palette.input.setText("play")
        qtbot.keyClick(palette.input, Qt.Key.Key_Return)
        palette.open_palette()
        assert _titles(palette)[0] == "Play / pause"
