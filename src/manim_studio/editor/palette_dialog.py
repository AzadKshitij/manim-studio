"""Command palette: a popup that filters and runs commands by name.

Type to filter, Up/Down to move, Enter to run, Esc to close.  The palette holds no
commands of its own: it asks ``items`` for them each time it opens, so enabled states,
scenes, recent files and blocks are always current.  Ranking lives in
:mod:`manim_studio.command_palette`; this module only draws and dispatches.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from PySide6.QtCore import QPoint, QSize, Qt
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QVBoxLayout,
    QWidget,
)

from manim_studio import command_palette
from manim_studio.command_palette import PaletteEntry
from manim_studio.theme import ThemeColors, current_theme

__all__ = ["CommandPalette", "PaletteItem"]

HINT = "Up/Down move  ·  Enter run  ·  Esc close  ·  > actions  ·  @ blocks  ·  ? shortcuts"


@dataclass(frozen=True)
class PaletteItem:
    """A palette row plus the callable that runs it."""

    entry: PaletteEntry
    run: Callable[[], object]


class _PaletteRow(QWidget):
    """One result: group, title, detail (or the reason it is unavailable), shortcut."""

    def __init__(self, entry: PaletteEntry, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        group = QLabel(entry.group, self)
        group.setObjectName("paletteGroup")
        group.setFixedWidth(92)
        title = QLabel(entry.title, self)
        title.setObjectName("paletteTitle")
        title.setEnabled(entry.enabled)
        detail_text = entry.detail if entry.enabled else (entry.reason or "unavailable")
        detail = QLabel(detail_text, self)
        detail.setObjectName("paletteDetail")
        detail.setVisible(bool(detail_text))
        shortcut = QLabel(entry.shortcut, self)
        shortcut.setObjectName("paletteKey")
        shortcut.setVisible(bool(entry.shortcut))

        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 0, 12, 0)
        layout.setSpacing(10)
        layout.addWidget(group)
        layout.addWidget(title, 1)
        layout.addWidget(detail)
        layout.addWidget(shortcut)


class CommandPalette(QFrame):
    """A centred popup over the editor window that lists and runs commands."""

    ROW_HEIGHT = 36

    def __init__(self, parent: QWidget, items: Callable[[], list[PaletteItem]]) -> None:
        super().__init__(parent, Qt.WindowType.Popup | Qt.WindowType.FramelessWindowHint)
        self.setObjectName("commandPalette")
        self._items_source = items
        self._items: dict[str, PaletteItem] = {}
        self._shown: list[PaletteEntry] = []
        self._recent: list[str] = []

        self._input = QLineEdit(self)
        self._input.setObjectName("paletteInput")
        self._input.setPlaceholderText("Run a command, scene or block")
        self._list = QListWidget(self)
        self._list.setObjectName("paletteList")
        self._list.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._footer = QLabel(HINT, self)
        self._footer.setObjectName("paletteFooter")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 8)
        layout.setSpacing(6)
        layout.addWidget(self._input)
        layout.addWidget(self._list, 1)
        layout.addWidget(self._footer)
        self.setStyleSheet(_stylesheet(current_theme().colors))
        self.setFixedSize(640, 440)

        self._input.textChanged.connect(self._refresh)
        self._input.returnPressed.connect(self._run_current)
        self._list.itemClicked.connect(self._run_clicked)
        self.hide()

    # ------------------------------------------------------------------
    # Read-only views (for callers and tests)
    # ------------------------------------------------------------------

    @property
    def input(self) -> QLineEdit:
        return self._input

    @property
    def shown(self) -> tuple[PaletteEntry, ...]:
        """The entries currently listed, in display order."""
        return tuple(self._shown)

    @property
    def current_entry(self) -> PaletteEntry | None:
        row = self._list.currentRow()
        return self._shown[row] if 0 <= row < len(self._shown) else None

    # ------------------------------------------------------------------
    # Opening and filtering
    # ------------------------------------------------------------------

    def open_palette(self, prefix: str = "") -> None:
        """Show the palette with ``prefix`` as the starting query (e.g. ``"?"``)."""
        self._items = {item.entry.key: item for item in self._items_source()}
        self._footer.setText(HINT)
        self._input.setText(prefix)
        self._refresh()
        parent = self.parentWidget()
        if parent is not None:
            x = (parent.width() - self.width()) // 2
            self.move(parent.mapToGlobal(QPoint(x, 80)))
        self.show()
        self.raise_()
        self._input.setFocus()
        self._input.selectAll()

    def _refresh(self) -> None:
        entries = [item.entry for item in self._items.values()]
        self._shown = command_palette.rank(entries, self._input.text(), recent=self._recent)
        self._list.clear()
        for entry in self._shown:
            row_item = QListWidgetItem(self._list)
            row_item.setSizeHint(QSize(0, self.ROW_HEIGHT))
            self._list.setItemWidget(row_item, _PaletteRow(entry, self._list))
        if not self._shown:
            empty = QListWidgetItem("No matches", self._list)
            empty.setFlags(Qt.ItemFlag.NoItemFlags)
            return
        self._list.setCurrentRow(0)

    # ------------------------------------------------------------------
    # Keyboard and mouse
    # ------------------------------------------------------------------

    def keyPressEvent(self, event: QKeyEvent) -> None:
        key = event.key()
        if key == Qt.Key.Key_Down:
            self._move(1)
        elif key == Qt.Key.Key_Up:
            self._move(-1)
        elif key == Qt.Key.Key_Escape:
            self.hide()
        else:
            super().keyPressEvent(event)

    def _move(self, step: int) -> None:
        if not self._shown:
            return
        row = min(max(self._list.currentRow() + step, 0), len(self._shown) - 1)
        self._list.setCurrentRow(row)
        self._list.scrollToItem(self._list.item(row))

    def _run_current(self) -> None:
        entry = self.current_entry
        if entry is not None:
            self._activate(entry)

    def _run_clicked(self, row_item: QListWidgetItem) -> None:
        row = self._list.row(row_item)
        if 0 <= row < len(self._shown):
            self._activate(self._shown[row])

    def _activate(self, entry: PaletteEntry) -> None:
        item = self._items.get(entry.key)
        if item is None:
            return
        if not entry.enabled:
            # Stay open and say why, so a greyed row is never a silent no-op.
            self._footer.setText(f"{entry.title} is unavailable: {entry.reason or 'not right now'}")
            return
        self._recent = command_palette.remember(self._recent, entry.key)
        self.hide()
        item.run()


def _stylesheet(colors: ThemeColors) -> str:
    return f"""
QFrame#commandPalette {{
    background: {colors.surface};
    border: 1px solid {colors.border_strong};
    border-radius: 10px;
}}
QLineEdit#paletteInput {{
    background: {colors.editor};
    border: 1px solid {colors.border};
    border-radius: 6px;
    padding: 6px 10px;
    color: {colors.text};
}}
QLineEdit#paletteInput:focus {{ border-color: {colors.accent}; }}
QListWidget#paletteList {{ background: transparent; border: none; outline: none; }}
QListWidget#paletteList::item {{ border-radius: 6px; padding: 0; }}
QListWidget#paletteList::item:selected {{ background: {colors.raised}; }}
QLabel#paletteGroup {{ color: {colors.faint}; }}
QLabel#paletteTitle {{ color: {colors.text}; }}
QLabel#paletteDetail {{ color: {colors.muted}; }}
QLabel#paletteKey {{
    color: {colors.muted};
    border: 1px solid {colors.border};
    border-radius: 4px;
    padding: 0 6px;
}}
QLabel#paletteFooter {{ color: {colors.faint}; padding: 0 4px; }}
"""
