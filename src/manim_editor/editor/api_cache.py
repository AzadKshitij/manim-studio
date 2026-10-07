"""Editor-side caching for the manimlib API index.

The index itself (AST scan, JSON serialization) lives in
:mod:`manim_editor.api_index`; this module owns the editor-specific cache
location and the load-or-rebuild policy used at window construction.
"""

from __future__ import annotations

import logging
from pathlib import Path

from PySide6.QtCore import QStandardPaths

from manim_editor.api_index import build_index, index_names, load_index, save_index

__all__ = ["api_cache_path", "load_api_names"]

log = logging.getLogger(__name__)


def api_cache_path() -> Path:
    """The persisted API index location (home-dir fallback for odd platforms)."""
    try:
        location = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.CacheLocation)
        return Path(location) / "api_index.json"
    except Exception:  # pragma: no cover - platform fallback
        return Path.home() / ".manim-editor" / "api_index.json"


def load_api_names() -> tuple[str, ...]:
    """Completion names for the manimlib API, using the disk cache when valid.

    On a cache miss the index is rebuilt by scanning the installed manimlib and
    persisted for the next launch.
    """
    cache = api_cache_path()
    symbols = load_index(cache)
    if not symbols:
        symbols = build_index()
        if symbols:
            try:
                save_index(cache, symbols)
            except OSError:
                log.debug("could not persist API index to %s", cache)
    return index_names(symbols)
