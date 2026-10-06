"""Python syntax-highlighting spans, powered by Pygments.

The bundled ``pyside6-scintilla`` wheel ships Scintilla without any compiled
lexers, so the editor cannot use ``SCI_SETLEXER``.  Instead this module runs the
Pygments ``PythonLexer`` over the whole buffer and returns styled runs, which
the editor applies through Scintilla's container-lexer styling API.

Lexing the full document (rather than line by line) means multi-line strings,
f-strings, decorators and line continuations tokenise correctly with no
per-line state to maintain.  The module has no Qt, ``manimlib`` or Scintilla
dependency, so it is fully unit-testable in isolation.
"""

from __future__ import annotations

import enum

from pygments.lexers import PythonLexer
from pygments.token import Token, _TokenType

__all__ = ["Span", "TokenKind", "highlight_spans", "representative_token"]


class TokenKind(enum.Enum):
    """A rendering class; each maps to one Scintilla style in the editor."""

    COMMENT = "comment"
    STRING = "string"
    NUMBER = "number"
    KEYWORD = "keyword"
    CONSTANT = "constant"
    NAME = "name"
    FUNCTION = "function"
    CLASS = "class"
    BUILTIN = "builtin"
    DECORATOR = "decorator"
    OPERATOR = "operator"


#: ``(start, end, kind)`` with character offsets into the original text.
Span = tuple[int, int, TokenKind]

#: Pygments token whose colour/weight defines the look of each kind.
_KIND_TOKEN: dict[TokenKind, _TokenType] = {
    TokenKind.COMMENT: Token.Comment,
    TokenKind.STRING: Token.String,
    TokenKind.NUMBER: Token.Number,
    TokenKind.KEYWORD: Token.Keyword,
    TokenKind.CONSTANT: Token.Keyword.Constant,
    TokenKind.NAME: Token.Name,
    TokenKind.FUNCTION: Token.Name.Function,
    TokenKind.CLASS: Token.Name.Class,
    TokenKind.BUILTIN: Token.Name.Builtin,
    TokenKind.DECORATOR: Token.Name.Decorator,
    TokenKind.OPERATOR: Token.Operator,
}

# Specific Pygments tokens mapped to a kind, deepest first so a subtype wins
# over its parent (``Name.Function`` before ``Name``, ``Keyword.Constant``
# before ``Keyword``).  Subtype membership is tested with ``token in known``.
_KIND_ORDER: tuple[tuple[_TokenType, TokenKind], ...] = (
    (Token.Name.Function, TokenKind.FUNCTION),
    (Token.Name.Builtin, TokenKind.BUILTIN),
    (Token.Name.Class, TokenKind.CLASS),
    (Token.Name.Exception, TokenKind.CLASS),
    (Token.Name.Decorator, TokenKind.DECORATOR),
    (Token.Name.Constant, TokenKind.CONSTANT),
    (Token.Name, TokenKind.NAME),
    (Token.Keyword.Constant, TokenKind.CONSTANT),
    (Token.Keyword, TokenKind.KEYWORD),
    (Token.String, TokenKind.STRING),
    (Token.Comment, TokenKind.COMMENT),
    (Token.Number, TokenKind.NUMBER),
    (Token.Operator, TokenKind.OPERATOR),
    (Token.Punctuation, TokenKind.OPERATOR),
)

_LEXER = PythonLexer()


def representative_token(kind: TokenKind) -> _TokenType:
    """The Pygments token whose style defines the look of ``kind``."""
    return _KIND_TOKEN[kind]


def highlight_spans(text: str) -> list[Span]:
    """Tokenise ``text`` and return ``(start, end, kind)`` character spans.

    Spans are in source order, never extend past ``len(text)`` (Pygments appends
    a trailing newline when the source lacks one), and adjacent runs of the same
    kind are merged so the editor makes one ``setStyling`` call per run.
    """
    spans: list[Span] = []
    length = len(text)
    for index, token_type, value in _LEXER.get_tokens_unprocessed(text):
        if not value or index >= length:
            continue
        kind = _kind_for(token_type)
        if kind is None:
            continue
        if kind is TokenKind.NAME:
            kind = _refine_name(value)
        end = min(index + len(value), length)
        if spans and spans[-1][1] == index and spans[-1][2] is kind:
            spans[-1] = (spans[-1][0], end, kind)
        else:
            spans.append((index, end, kind))
    return spans


def _refine_name(value: str) -> TokenKind:
    """Apply Python naming conventions Pygments cannot know from syntax alone.

    ``LEFT``/``MED_SMALL_BUFF`` become constants and ``Circle``/``Demo`` become
    types, which is what readers expect even though the lexer only sees a name.
    """
    if len(value) > 1 and value.isupper():
        return TokenKind.CONSTANT
    if len(value) > 1 and value[:1].isupper():
        return TokenKind.CLASS
    return TokenKind.NAME


def _kind_for(token_type: _TokenType) -> TokenKind | None:
    for known, kind in _KIND_ORDER:
        if token_type in known:
            return kind
    return None
