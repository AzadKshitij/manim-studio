"""Scintilla-based Python code editor.

The ``pyside6-scintilla`` binding is thin around Scintilla 5 and has a few sharp
edges (bytes arguments must be passed as ``str``, some ``SCI_GET*`` calls are
properties, the modify state is ``modify()``).  This wrapper is the single place
that knows about them, and it exposes a small, typed, Qt-signal-friendly API.
"""

from __future__ import annotations

import contextlib
import logging
import math
import re
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QFontDatabase, QKeyEvent
from PySide6.QtWidgets import QWidget
from pyside6_scintilla import Scintilla, ScintillaEdit

from manim_studio.ipc import as_bytes as _as_bytes
from manim_studio.python_highlight import (
    Span,
    TokenKind,
    highlight_spans,
    kind_at,
)
from manim_studio.theme import (
    PAPER,
    Theme,
    current_theme,
    derive,
)

__all__ = ["CodeEditor"]

log = logging.getLogger(__name__)

# Scintilla constants (the bindings expose enums only for a subset).  The
# bundled Scintilla has no compiled lexers, so highlighting is applied through
# the container-styling API (SCI_STARTSTYLING/SCI_SETSTYLING) from Pygments.
SC_MARGIN_SYMBOL = 0
SC_MARGIN_NUMBER = 1
SC_EOL_LF = 2
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
#: Whole-line tint and left-edge bar of the highlighted block (jump to code).
MARKER_BLOCK = 1
MARKER_BLOCK_EDGE = 2
INDIC_ERROR = 8
#: How long a jump-to-code highlight stays before it fades out.
BLOCK_HIGHLIGHT_MS = 3000
BLOCK_TINT_ALPHA = 70
SC_MOD_TEXT_CHANGED = 0x3  # SC_MOD_INSERTTEXT | SC_MOD_DELETETEXT
# A step the container put on the undo stack (addUndoAction) was undone or redone.
SC_MOD_CONTAINER = 0x40000
SC_PERFORMED_UNDO = 0x20
SC_PERFORMED_REDO = 0x40

_MONOSPACE_CANDIDATES = (
    "JetBrains Mono",
    "Cascadia Mono",
    "Consolas",
    "Cascadia Mono",
    "Menlo",
    "DejaVu Sans Mono",
    "Courier New",
)

# Buffer identifiers are offered as instant (worker-free) completions.  One
# identifier-shaped word token; the cap keeps the popup list bounded on huge
# generated files.
_IDENTIFIER_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_MAX_INSTANT_COMPLETIONS = 4000


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
    #: A timeline step on the undo stack was undone or redone; the window applies it.
    containerUndone = Signal()
    containerRedone = Signal()
    completionsRequested = Signal(str, int)  # source, caret offset
    signaturesRequested = Signal(str, int)  # source, caret offset

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._completions: tuple[str, ...] = ()
        self._error_line: int | None = None
        self._block_highlight: tuple[int, int] | None = None
        self._block_timer = QTimer(self)
        self._block_timer.setSingleShot(True)
        self._block_timer.setInterval(BLOCK_HIGHLIGHT_MS)
        self._block_timer.timeout.connect(self.clear_block_highlight)
        self._last_modified = False
        self._saved_text: str | None = None  # the text as it was last loaded or saved
        self._code_words: tuple[str, ...] = ()
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
        self.apply_theme()
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
        # Keep typed newlines as LF.  On Windows Scintilla defaults to CRLF, so
        # an Enter would insert "\r\n" while ``text()`` normalises to "\n"; the
        # caret byte offset would then drift past the caret for Jedi (see
        # ``_byte_to_char``).  Loaded files are already normalised to LF.
        self.setEOLMode(SC_EOL_LF)
        self.setIndentationGuides(True)
        self.setScrollWidthTracking(True)
        self.setCaretLineVisible(True)
        # Margin 0: line numbers. Margin 1: error glyphs.
        self.setMarginTypeN(0, SC_MARGIN_NUMBER)
        self.setMarginWidthN(0, 48)
        self.setMarginTypeN(1, SC_MARGIN_SYMBOL)
        self.setMarginWidthN(1, 16)
        self.setMarginSensitiveN(1, True)
        self.markerDefine(MARKER_ERROR, Scintilla.MarkerSymbol.Bookmark)
        self.markerDefine(MARKER_BLOCK, Scintilla.MarkerSymbol.Background)
        self.markerDefine(MARKER_BLOCK_EDGE, Scintilla.MarkerSymbol.LeftRect)
        self.indicSetStyle(INDIC_ERROR, 1)  # INDIC_SQUIGGLE
        self.setZoom(0)

    def apply_theme(self, theme: Theme | None = None, *, dark: bool | None = None) -> None:
        """Apply the editor chrome and token colours as Scintilla styles.

        ``theme`` defaults to the application's current theme; ``dark=False`` picks the
        built-in light theme instead (kept for callers that only know light/dark).
        """
        if theme is None:
            theme = PAPER if dark is False else current_theme()
        palette = _editor_palette(theme)
        family = self._monospace_family()
        try:
            self.styleSetFont(STYLE_DEFAULT, family)
        except Exception:  # pragma: no cover - font fallback
            self.styleSetFont(STYLE_DEFAULT, "Courier New")
        # Theme sizes are pixels; Scintilla sizes are points (96 dpi: 0.75 pt per px).
        self.styleSetSize(STYLE_DEFAULT, math.ceil(theme.metrics.editor_font_size * 0.75))
        self.styleSetFore(STYLE_DEFAULT, _colorref(palette["default_fg"]))
        self.styleSetBack(STYLE_DEFAULT, _colorref(palette["default_bg"]))
        self.styleClearAll()
        for kind, style_id in _ROLE_STYLE.items():
            colour, italic = _token_style(theme, kind)
            self.styleSetFore(style_id, _colorref(colour))
            self.styleSetBold(style_id, False)
            self.styleSetItalic(style_id, italic)
        self.setCaretFore(_colorref(palette["default_fg"]))
        self.markerSetFore(MARKER_ERROR, _colorref(palette["error_fg"]))
        self.markerSetBack(MARKER_ERROR, _colorref(palette["error"]))
        self.markerSetBack(MARKER_BLOCK, _colorref(palette["block"]))
        self.markerSetAlpha(MARKER_BLOCK, BLOCK_TINT_ALPHA)
        self.markerSetBack(MARKER_BLOCK_EDGE, _colorref(palette["block"]))
        self.indicSetFore(INDIC_ERROR, _colorref(palette["error"]))
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
        # Selection highlight.  Keep the Pygments token colours inside the
        # selection (setSelFore(False, ...)) and use a theme-appropriate band,
        # rather than Scintilla's opaque light-grey default which made the
        # light token colours unreadable on the dark theme.
        self.setSelBack(True, _colorref(palette["selection_bg"]))
        self.setSelFore(False, 0)
        self.setSelAlpha(palette["selection_alpha"])
        self.setAdditionalSelBack(_colorref(palette["selection_bg"]))
        self.setAdditionalSelAlpha(palette["selection_alpha"])
        # Current-line highlight follows the theme too (it used to be pinned dark).
        self.setCaretLineBack(_colorref(palette["caret_line_bg"]))
        self.setCaretLineBackAlpha(palette["caret_line_alpha"])
        self.restyle()

    def restyle(self) -> None:
        """Re-tokenise the buffer and push token styles into Scintilla.

        Scintilla styling counts UTF-8 bytes (positions and lengths), while
        Pygments spans are character offsets, so the gap and run lengths are
        encoded before being handed to ``setStyling``.  The same spans refresh
        the instant completion words, so no extra lexing happens per keystroke.
        """
        text = self._raw_text()
        spans = highlight_spans(text)
        self.startStyling(0, _STYLE_MASK)
        last = 0
        for start, end, kind in spans:
            if start > last:
                self.setStyling(_byte_length(text, last, start), STYLE_DEFAULT)
            self.setStyling(_byte_length(text, start, end), _ROLE_STYLE[kind])
            last = end
        if last < len(text):
            self.setStyling(_byte_length(text, last, len(text)), STYLE_DEFAULT)
        self._code_words = _code_identifiers(text, spans)

    @staticmethod
    def _monospace_family() -> str:
        available = set(QFontDatabase.families())
        for candidate in _MONOSPACE_CANDIDATES:
            if candidate in available:
                return candidate
        return "Courier New"

    def _connect_signals(self) -> None:
        self.modified.connect(self._on_modified)
        self.modified.connect(self._on_container_step)
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

    def replace_text(self, text: str) -> None:
        """Replace the whole buffer as a single undoable edit, keeping the caret line.

        Unlike :meth:`set_text` this stays on the undo stack, so a generated rewrite
        (such as applying timeline edits to the source) can be undone with Ctrl+Z.
        """
        normalised = text.replace("\r\n", "\n").replace("\r", "\n")
        line = self.current_line()
        self.autoCCancel()
        self.callTipCancel()
        self.beginUndoAction()
        try:
            self.targetWholeDocument()
            self.replaceTarget(len(normalised.encode("utf-8")), normalised)
        finally:
            self.endUndoAction()
        self.restyle()
        self.go_to_line(line)

    def load_file(self, path: str | Path) -> None:
        """Read a UTF-8 Python file into the buffer and reset modification state."""
        target = Path(path)
        self.set_text(target.read_text(encoding="utf-8"))
        self.emptyUndoBuffer()
        self.setSavePoint()
        self._saved_text = self.text()
        self.go_to_line(1)
        self.clear_error_marker()
        self._last_modified = False
        self.modificationChanged.emit(False)

    def reload_from_disk(self, text: str) -> None:
        """Show ``text`` (the file as it is on disk) as the saved text, keeping the undo history.

        The previous text stays on the undo stack as one step, so Ctrl+Z brings it back.
        """
        self.replace_text(text)
        self.setSavePoint()
        self._saved_text = self.text()
        self._last_modified = False
        self.modificationChanged.emit(False)

    def save_file(self, path: str | Path) -> Path:
        """Write the buffer to ``path`` and mark the save point."""
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        content = self.text()
        target.write_text(content, encoding="utf-8", newline="")
        self.setSavePoint()
        self._saved_text = content
        self._last_modified = False
        self.modificationChanged.emit(False)
        return target

    def settle_saved_state(self) -> None:
        """Make "modified" mean "the text differs from what was saved", after an undo or redo.

        A timeline step sits on Scintilla's undo stack without changing the text, but
        Scintilla counts moving off the save point as an edit.  Call this once an undo or
        redo has returned: when the text is the saved text, the save point moves here.
        """
        if self._saved_text is not None and self.is_modified() and self.text() == self._saved_text:
            self.setSavePoint()

    def _on_container_step(self, kind: int, *_args: Any) -> None:
        """Pass on undo/redo of a timeline step; Scintilla reports it inside undo()/redo()."""
        if not kind & SC_MOD_CONTAINER:
            return
        if kind & SC_PERFORMED_UNDO:
            self.containerUndone.emit()
        elif kind & SC_PERFORMED_REDO:
            self.containerRedone.emit()

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

    def insert_line_at(self, line: int, text: str) -> None:
        """Insert a new line of ``text`` at 1-based ``line``, preserving undo.

        Used for generated edits such as inserting ``self.wait(...)`` at a block
        boundary; Scintilla's ``insertText`` keeps the operation on the undo stack.
        """
        count = self.lineCount()
        index = max(0, min(line - 1, count))
        position = self.textLength() if index >= count else self.positionFromLine(index)
        content = text + "\n"
        if position == self.textLength() and position > 0 and not self._raw_text().endswith("\n"):
            content = "\n" + content
        self.insertText(position, content)
        self.go_to_line(index + 1)

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

    def highlight_lines(self, first: int, last: int | None = None) -> None:
        """Tint lines ``first``..``last`` (1-based, inclusive) as the jumped-to block.

        The tint covers whole lines and a bar marks the left edge.  It fades after
        :data:`BLOCK_HIGHLIGHT_MS`, and is dropped when the text is edited or another
        block is highlighted, so it never describes code that has moved.
        """
        self.clear_block_highlight()
        count = self.lineCount()
        start = max(1, min(first, count))
        end = max(start, min(last if last is not None else start, count))
        for line in range(start, end + 1):
            self.markerAdd(line - 1, MARKER_BLOCK)
            self.markerAdd(line - 1, MARKER_BLOCK_EDGE)
        self._block_highlight = (start, end)
        self._block_timer.start()

    def clear_block_highlight(self) -> None:
        self._block_timer.stop()
        if self._block_highlight is not None:
            self.markerDeleteAll(MARKER_BLOCK)
            self.markerDeleteAll(MARKER_BLOCK_EDGE)
        self._block_highlight = None

    @property
    def block_highlight(self) -> tuple[int, int] | None:
        """The highlighted ``(first, last)`` lines, or ``None``."""
        return self._block_highlight

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
        """Show the instant word list and ask Jedi for a richer one."""
        shown = self._show_static_completions()
        self._request_completions()
        return shown

    def _show_static_completions(self) -> bool:
        """Offer instant completions: API names plus in-buffer identifiers.

        In a member-access context (``self.``, ``grid.``) the global API list
        would be wrong, so only identifiers already seen in the buffer are
        offered until Jedi answers with the real member list.
        """
        position = self.current_position()
        words = self._instant_completions(member=self._in_member_access(position))
        if not words:
            return False
        self.autoCShow(position - self._word_start(position), " ".join(words))
        return True

    def _instant_completions(self, *, member: bool) -> tuple[str, ...]:
        merged: dict[str, None] = {} if member else dict.fromkeys(self._completions)
        for word in self._code_words:
            merged.setdefault(word, None)
            if len(merged) >= _MAX_INSTANT_COMPLETIONS:
                break
        return tuple(merged)

    def show_completions(self, offset: int, items: Sequence[str]) -> None:
        """Display a Jedi completion list for ``offset`` (ignored if stale).

        ``offset`` is a character offset (Jedi's unit); Scintilla positions are
        UTF-8 byte offsets, so the popup anchor is converted below.
        """
        if offset != self._char_position() or not items:
            return
        position = self.current_position()
        self.autoCCancel()
        self.autoCShow(position - self._word_start(position), " ".join(items))

    def show_calltip(self, offset: int, text: str) -> None:
        """Display a signature calltip for ``offset`` (ignored if stale)."""
        if offset != self._char_position() or not text:
            return
        self.callTipShow(self.current_position(), text)

    def _request_completions(self) -> None:
        source = self.text()
        offset = self._char_position()
        if self._caret_in_ignored_token(source, offset):
            return
        self.completionsRequested.emit(source, offset)

    def _request_signatures(self) -> None:
        source = self.text()
        offset = self._char_position()
        if self._caret_in_ignored_token(source, offset):
            return
        self.signaturesRequested.emit(source, offset)

    @staticmethod
    def _caret_in_ignored_token(source: str, offset: int) -> bool:
        """True when the character before the caret is inside a string/comment."""
        if offset <= 0:
            return False
        return kind_at(source, offset - 1) in (TokenKind.STRING, TokenKind.COMMENT)

    def _char_position(self) -> int:
        """Caret position in characters (Jedi's unit; Scintilla counts bytes)."""
        return self._byte_to_char(self.current_position())

    def _byte_to_char(self, byte_position: int) -> int:
        if byte_position <= 0:
            return 0
        prefix = self._raw_text().encode("utf-8")[:byte_position]
        # ``text()`` normalises CRLF/CR to LF, so an offset into it must count
        # the normalised characters, not the raw ones.  Without this a pasted
        # CRLF (or Scintilla's Windows EOL mode) would push the Jedi offset past
        # the caret and complete the wrong token.
        raw = prefix.decode("utf-8", "replace")
        return len(raw.replace("\r\n", "\n").replace("\r", "\n"))

    def _in_member_access(self, position: int) -> bool:
        """True when the word at ``position`` follows a dot (``obj.``)."""
        start = self._word_start(position)
        return start > 0 and chr(self.charAt(start - 1)) == "."

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
        if self._block_highlight is not None and _args and int(_args[0]) & SC_MOD_TEXT_CHANGED:
            self.clear_block_highlight()
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


def _code_identifiers(text: str, spans: Sequence[Span]) -> tuple[str, ...]:
    """Identifiers that occur in code, excluding ones inside strings/comments.

    The instant completion list is built from these; skipping prose keeps words
    like "the" or "mobject" (from comments and docstrings) out of the popup.
    """
    words: dict[str, None] = {}
    for start, end, kind in spans:
        if kind in (TokenKind.STRING, TokenKind.COMMENT):
            continue
        for word in _IDENTIFIER_RE.findall(text, start, end):
            if len(word) >= 2:
                words.setdefault(word, None)
                if len(words) >= _MAX_INSTANT_COMPLETIONS:
                    return tuple(words)
    return tuple(words)


def _dedent(lines: list[str]) -> list[str]:
    """Strip the common leading whitespace shared by all non-blank lines."""
    indents = [len(line) - len(line.lstrip()) for line in lines if line.strip()]
    common = min(indents) if indents else 0
    return [line[common:] if len(line) >= common else line for line in lines]


def _hex_int(colour: str) -> int:
    return int(colour[1:], 16)


def _editor_palette(theme: Theme) -> dict[str, Any]:
    """Scintilla chrome colours (0xRRGGBB ints) for ``theme``.

    Selection bands sit under the token colours (``setSelFore(False, ...)`` keeps the
    token foreground), so they are a muted accent that every token colour still reads
    on.  Scintilla's unstyled default is an opaque light grey that washed them out.
    """
    c = theme.colors
    d = derive(theme)
    return {
        "default_fg": _hex_int(c.text),
        "default_bg": _hex_int(c.editor),
        "gutter_bg": _hex_int(c.surface),
        "line_number_fg": _hex_int(c.faint),
        "selection_bg": _hex_int(d.selection),
        "selection_alpha": 256,
        "caret_line_bg": _hex_int(d.caret_line),
        "caret_line_alpha": 256,
        "error": _hex_int(c.err),
        "block": _hex_int(c.accent),
        "error_fg": _hex_int(c.editor),
    }


def _token_style(theme: Theme, kind: TokenKind) -> tuple[int, bool]:
    """Colour (0xRRGGBB) and italic flag for a token kind under ``theme``."""
    syntax = theme.syntax
    colours = {
        TokenKind.COMMENT: syntax.comment,
        TokenKind.STRING: syntax.string,
        TokenKind.NUMBER: syntax.number,
        TokenKind.KEYWORD: syntax.keyword,
        TokenKind.CONSTANT: syntax.builtin,
        TokenKind.FUNCTION: syntax.function,
        TokenKind.CLASS: syntax.class_,
        TokenKind.BUILTIN: syntax.builtin,
        TokenKind.DECORATOR: syntax.builtin,
    }
    return _hex_int(colours.get(kind, theme.colors.text)), kind is TokenKind.COMMENT
