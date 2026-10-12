"""Live-shell dock: host logs, a Python prompt and exec-result formatting.

The dock is a passive view — it emits :attr:`ShellPanel.lineSubmitted` and lets
the window talk to the preview host.  Routine output only bumps an unread
counter (``unreadChanged``); ``append(..., reveal=True)`` opens the dock for real
problems and for output the user asked for.
"""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QDockWidget, QLineEdit, QPlainTextEdit, QVBoxLayout, QWidget

__all__ = ["ShellPanel"]


class ShellPanel(QDockWidget):
    """Bottom dock for preview-host logs; Enter submits a line to the host."""

    lineSubmitted = Signal(str)
    unreadChanged = Signal(int)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__("Live shell", parent)
        self._unread = 0
        self.output = QPlainTextEdit(self)
        self.output.setReadOnly(True)
        self.output.setMaximumBlockCount(2000)
        self.output.setPlaceholderText("Preview host log and live shell output")
        self.input = QLineEdit(self)
        self.input.setPlaceholderText(">>> run Python against the live scene (Enter)")
        self.input.returnPressed.connect(self._send_current)
        body = QWidget(self)
        layout = QVBoxLayout(body)
        layout.setContentsMargins(2, 2, 2, 2)
        layout.addWidget(self.output, 1)
        layout.addWidget(self.input)
        self.setWidget(body)
        self.setAllowedAreas(Qt.DockWidgetArea.BottomDockWidgetArea)
        self.hide()

    @property
    def unread(self) -> int:
        return self._unread

    def append(self, text: str, *, reveal: bool = False) -> None:
        """Append output; open the dock only when ``reveal`` (else count it unread)."""
        if not text:
            return
        self.output.appendPlainText(text)
        if reveal:
            self.show()
        elif not self.isVisible():
            self._set_unread(self._unread + 1)

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
