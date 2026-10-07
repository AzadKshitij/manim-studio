"""Tests for the stats controller: status text, overlay and dialog ownership."""

from __future__ import annotations

from typing import Any

from manim_studio.editor.stats_controller import StatsController

HOST_STATS: dict[str, Any] = {
    "frames": 312,
    "bytes": 12 * 1024 * 1024,
    "max_bytes": 256 * 1024 * 1024,
    "max_frames": 7200,
    "host_rss": 412 * 1024 * 1024,
}


class _StubPreviewController:
    def __init__(self) -> None:
        self.polls = 0

    def stats(self) -> bool:
        self.polls += 1
        return True


def _controller(**kwargs: Any) -> StatsController:
    return StatsController(_StubPreviewController(), lambda: {"editor_rss": 1}, **kwargs)


class TestStatusText:
    def test_placeholder_before_events(self) -> None:
        stats = _controller()
        assert stats.memory_text().startswith("cache — · host — · editor ")

    def test_stats_updates_memory_text(self) -> None:
        stats = _controller()
        labels: list[str] = []
        stats.memoryLabelChanged.connect(labels.append)
        stats.on_stats(dict(HOST_STATS))
        assert labels and "cache 12.0 MiB 5%" in labels[-1]
        assert "host 412.0 MiB" in labels[-1]
        assert stats.stats_cache["frames"] == 312

    def test_legacy_snapshot_without_bytes_uses_frames(self) -> None:
        stats = _controller()
        stats.on_stats({"frames": 5})
        assert "cache 5 frames" in stats.memory_text()
        assert "host —" in stats.memory_text()

    def test_timer_refreshes_label(self, qtbot) -> None:  # type: ignore[no-untyped-def]
        stats = _controller(refresh_ms=10)
        labels: list[str] = []
        stats.memoryLabelChanged.connect(labels.append)
        qtbot.waitUntil(lambda: bool(labels), timeout=3000)


class TestFilmstripOverlay:
    def test_filmstrip_emits_overlay_and_label(self) -> None:
        stats = _controller()
        overlays: list[str] = []
        stats.cacheStatusChanged.connect(overlays.append)
        stats.on_filmstrip({**HOST_STATS, "done": False, "capped": False})
        assert overlays == ["cache: 312 frames · 12.0 MiB…"]

    def test_legacy_filmstrip_without_bytes_still_works(self) -> None:
        stats = _controller()
        overlays: list[str] = []
        stats.cacheStatusChanged.connect(overlays.append)
        stats.on_filmstrip({"frames": 5, "done": True, "capped": True})
        assert overlays == ["cache: 5 frames (capped)"]


class TestDialog:
    def test_dialog_created_lazily_and_updated(self, qtbot) -> None:  # type: ignore[no-untyped-def]
        stats = _controller()
        assert stats.dialog is None
        stats.on_stats(dict(HOST_STATS))  # no dialog yet: must not raise
        stats.show_dialog()
        dialog = stats.dialog
        assert dialog is not None
        qtbot.addWidget(dialog)
        assert "312 frames" in dialog._cache_value.text()
        stats.on_stats({**HOST_STATS, "frames": 10})
        assert "10 frames" in dialog._cache_value.text()
        dialog.close()
