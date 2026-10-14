"""Autosave snapshots: where recovery copies live and when they are worth offering.

Pure (no Qt).  A snapshot is a hidden sibling of the scene file, ``.<name>.autosave``,
kept while the buffer holds edits that are not on disk.  It is removed on save, on
discard, and once the buffer matches the file again; a crash leaves it behind for the
next open to offer.
"""

from __future__ import annotations

from pathlib import Path

__all__ = ["SUFFIX", "autosave_path", "should_keep_snapshot", "should_offer_recovery"]

SUFFIX = ".autosave"


def autosave_path(scene: str | Path) -> Path:
    """The snapshot beside ``scene``: ``demo.py`` becomes ``.demo.py.autosave``."""
    path = Path(scene)
    return path.with_name(f".{path.name}{SUFFIX}")


def should_keep_snapshot(buffer_text: str, disk_text: str | None, modified: bool) -> bool:
    """A snapshot is worth keeping only while the buffer holds edits not on disk."""
    return modified and buffer_text != disk_text


def should_offer_recovery(
    snapshot_text: str | None,
    snapshot_mtime: float | None,
    disk_text: str | None,
    disk_mtime: float | None,
) -> bool:
    """Offer recovery when a snapshot is newer than the scene and holds other code."""
    if snapshot_text is None or snapshot_mtime is None:
        return False
    if disk_mtime is not None and snapshot_mtime <= disk_mtime:
        return False
    return snapshot_text != disk_text
