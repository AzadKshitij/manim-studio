"""Scintilla-based Python code editor.

The ``pyside6-scintilla`` binding is thin around Scintilla 5 and has a few sharp
edges (bytes arguments must be passed as ``str``, some ``SCI_GET*`` calls are
properties, the modify state is ``modify()``).  This wrapper is the single place
that knows about them, and it exposes a small, typed, Qt-signal-friendly API.
"""

from __future__ import annotations

import contextlib
import logging
from pathlib import Path
from typing import Any

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFontDatabase, QKeyEvent
from PySide6.QtWidgets import QWidget
from pyside6_scintilla import Scintilla, ScintillaEdit

from manim_editor.ipc import as_bytes as _as_bytes

__all__ = ["CodeEditor"]

log = logging.getLogger(__name__)

# Scintilla constants (the bindings expose enums only for a subset).
SCLEX_PYTHON = 2
SC_MARGIN_SYMBOL = 0
SC_MARGIN_NUMBER = 1
STYLE_DEFAULT = 32
STYLE_LINENUMBER = 33

# LexPython style ids.
PY_COMMENT = 1
PY_NUMBER = 2
PY_STRING = 3
PY_CHARACTER = 4
PY_KEYWORD = 5
PY_TRIPLE = 6
PY_TRIPLEDOUBLE = 7
PY_CLASSNAME = 8
PY_DEFNAME = 9
PY_OPERATOR = 10
PY_IDENTIFIER = 11
PY_COMMENTBLOCK = 12
PY_STRINGEOL = 13
PY_DECORATOR = 15

MARKER_ERROR = 0
INDIC_ERROR = 8

_MONOSPACE_CANDIDATES = ("Consolas", "Cascadia Mono", "Menlo", "DejaVu Sans Mono", "Courier New")


def _colorref(rgb: int) -> int:
    """Convert 0xRRGGBB to Scintilla's BGR colour-ref format."""
    red = (rgb >> 16) & 0xFF
    green = (rgb >> 8) & 0xFF
    blue = rgb & 0xFF
    return (blue << 16) | (green << 8) | red


class CodeEditor(ScintillaEdit):
    """Python source editor with line numbers, completion, markers and themes."""

    saveRequested = Signal()
    fileDropped = Signal(str)
    cursorMoved = Signal(int, int)  # 1-based line, 1-based column
    modificationChanged = Signal(bool)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._completions: tuple[str, ...] = ()
        self._error_line: int | None = None
        self._last_modified = False
        self.setAcceptDrops(True)
        self._configure_base()
        self.apply_theme(dark=True)
        self._connect_signals()

    # ------------------------------------------------------------------
    # Configuration
    # ------------------------------------------------------------------

    def _configure_base(self) -> None:
        self.lexer = SCLEX_PYTHON  # type: ignore[method-assign, assignment]
        self.setIndent(4)
        self.setTabWidth(4)
        self.setUseTabs(False)
        self.setTabIndents(True)
        self.setBackSpaceUnIndents(True)
        self.setIndentationGuides(True)
        self.setScrollWidthTracking(True)
        self.setCaretLineVisible(True)
        self.setCaretLineBack(_colorref(0x2A2D2E))
        self.setCaretFore(_colorref(0xFFFFFF))
        # Margin 0: line numbers. Margin 1: error glyphs.
        self.setMarginTypeN(0, SC_MARGIN_NUMBER)
        self.setMarginWidthN(0, 48)
        self.setMarginTypeN(1, SC_MARGIN_SYMBOL)
        self.setMarginWidthN(1, 16)
        self.setMarginSensitiveN(1, True)
        self.markerDefine(MARKER_ERROR, Scintilla.MarkerSymbol.Bookmark)
        self.markerSetFore(MARKER_ERROR, _colorref(0xFFFFFF))
        self.markerSetBack(MARKER_ERROR, _colorref(0xCF5044))
        self.indicSetStyle(INDIC_ERROR, 1)  # INDIC_SQUIGGLE
        self.indicSetFore(INDIC_ERROR, _colorref(0xCF5044))
        self.setZoom(0)

    def apply_theme(self, *, dark: bool = True) -> None:
        """Apply the editor colour theme to the default style and lexer styles."""
        palette = _DARK if dark else _LIGHT
        family = self._monospace_family()
        try:
            self.styleSetFont(STYLE_DEFAULT, family)
        except Exception:  # pragma: no cover - font fallback
            self.styleSetFont(STYLE_DEFAULT, "Courier New")
        self.styleSetSize(STYLE_DEFAULT, 11)
        self.styleSetFore(STYLE_DEFAULT, _colorref(palette["default_fg"]))
        self.styleSetBack(STYLE_DEFAULT, _colorref(palette["default_bg"]))
        self.styleClearAll()
        for style, color in palette["styles"].items():
            self.styleSetFore(style, _colorref(color))
        self.styleSetBold(PY_KEYWORD, True)
        self.styleSetBold(PY_CLASSNAME, True)
        self.styleSetItalic(PY_COMMENT, True)
        self.styleSetItalic(PY_COMMENTBLOCK, True)
        # Line-number margin.
        self.styleSetFore(STYLE_LINENUMBER, _colorref(palette["line_number_fg"]))
        self.styleSetBack(STYLE_LINENUMBER, _colorref(palette["gutter_bg"]))
        self.setMarginBackN(0, _colorref(palette["gutter_bg"]))
        self.setMarginBackN(1, _colorref(palette["gutter_bg"]))

    @staticmethod
    def _monospace_family() -> str:
        available = set(QFontDatabase.families())
        for candidate in _MONOSPACE_CANDIDATES:
            if candidate in available:
                return candidate
        return "Courier New"

    def _connect_signals(self) -> None:
        self.modified.connect(self._on_modified)
        self.savePointChanged.connect(self._on_save_point)
        self.updateUi.connect(self._on_update_ui)
        self.charAdded.connect(self._on_char_added)

    # ------------------------------------------------------------------
    # File handling
    # ------------------------------------------------------------------

    def text(self) -> str:
        """Current buffer as a normalised (LF) Python string."""
        raw = _as_bytes(self.getText(self.textLength())).decode("utf-8", "replace")
        return raw.replace("\r\n", "\n").replace("\r", "\n")

    def set_text(self, text: str) -> None:
        self.setText(text.replace("\r\n", "\n").replace("\r", "\n"))

    def load_file(self, path: str | Path) -> None:
        """Read a UTF-8 Python file into the buffer and reset modification state."""
        target = Path(path)
        self.set_text(target.read_text(encoding="utf-8"))
        self.emptyUndoBuffer()
        self.setSavePoint()
        self.go_to_line(1)
        self.clear_error_marker()
        self._last_modified = False
        self.modificationChanged.emit(False)

    def save_file(self, path: str | Path) -> Path:
        """Write the buffer to ``path`` and mark the save point."""
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(self.text(), encoding="utf-8", newline="")
        self.setSavePoint()
        self._last_modified = False
        self.modificationChanged.emit(False)
        return target

    def is_modified(self) -> bool:
        try:
            return bool(self.modify())
        except Exception:  # pragma: no cover - defensive
            return self._last_modified

    # ------------------------------------------------------------------
    # Navigation and markers
    # ------------------------------------------------------------------

    def current_position(self) -> int:
        value = self.currentPos
        return int(value() if callable(value) else value)

    def current_line(self) -> int:
        """1-based line number of the caret."""
        return self.lineFromPosition(self.current_position()) + 1

    def current_column(self) -> int:
        """1-based column of the caret."""
        position = self.current_position()
        line_start = self.positionFromLine(self.lineFromPosition(position))
        return position - line_start + 1

    def go_to_line(self, line: int) -> None:
        """1-based line navigation with the caret scrolled into view."""
        self.gotoLine(max(0, min(line - 1, self.lineCount() - 1)))
        self.setVisiblePolicy(1, 1)
        self.scrollCaret()

    def set_error_marker(self, line: int, message: str = "") -> None:
        """Mark one line as erroneous (temporary marker + squiggle indicator)."""
        self.clear_error_marker()
        index = max(0, line - 1)
        if index >= self.lineCount():
            return
        self.markerAdd(index, MARKER_ERROR)
        start = self.positionFromLine(index)
        end = self.lineEndPosition(index)
        self.setIndicatorCurrent(INDIC_ERROR)
        self.indicatorFillRange(start, max(1, end - start))
        self._error_line = line
        annotate = getattr(self, "annotationSetText", None)
        if message and annotate is not None:
            try:
                self.setAnnotationVisible(1)  # type: ignore[attr-defined]
                annotate(index, message)
                self.annotationSetStyle(index, STYLE_DEFAULT)
            except Exception:  # pragma: no cover - annotations are optional
                log.debug("annotations unavailable in this Scintilla build")

    def clear_error_marker(self) -> None:
        self.markerDeleteAll(MARKER_ERROR)
        self.setIndicatorCurrent(INDIC_ERROR)
        self.indicatorClearRange(0, self.textLength())
        if self._error_line is not None and hasattr(self, "annotationClearAll"):
            with contextlib.suppress(Exception):
                self.annotationClearAll()
        self._error_line = None

    @property
    def error_line(self) -> int | None:
        return self._error_line

    # ------------------------------------------------------------------
    # Snippets and completion
    # ------------------------------------------------------------------

    def insert_snippet(self, code: str) -> None:
        """Insert a snippet at the caret, re-indented to the current block level."""
        lines = code.rstrip("\n").split("\n")
        indent = self._current_line_indent()
        dedented = _dedent(lines)
        rendered = "\n".join(indent + line if line.strip() else "" for line in dedented)
        if self.getSelText():
            self.replaceSel(rendered)
        else:
            self.insertText(self.current_position(), rendered)

    def _current_line_indent(self) -> str:
        line = self.lineFromPosition(self.current_position())
        start = self.positionFromLine(line)
        end = self.lineIndentPosition(line)
        return _as_bytes(self.get_text_range(start, end)).decode("utf-8", "replace")

    def set_completions(self, names: tuple[str, ...] | list[str]) -> None:
        self._completions = tuple(names)

    def trigger_completion(self) -> bool:
        """Show the completion list filtered by the word being typed."""
        if not self._completions:
            return False
        position = self.current_position()
        start = position
        while start > 0:
            char = chr(self.charAt(start - 1))
            if char.isalnum() or char == "_":
                start -= 1
            else:
                break
        prefix_length = position - start
        self.autoCShow(prefix_length, " ".join(self._completions))
        return True

    # ------------------------------------------------------------------
    # Qt events
    # ------------------------------------------------------------------

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() == Qt.Key.Key_S and event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            self.saveRequested.emit()
            event.accept()
            return
        if (
            event.key() in (Qt.Key.Key_Space, Qt.Key.Key_Tab)
            and event.modifiers() & Qt.KeyboardModifier.ControlModifier
        ):
            self.trigger_completion()
            event.accept()
            return
        super().keyPressEvent(event)

    def dragEnterEvent(self, event: Any) -> None:
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            super().dragEnterEvent(event)

    def dropEvent(self, event: Any) -> None:
        urls = event.mimeData().urls()
        if urls:
            path = urls[0].toLocalFile()
            if path:
                self.fileDropped.emit(path)
                event.acceptProposedAction()
                return
        super().dropEvent(event)

    # ------------------------------------------------------------------
    # Signal plumbing
    # ------------------------------------------------------------------

    def _on_modified(self, *_args: Any) -> None:
        modified = self.is_modified()
        if modified != self._last_modified:
            self._last_modified = modified
            self.modificationChanged.emit(modified)

    def _on_save_point(self) -> None:
        self._last_modified = False
        self.modificationChanged.emit(False)

    def _on_update_ui(self, *_args: Any) -> None:
        self.cursorMoved.emit(self.current_line(), self.current_column())

    def _on_char_added(self, char: int) -> None:
        try:
            text = chr(char)
        except ValueError:  # pragma: no cover - defensive
            return
        if text.isalpha() or text == "_":
            self.trigger_completion()


def _dedent(lines: list[str]) -> list[str]:
    """Strip the common leading whitespace shared by all non-blank lines."""
    indents = [len(line) - len(line.lstrip()) for line in lines if line.strip()]
    common = min(indents) if indents else 0
    return [line[common:] if len(line) >= common else line for line in lines]


_DARK: dict[str, Any] = {
    "default_fg": 0xD4D4D4,
    "default_bg": 0x1E1E1E,
    "gutter_bg": 0x252526,
    "line_number_fg": 0x858585,
    "styles": {
        PY_COMMENT: 0x6A9955,
        PY_COMMENTBLOCK: 0x6A9955,
        PY_NUMBER: 0xB5CEA8,
        PY_STRING: 0xCE9178,
        PY_CHARACTER: 0xCE9178,
        PY_TRIPLE: 0xCE9178,
        PY_TRIPLEDOUBLE: 0xCE9178,
        PY_KEYWORD: 0x569CD6,
        PY_CLASSNAME: 0x4EC9B0,
        PY_DEFNAME: 0xDCDCAA,
        PY_OPERATOR: 0xD4D4D4,
        PY_IDENTIFIER: 0x9CDCFE,
        PY_STRINGEOL: 0xCE9178,
        PY_DECORATOR: 0xDCDCAA,
    },
}

_LIGHT: dict[str, Any] = {
    "default_fg": 0x1F1F1F,
    "default_bg": 0xFFFFFF,
    "gutter_bg": 0xF3F3F3,
    "line_number_fg": 0x7A7A7A,
    "styles": {
        PY_COMMENT: 0x008000,
        PY_COMMENTBLOCK: 0x008000,
        PY_NUMBER: 0x098658,
        PY_STRING: 0xA31515,
        PY_CHARACTER: 0xA31515,
        PY_TRIPLE: 0xA31515,
        PY_TRIPLEDOUBLE: 0xA31515,
        PY_KEYWORD: 0x0000FF,
        PY_CLASSNAME: 0x267F99,
        PY_DEFNAME: 0x795E26,
        PY_OPERATOR: 0x1F1F1F,
        PY_IDENTIFIER: 0x001080,
        PY_STRINGEOL: 0xA31515,
        PY_DECORATOR: 0x795E26,
    },
}
