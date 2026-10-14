"""Recent-files list: pure ordering/pruning rules (no Qt, no settings IO)."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from pathlib import Path

__all__ = ["MAX_RECENT", "prune_recent", "push_recent"]

MAX_RECENT = 8


def _key(path: str) -> str:
    return str(Path(path).resolve()).casefold()


def push_recent(existing: Iterable[str], path: str | Path, *, limit: int = MAX_RECENT) -> list[str]:
    """Return ``existing`` with ``path`` moved to the front, de-duplicated and capped."""
    head = str(Path(path).resolve())
    seen = {_key(head)}
    result = [head]
    for entry in existing:
        key = _key(entry)
        if key in seen:
            continue
        seen.add(key)
        result.append(entry)
    return result[:limit]


def prune_recent(
    entries: Iterable[str], exists: Callable[[str], bool] = lambda p: Path(p).is_file()
) -> list[str]:
    """Drop entries whose file is gone, keeping order."""
    return [entry for entry in entries if exists(entry)]
