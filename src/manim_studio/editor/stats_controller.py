"""Filmstrip-cache and memory readouts for the status bar and App stats dialog.

The preview host reports cache numbers through ``filmstripReceived`` (crawl
progress) and ``statsReceived`` (polls); this controller keeps the latest
snapshot, formats the compact status-bar text, and owns the lazily-created
App stats dialog.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from PySide6.QtCore import QObject, QTimer, Signal
from PySide6.QtWidgets import QWidget

from manim_studio.editor.stats_dialog import AppStatsDialog
from manim_studio.stats import format_bytes, process_rss_bytes, usage_percent

if TYPE_CHECKING:
    from collections.abc import Callable

    from manim_studio.editor.preview_controller import PreviewController

__all__ = ["StatsController"]

log = logging.getLogger(__name__)


class StatsController(QObject):
    """Turns host cache/RAM snapshots into status text and dialog updates."""

    memoryLabelChanged = Signal(str)  # status-bar cache/host/editor readout
    cacheStatusChanged = Signal(str)  # preview-panel filmstrip overlay

    def __init__(
        self,
        controller: PreviewController,
        editor_snapshot: Callable[[], dict[str, Any]],
        *,
        dialog_parent: QWidget | None = None,
        refresh_ms: int = 2000,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._controller = controller
        self._editor_snapshot = editor_snapshot
        self._dialog_parent = dialog_parent
        self._stats_cache: dict[str, Any] = {}
        self._dialog: AppStatsDialog | None = None
        self._timer = QTimer(self)
        self._timer.setInterval(refresh_ms)
        self._timer.timeout.connect(self.refresh_memory_label)
        self._timer.start()

    @property
    def stats_cache(self) -> dict[str, Any]:
        """The most recent host snapshot (empty until the first event)."""
        return dict(self._stats_cache)

    @property
    def dialog(self) -> AppStatsDialog | None:
        """The App stats dialog, once it has been opened."""
        return self._dialog

    def on_filmstrip(self, message: dict[str, Any]) -> None:
        self._stats_cache = dict(message)
        frames = int(message.get("frames", 0))
        done = bool(message.get("done", False))
        capped = bool(message.get("capped", False))
        byte_size = message.get("bytes")
        if isinstance(byte_size, (int, float)) and byte_size > 0:
            cache_text = f"cache: {frames} frames · {format_bytes(byte_size)}"
        else:
            cache_text = f"cache: {frames} frames"
        suffix = (" (capped)" if capped else "") if done else "…"
        self.cacheStatusChanged.emit(cache_text + suffix)
        self.refresh_memory_label()

    def on_stats(self, message: dict[str, Any]) -> None:
        """Host answered a STATS poll: cache numbers double as filmstrip state."""
        self._stats_cache = dict(message)
        self.refresh_memory_label()
        if self._dialog is not None:
            try:
                self._dialog.set_host_stats(message)
            except Exception:  # never let the dialog break the control channel
                log.debug("stats dialog update failed", exc_info=True)

    def refresh_memory_label(self) -> None:
        self.memoryLabelChanged.emit(self.memory_text())

    def memory_text(self) -> str:
        """Compact readout: cache MiB + host/editor RSS."""
        cache = self._stats_cache
        byte_size = cache.get("bytes")
        max_bytes = cache.get("max_bytes")
        frames = cache.get("frames")
        host_rss = cache.get("host_rss")
        if (
            isinstance(byte_size, (int, float))
            and isinstance(max_bytes, (int, float))
            and max_bytes > 0
        ):
            percent = usage_percent(byte_size, max_bytes)
            percent_text = f" {percent:.0f}%" if percent is not None else ""
            cache_text = f"cache {format_bytes(byte_size)}{percent_text}"
        elif isinstance(frames, int):
            cache_text = f"cache {frames} frames"
        else:
            cache_text = "cache —"
        host_text = f"host {format_bytes(host_rss)}" if host_rss is not None else "host —"
        editor_text = f"editor {format_bytes(process_rss_bytes())}"
        return f"{cache_text} · {host_text} · {editor_text}"

    def show_dialog(self) -> None:
        if self._dialog is None:
            self._dialog = AppStatsDialog(
                self._controller, self._editor_snapshot, self._dialog_parent
            )
        if self._stats_cache:
            self._dialog.set_host_stats(self._stats_cache)
        self._dialog.show()
        self._dialog.raise_()
        self._dialog.activateWindow()
