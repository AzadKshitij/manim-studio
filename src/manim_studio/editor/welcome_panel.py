"""Welcome page shown in place of the preview while no scene is open."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from manim_studio.editor.elided_label import ElidedLabel

__all__ = ["WelcomePanel"]

_PATH_ROLE = Qt.ItemDataRole.UserRole


class WelcomePanel(QWidget):
    """Open/new buttons, a recent-files list and a three-line first-steps hint."""

    openRequested = Signal()
    newRequested = Signal()
    recentRequested = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("welcomePanel")
        title = QLabel("Manim Studio", self)
        title.setObjectName("welcomeTitle")
        font = title.font()
        font.setPointSize(font.pointSize() + 8)
        font.setBold(True)
        title.setFont(font)
        subtitle = QLabel("Open a ManimGL scene, or start a new one.", self)
        self.open_button = QPushButton("Open scene…", self)
        self.new_button = QPushButton("New scene…", self)
        self.open_button.clicked.connect(self.openRequested)
        self.new_button.clicked.connect(self.newRequested)
        buttons = QHBoxLayout()
        buttons.addWidget(self.open_button)
        buttons.addWidget(self.new_button)
        buttons.addStretch(1)

        self._recent_title = QLabel("Recent", self)
        self.recent_list = QListWidget(self)
        self.recent_list.itemActivated.connect(self._on_item_activated)
        self.recent_list.itemClicked.connect(self._on_item_activated)
        self._empty = QLabel("Scenes you open will appear here.", self)
        self._empty.setEnabled(False)

        hint = ElidedLabel("Edit → save → the preview reloads.  Drag the timeline to scrub.", self)
        hint.setEnabled(False)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(10)
        layout.addStretch(1)
        layout.addWidget(title)
        layout.addWidget(subtitle)
        layout.addLayout(buttons)
        layout.addSpacing(8)
        layout.addWidget(self._recent_title)
        layout.addWidget(self.recent_list, 2)
        layout.addWidget(self._empty)
        layout.addWidget(hint)
        layout.addStretch(1)
        self.set_recent([])

    def set_recent(self, paths: list[str]) -> None:
        self.recent_list.clear()
        for path in paths:
            item = QListWidgetItem(Path(path).name)
            item.setToolTip(path)
            item.setData(_PATH_ROLE, path)
            self.recent_list.addItem(item)
        has = bool(paths)
        self.recent_list.setVisible(has)
        self._recent_title.setVisible(has)
        self._empty.setVisible(not has)

    def _on_item_activated(self, item: QListWidgetItem) -> None:
        path = item.data(_PATH_ROLE)
        if isinstance(path, str):
            self.recentRequested.emit(path)
