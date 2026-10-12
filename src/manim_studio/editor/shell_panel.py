"""Live-shell dock: host logs, a Python prompt and exec-result formatting.

The dock is a passive view — it emits :attr:`ShellPanel.lineSubmitted` and lets
the window talk to the preview host.  Routine output only bumps an unread
counter (``unreadChanged``); ``append(..., reveal=True)`` opens the dock for real
problems and for output the user asked for.

A filter row (All · Info · Warnings · Errors, plus a search box) decides which lines
the view shows.  Every line is kept in a bounded log classified by
:mod:`manim_studio.shell_log`, so changing the filter re-renders the history.  The
unread counter counts only the lines the active filter shows.
"""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QButtonGroup,
    QDockWidget,
    QHBoxLayout,
    QLineEdit,
    QPlainTextEdit,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from manim_studio.shell_log import FILTERS, Entry, Level, ShellLog, shows

__all__ = ["ShellPanel"]


class ShellPanel(QDockWidget):
    """Bottom dock for preview-host logs; Enter submits a line to the host."""

    lineSubmitted = Signal(str)
    unreadChanged = Signal(int)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__("Live shell", parent)
        self._unread = 0
        self._log = ShellLog()
        self._level = Level.DEBUG
        self._query = ""
        self.output = QPlainTextEdit(self)
        self.output.setReadOnly(True)
        self.output.setMaximumBlockCount(2000)
        self.output.setPlaceholderText("Preview host log and live shell output")
        self.input = QLineEdit(self)
        self.input.setPlaceholderText(">>> run Python against the live scene (Enter)")
        self.input.returnPressed.connect(self._send_current)
        self.search = QLineEdit(self)
        self.search.setPlaceholderText("Find in the shell")
        self.search.setClearButtonEnabled(True)
        self.search.setMaximumWidth(220)
        self.search.textChanged.connect(self.set_query)
        self._filters = QButtonGroup(self)
        self._filters.setExclusive(True)
        filter_row = QHBoxLayout()
        filter_row.setContentsMargins(0, 0, 0, 0)
        for number, (label, level) in enumerate(FILTERS):
            button = QToolButton(self)
            button.setText(label)
            button.setCheckable(True)
            button.setChecked(level == self._level)
            button.setToolTip(f"Show {label.lower()} lines")
            button.setAccessibleName(f"Show {label.lower()} lines")
            self._filters.addButton(button, number)
            filter_row.addWidget(button)
        self._filters.idClicked.connect(self._on_filter_clicked)
        filter_row.addStretch(1)
        filter_row.addWidget(self.search)
        body = QWidget(self)
        layout = QVBoxLayout(body)
        layout.setContentsMargins(2, 2, 2, 2)
        layout.addLayout(filter_row)
        layout.addWidget(self.output, 1)
        layout.addWidget(self.input)
        self.setWidget(body)
        self.setAllowedAreas(Qt.DockWidgetArea.BottomDockWidgetArea)
        self.hide()

    @property
    def unread(self) -> int:
        return self._unread

    @property
    def level(self) -> Level:
        """The lowest level the view shows (the active filter)."""
        return self._level

    @property
    def query(self) -> str:
        return self._query

    def append(self, text: str, *, reveal: bool = False) -> None:
        """Append an editor message; open the dock only when ``reveal`` (else count it unread)."""
        self._add(text, Level.INFO, reveal=reveal)

    def append_output(self, text: str) -> None:
        """Append raw host or manimgl output: routine, unless the text says otherwise."""
        self._add(text, Level.DEBUG, reveal=False)

    def set_filter(self, level: Level) -> None:
        """Show ``level`` and everything above it, re-rendering the view."""
        self._level = level
        for number, (_label, filter_level) in enumerate(FILTERS):
            button = self._filters.button(number)
            if button is not None:
                button.setChecked(filter_level == level)
        self._render()

    def set_query(self, text: str) -> None:
        """Show only lines containing ``text`` (case-insensitive); empty shows all."""
        self._query = text
        self._render()

    def _on_filter_clicked(self, number: int) -> None:
        self.set_filter(FILTERS[number][1])

    def _add(self, text: str, base: Level, *, reveal: bool) -> None:
        if not text:
            return
        entry = self._log.add(text, base)
        shown = self._shows(entry)
        if shown:
            self.output.appendPlainText(entry.text)
        if reveal:
            self.show()
        elif shown and not self.isVisible():
            self._set_unread(self._unread + 1)

    def _shows(self, entry: Entry) -> bool:
        return shows(entry, self._level, self._query)

    def _render(self) -> None:
        self.output.setPlainText(
            "\n".join(entry.text for entry in self._log.visible(self._level, self._query))
        )

    def _set_unread(self, count: int) -> None:
        if count != self._unread:
            self._unread = count
            self.unreadChanged.emit(count)

    def showEvent(self, event: Any) -> None:
        self._set_unread(0)
        super().showEvent(event)

    def append_exec_result(self, message: dict[str, Any]) -> None:
        """Render one ``exec_result`` event: stdout, the value, then any error."""
        stdout = str(message.get("stdout", ""))
        if stdout:
            self.append(stdout.rstrip(), reveal=True)
        result = message.get("result")
        if result is not None:
            self.append(f"< {result}", reveal=True)
        error = message.get("error")
        if error:
            self.append(f"! {error}", reveal=True)

    def submit_text(self, code: str) -> None:
        """Put ``code`` in the prompt and submit it (used by menus/tests)."""
        self.input.setText(code)
        self._send_current()

    def focus_prompt(self) -> None:
        self.show()
        self.input.setFocus()

    def _send_current(self) -> None:
        code = self.input.text().strip()
        if not code:
            return
        self.input.clear()
        self.append(f">>> {code}", reveal=True)
        self.lineSubmitted.emit(code)
