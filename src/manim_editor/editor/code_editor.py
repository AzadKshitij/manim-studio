"""Scintilla-based Python code editor.

The ``pyside6-scintilla`` binding is thin around Scintilla 5 and has a few sharp
edges (bytes arguments must be passed as ``str``, some ``SCI_GET*`` calls are
properties, the modify state is ``modify()``).  This wrapper is the single place
that knows about them, and it exposes a small, typed, Qt-signal-friendly API.
"""

from __future__ import annotations

import contextlib
import logging
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from pygments.style import Style
from pygments.styles import get_style_by_name
from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QFontDatabase, QKeyEvent
from PySide6.QtWidgets import QWidget
from pyside6_scintilla import Scintilla, ScintillaEdit

from manim_editor.ipc import as_bytes as _as_bytes
from manim_editor.python_highlight import (
    TokenKind,
    highlight_spans,
    kind_at,
    representative_token,
)

__all__ = ["CodeEditor"]

log = logging.getLogger(__name__)

# Scintilla constants (the bindings expose enums only for a subset).  The
# bundled Scintilla has no compiled lexers, so highlighting is applied through
# the container-styling API (SCI_STARTSTYLING/SCI_SETSTYLING) from Pygments.
SC_MARGIN_SYMBOL = 0
SC_MARGIN_NUMBER = 1
STYLE_DEFAULT = 32
STYLE_LINENUMBER = 33
_STYLE_MASK = 0x1F  # SC_STYLE_MASK: all five style bits

# Style ids for our token kinds (Scintilla reserves 0..31 for its own styles).
PY_COMMENT = 1
PY_NUMBER = 2
PY_STRING = 3
PY_KEYWORD = 5
PY_CLASSNAME = 8
PY_DEFNAME = 9
PY_OPERATOR = 10
PY_IDENTIFIER = 11
PY_DECORATOR = 15
PY_CONSTANT = 16
PY_BUILTIN = 17

# Pygments style names used to colour each token kind (chrome stays ours).
_PYGMENTS_DARK = "monokai"
_PYGMENTS_LIGHT = "friendly"

# Token kind -> Scintilla style id.
_ROLE_STYLE: dict[TokenKind, int] = {
    TokenKind.COMMENT: PY_COMMENT,
    TokenKind.STRING: PY_STRING,
    TokenKind.NUMBER: PY_NUMBER,
    TokenKind.KEYWORD: PY_KEYWORD,
    TokenKind.CONSTANT: PY_CONSTANT,
    TokenKind.NAME: PY_IDENTIFIER,
    TokenKind.FUNCTION: PY_DEFNAME,
    TokenKind.CLASS: PY_CLASSNAME,
    TokenKind.BUILTIN: PY_BUILTIN,
    TokenKind.DECORATOR: PY_DECORATOR,
    TokenKind.OPERATOR: PY_OPERATOR,
}

MARKER_ERROR = 0
INDIC_ERROR = 8

_MONOSPACE_CANDIDATES = ("Consolas", "Cascadia Mono", "Menlo", "DejaVu Sans Mono", "Courier New")


def _colorref(rgb: int) -> int:
    """Convert 0xRRGGBB to Scintilla's BGR colour-ref format."""
    red = (rgb >> 16) & 0xFF
    green = (rgb >> 8) & 0xFF
    blue = rgb & 0xFF
    return (blue << 16) | (green << 8) | red


def _parse_hex(value: str) -> int | None:
    """Parse a Pygments colour string (``#rgb``/``rrggbb``) to 0xRRGGBB."""
    value = value.lstrip("#")
    if len(value) == 3:
        value = "".join(ch * 2 for ch in value)
    try:
        return int(value, 16)
    except ValueError:
        return None


class CodeEditor(ScintillaEdit):
    """Python source editor with line numbers, completion, markers and themes."""

    saveRequested = Signal()
    fileDropped = Signal(str)
    cursorMoved = Signal(int, int)  # 1-based line, 1-based column
    modificationChanged = Signal(bool)
    completionsRequested = Signal(str, int)  # source, caret offset
    signaturesRequested = Signal(str, int)  # source, caret offset

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._completions: tuple[str, ...] = ()
        self._error_line: int | None = None
        self._last_modified = False
        # Pygments lexes the whole buffer, so coalesce edits into one restyle
        # per event-loop turn instead of re-lexing on every keystroke.
        self._restyle_timer = QTimer(self)
        self._restyle_timer.setSingleShot(True)
        self._restyle_timer.setInterval(0)
        self._restyle_timer.timeout.connect(self.restyle)
        # Debounce Jedi lookups while the user is mid-word; '.' fires at once.
        self._completion_timer = QTimer(self)
        self._completion_timer.setSingleShot(True)
        self._completion_timer.setInterval(120)
        self._completion_timer.timeout.connect(self._request_completions)
        self.setAcceptDrops(True)
        self._configure_base()
        self.apply_theme(dark=True)
        self._connect_signals()

    # ------------------------------------------------------------------
    # Configuration
    # ------------------------------------------------------------------

    def _configure_base(self) -> None:
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
        """Apply the editor chrome and Pygments token colours as Scintilla styles."""
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
        style = self._pygments_style(dark)
        for kind, style_id in _ROLE_STYLE.items():
            info = style.style_for_token(representative_token(kind))
            colour = _parse_hex(str(info.get("color") or ""))
            self.styleSetFore(style_id, _colorref(colour or palette["default_fg"]))
            self.styleSetBold(style_id, bool(info.get("bold")))
            self.styleSetItalic(style_id, bool(info.get("italic")))
        # Line-number margin.
        self.styleSetFore(STYLE_LINENUMBER, _colorref(palette["line_number_fg"]))
        self.styleSetBack(STYLE_LINENUMBER, _colorref(palette["gutter_bg"]))
        self.setMarginBackN(0, _colorref(palette["gutter_bg"]))
        self.setMarginBackN(1, _colorref(palette["gutter_bg"]))
        # Completion popup and calltip behaviour/chrome.
        self.callTipSetBack(_colorref(palette["default_bg"]))
        self.callTipSetFore(_colorref(palette["default_fg"]))
        self.autoCSetIgnoreCase(True)
        self.autoCSetDropRestOfWord(True)
        self.autoCSetMaxHeight(8)
        self.restyle()

    @staticmethod
    def _pygments_style(dark: bool) -> type[Style]:
        """Resolve the Pygments style object, falling back to ``default``."""
        name = _PYGMENTS_DARK if dark else _PYGMENTS_LIGHT
        try:
            return get_style_by_name(name)
        except Exception:  # pragma: no cover - unknown style name
            return get_style_by_name("default")

    def restyle(self) -> None:
        """Re-tokenise the buffer and push token styles into Scintilla.

        Scintilla styling counts UTF-8 bytes (positions and lengths), while
        Pygments spans are character offsets, so the gap and run lengths are
        encoded before being handed to ``setStyling``.
        """
        text = self._raw_text()
        self.startStyling(0, _STYLE_MASK)
        last = 0
        for start, end, kind in highlight_spans(text):
            if start > last:
                self.setStyling(_byte_length(text, last, start), STYLE_DEFAULT)
            self.setStyling(_byte_length(text, start, end), _ROLE_STYLE[kind])
            last = end
        if last < len(text):
            self.setStyling(_byte_length(text, last, len(text)), STYLE_DEFAULT)

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
        return self._raw_text().replace("\r\n", "\n").replace("\r", "\n")

    def _raw_text(self) -> str:
        """Buffer contents exactly as stored, preserving line endings."""
        return _as_bytes(self.getText(self.textLength())).decode("utf-8", "replace")

    def set_text(self, text: str) -> None:
        self.autoCCancel()
        self.callTipCancel()
        self.setText(text.replace("\r\n", "\n").replace("\r", "\n"))
        self.restyle()

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
        """Show the instant static list and ask Jedi for a richer one."""
        shown = self._show_static_completions()
        self._request_completions()
        return shown

    def _show_static_completions(self) -> bool:
        if not self._completions:
            return False
        position = self.current_position()
        self.autoCShow(position - self._word_start(position), " ".join(self._completions))
        return True

    def show_completions(self, offset: int, items: Sequence[str]) -> None:
        """Display a Jedi completion list for ``offset`` (ignored if stale)."""
        if offset != self.current_position() or not items:
            return
        self.autoCCancel()
        self.autoCShow(offset - self._word_start(offset), " ".join(items))

    def show_calltip(self, offset: int, text: str) -> None:
        """Display a signature calltip for ``offset`` (ignored if stale)."""
        if offset != self.current_position() or not text:
            return
        self.callTipShow(offset, text)

    def _request_completions(self) -> None:
        source = self.text()
        offset = self.current_position()
        if self._caret_in_ignored_token(source, offset):
            return
        self.completionsRequested.emit(source, offset)

    def _request_signatures(self) -> None:
        source = self.text()
        offset = self.current_position()
        if self._caret_in_ignored_token(source, offset):
            return
        self.signaturesRequested.emit(source, offset)

    @staticmethod
    def _caret_in_ignored_token(source: str, offset: int) -> bool:
        """True when the character before the caret is inside a string/comment."""
        if offset <= 0:
            return False
        return kind_at(source, offset - 1) in (TokenKind.STRING, TokenKind.COMMENT)

    def _word_start(self, position: int) -> int:
        start = position
        while start > 0:
            char = chr(self.charAt(start - 1))
            if char.isalnum() or char == "_":
                start -= 1
            else:
                break
        return start

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
        # Scintilla assigns style 0 to inserted text and does not emit
        # styleNeeded for every edit (e.g. while the completion popup is open),
        # so restyle ourselves after the edit settles.
        self._restyle_timer.start()
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
        if text == ".":
            self._completion_timer.start(0)  # member access: no debounce
        elif text == "(":
            self._request_signatures()
        elif text.isalpha() or text == "_":
            self._show_static_completions()
            self._completion_timer.start()


def _byte_length(text: str, start: int, end: int) -> int:
    """UTF-8 byte length of ``text[start:end]`` (Scintilla styling units)."""
    return len(text[start:end].encode("utf-8"))


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
}

_LIGHT: dict[str, Any] = {
    "default_fg": 0x1F1F1F,
    "default_bg": 0xFFFFFF,
    "gutter_bg": 0xF3F3F3,
    "line_number_fg": 0x7A7A7A,
}
