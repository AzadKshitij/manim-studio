"""Command palette rules: entries, query scopes, fuzzy ranking and recency.

Pure (no Qt).  The editor builds :class:`PaletteEntry` rows and keeps the action for
each key on its own side; this module only decides what matches and in what order.

Query prefixes narrow the search: ``>`` actions and timeline operations, ``@`` blocks,
``?`` anything with a keyboard shortcut.  With no prefix every group is searched.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass

__all__ = [
    "ACTIONS",
    "BLOCKS",
    "RECENT",
    "SCENES",
    "TIMELINE",
    "PaletteEntry",
    "ParsedQuery",
    "fuzzy_score",
    "parse_query",
    "rank",
    "remember",
]

ACTIONS = "Actions"
TIMELINE = "Timeline"
SCENES = "Scenes"
RECENT = "Recent files"
BLOCKS = "Blocks"

_SCOPE_BY_PREFIX = {">": "actions", "@": "blocks", "?": "shortcuts"}
#: Groups each scope searches; ``None`` means every group.
_GROUPS_BY_SCOPE: dict[str, frozenset[str] | None] = {
    "all": None,
    "actions": frozenset({ACTIONS, TIMELINE}),
    "blocks": frozenset({BLOCKS}),
    "shortcuts": None,  # filtered by shortcut instead, see _in_scope
}


@dataclass(frozen=True, slots=True)
class PaletteEntry:
    """One row the palette can show and run.  ``key`` is the stable id the caller maps
    back to an action."""

    key: str
    title: str
    group: str
    detail: str = ""
    shortcut: str = ""
    enabled: bool = True
    reason: str = ""  # why it is disabled; shown in place of the detail
    keywords: str = ""  # searched but not shown


@dataclass(frozen=True, slots=True)
class ParsedQuery:
    scope: str
    term: str


def parse_query(text: str) -> ParsedQuery:
    """Split a raw query into its scope (from a leading prefix) and the term to match."""
    stripped = text.lstrip()
    prefix_scope = _SCOPE_BY_PREFIX.get(stripped[:1])
    if prefix_scope is None:
        return ParsedQuery(scope="all", term=stripped.strip())
    return ParsedQuery(scope=prefix_scope, term=stripped[1:].strip())


def fuzzy_score(term: str, text: str) -> int | None:
    """Score ``term`` against ``text`` as an in-order subsequence; ``None`` if it does not fit.

    Matches that start a word or run on from the previous character score higher, long
    gaps score lower, and among equal hits a shorter text ranks first.
    """
    needle = term.casefold().replace(" ", "")
    if not needle:
        return 0
    haystack = text.casefold()
    score = 0
    position = -1
    previous = -2
    for char in needle:
        found = haystack.find(char, position + 1)
        if found < 0:
            return None
        bonus = 0
        if found == 0 or not haystack[found - 1].isalnum():
            bonus += 8  # the start of a word
        if found == previous + 1:
            bonus += 6  # continues the previous match
        score += 4 + bonus - min(found - position - 1, 6)
        previous = found
        position = found
    return score - len(haystack) // 25


def rank(
    entries: Iterable[PaletteEntry],
    query: str,
    *,
    recent: Sequence[str] = (),
    limit: int = 60,
) -> list[PaletteEntry]:
    """Entries matching ``query`` in display order, capped at ``limit``.

    With no term, recently used entries come first (most recent first) and the rest keep
    their given order.  With a term, better fuzzy scores come first; ties go to the more
    recently used entry, then to the given order.
    """
    parsed = parse_query(query)
    pool = [entry for entry in entries if _in_scope(entry, parsed.scope)]
    recency = {key: index for index, key in enumerate(recent)}
    unused = len(recent)
    if not parsed.term:
        order = sorted(range(len(pool)), key=lambda i: (recency.get(pool[i].key, unused), i))
        return [pool[i] for i in order][:limit]
    scored: list[tuple[int, int, int]] = []
    for index, entry in enumerate(pool):
        score = fuzzy_score(parsed.term, f"{entry.title} {entry.keywords}")
        if score is not None:
            scored.append((-score, recency.get(entry.key, unused), index))
    scored.sort()
    return [pool[index] for _, _, index in scored][:limit]


def remember(recent: Sequence[str], key: str, *, limit: int = 20) -> list[str]:
    """``recent`` with ``key`` moved to the front, de-duplicated and capped."""
    return [key, *(other for other in recent if other != key)][:limit]


def _in_scope(entry: PaletteEntry, scope: str) -> bool:
    if scope == "shortcuts":
        return bool(entry.shortcut)
    groups = _GROUPS_BY_SCOPE[scope]
    return groups is None or entry.group in groups
