"""Live-shell lines: how severe each one is, and which ones a filter shows (pure, stdlib only).

Every line the shell receives gets a :class:`Level`.  Output from the preview host and from
manimgl starts at ``DEBUG`` (routine chatter), the editor's own messages start at ``INFO``
(the prompt, results, notes); error and warning patterns raise a line to ``WARNING`` or
``ERROR`` whatever its source.  A filter shows its level and everything above it, so the
``All`` filter is the only one that shows routine chatter.  The buffer is bounded: the
window re-renders the view from it when the filter or the search changes.
"""

from __future__ import annotations

import re
from collections import deque
from dataclasses import dataclass
from enum import IntEnum

__all__ = [
    "DEFAULT_LIMIT",
    "FILTERS",
    "Entry",
    "Level",
    "ShellLog",
    "classify",
    "shows",
]

#: Lines kept for re-rendering; older lines fall off the front.
DEFAULT_LIMIT = 5000


class Level(IntEnum):
    """How much a line matters; a filter shows its level and every level above it."""

    DEBUG = 0  # routine output of the preview host and of manimgl
    INFO = 1  # the editor's own messages: the prompt, results, notes
    WARNING = 2
    ERROR = 3


#: The toolbar's filters, in order: label and the lowest level they show.
FILTERS: tuple[tuple[str, Level], ...] = (
    ("All", Level.DEBUG),
    ("Info", Level.INFO),
    ("Warnings", Level.WARNING),
    ("Errors", Level.ERROR),
)

# An ``ERROR:`` prefix (the host's own error lines), a shell ``!`` error, a traceback header,
# or a Python exception line such as ``NameError: ...``.
_ERROR = re.compile(
    r"^(?:ERROR\b|!\s|Traceback \(most recent call last\)|[A-Za-z_][\w.]*(?:Error|Exception)\b:)"
)
_FAILED = re.compile(r"\bfailed\b", re.IGNORECASE)
_WARNING = re.compile(r"^\s*(?:WARNING\b|warning:)|\b\w*Warning:", re.IGNORECASE)


def classify(text: str, base: Level = Level.INFO) -> Level:
    """The level of a line (or a multi-line chunk): ``base``, raised by what the text says."""
    level = base
    for line in text.splitlines() or [""]:
        if _ERROR.search(line) or _FAILED.search(line):
            return Level.ERROR
        if _WARNING.search(line):
            level = max(level, Level.WARNING)
    return level


@dataclass(frozen=True, slots=True)
class Entry:
    """One chunk of shell output and its level."""

    text: str
    level: Level


def shows(entry: Entry, level: Level, query: str = "") -> bool:
    """True when a filter at ``level`` with the search text ``query`` shows ``entry``."""
    if entry.level < level:
        return False
    needle = query.strip().casefold()
    return not needle or needle in entry.text.casefold()


class ShellLog:
    """The bounded history of shell output, classified as it arrives."""

    def __init__(self, limit: int = DEFAULT_LIMIT) -> None:
        self._entries: deque[Entry] = deque(maxlen=limit)

    def __len__(self) -> int:
        return len(self._entries)

    def add(self, text: str, base: Level = Level.INFO) -> Entry:
        """Record ``text`` (classified from ``base``) and return its entry."""
        entry = Entry(text, classify(text, base))
        self._entries.append(entry)
        return entry

    def visible(self, level: Level, query: str = "") -> list[Entry]:
        """The entries a filter at ``level`` with ``query`` shows, oldest first."""
        return [entry for entry in self._entries if shows(entry, level, query)]
