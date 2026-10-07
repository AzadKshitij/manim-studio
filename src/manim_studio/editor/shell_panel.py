"""Live-shell dock: host logs, a Python prompt and exec-result formatting.

The dock is a passive view — it emits :attr:`ShellPanel.lineSubmitted` and lets
the window talk to the preview host.  ``append`` reveals the dock, because both
unprompted host logs and export output are worth surfacing.
"""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QDockWidget, QLineEdit, QPlainTextEdit, QVBoxLayout, QWidget

__all__ = ["ShellPanel"]


class ShellPanel(QDockWidget):
    """Bottom dock for preview-host logs; Enter submits a line to the host."""

    lineSubmitted = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__("Live shell", parent)
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

    def append(self, text: str) -> None:
        """Append output and reveal the dock."""
        if not text:
            return
        self.output.appendPlainText(text)
        self.show()

    def append_exec_result(self, message: dict[str, Any]) -> None:
        """Render one ``exec_result`` event: stdout, the value, then any error."""
        stdout = str(message.get("stdout", ""))
        if stdout:
            self.append(stdout.rstrip())
        result = message.get("result")
        if result is not None:
            self.append(f"< {result}")
        error = message.get("error")
        if error:
            self.append(f"! {error}")

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
        self.show()
        self.append(f">>> {code}")
        self.lineSubmitted.emit(code)
