"""The code editor with its header strip (file name, modified dot, language)."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QHBoxLayout, QLabel, QVBoxLayout, QWidget

from manim_studio.editor.code_editor import CodeEditor

__all__ = ["EditorPane"]

HEADER_HEIGHT = 32


class EditorPane(QWidget):
    """``CodeEditor`` under a fixed-height header showing the open file."""

    def __init__(self, editor: CodeEditor, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.editor = editor
        self._file = QLabel("untitled", self)
        self._file.setObjectName("fileTab")
        self._dot = QLabel("", self)
        self._dot.setObjectName("modifiedDot")
        self._language = QLabel("Python · UTF-8", self)
        self._language.setObjectName("panelMeta")

        header = QWidget(self)
        header.setObjectName("panelHeader")
        header.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        header.setFixedHeight(HEADER_HEIGHT)
        row = QHBoxLayout(header)
        row.setContentsMargins(12, 0, 12, 0)
        row.setSpacing(8)
        row.addWidget(self._dot)
        row.addWidget(self._file)
        row.addStretch(1)
        row.addWidget(self._language)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(header)
        layout.addWidget(editor, 1)
        self.set_file(None)

    def set_file(self, name: str | None, *, modified: bool = False) -> None:
        self._file.setText(name or "untitled")
        self._dot.setText("●" if modified else "")
        self._dot.setVisible(modified)

    @property
    def file_text(self) -> str:
        return self._file.text()
