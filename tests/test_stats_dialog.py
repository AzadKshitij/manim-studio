"""Offscreen tests for the App stats dialog and its MainWindow wiring."""

from __future__ import annotations

from typing import Any

from manim_studio.editor.main_window import MainWindow
from manim_studio.editor.stats_dialog import AppStatsDialog
from manim_studio.ipc import Command, Event

HOST_STATS: dict[str, Any] = {
    "event": Event.STATS,
    "trace_id": 3,
    "frames": 312,
    "bytes": 12 * 1024 * 1024,
    "max_bytes": 256 * 1024 * 1024,
    "max_frames": 7200,
    "host_rss": 412 * 1024 * 1024,
    "sent": 1234,
    "dropped": 12,
    "fps": 30,
    "blocks": 14,
    "total_duration": 9.5,
    "size": [1280, 720],
    "scene_name": "Demo",
}


class _StubController:
    def __init__(self) -> None:
        self.stats_calls = 0

    def stats(self) -> bool:
        self.stats_calls += 1
        return True


def _snapshot() -> dict[str, Any]:
    return {"editor_rss": 210 * 1024 * 1024, "api_names": 1234, "blocks": 14, "total_duration": 9.5}


class TestAppStatsDialog:
    def test_host_stats_render(self, qtbot) -> None:  # type: ignore[no-untyped-def]
        dialog = AppStatsDialog(_StubController(), _snapshot)
        qtbot.addWidget(dialog)
        dialog.set_host_stats(HOST_STATS)
        assert "312 frames" in dialog._cache_value.text()
        assert "12.0 MiB / 256.0 MiB" in dialog._cache_value.text()
        assert "412.0 MiB" in dialog._host_rss_value.text()
        assert "1234 sent" in dialog._pump_value.text()
        assert "Demo" in dialog._scene_value.text()

    def test_refresh_polls_and_paints_editor(self, qtbot) -> None:  # type: ignore[no-untyped-def]
        controller = _StubController()
        dialog = AppStatsDialog(controller, _snapshot)
        qtbot.addWidget(dialog)
        dialog.refresh()
        assert controller.stats_calls == 1
        assert "210.0 MiB" in dialog._editor_rss_value.text()
        assert "1234" in dialog._api_value.text()
        assert "14 blocks" in dialog._timeline_value.text()

    def test_empty_host_keeps_placeholders(self, qtbot) -> None:  # type: ignore[no-untyped-def]
        dialog = AppStatsDialog(_StubController(), _snapshot)
        qtbot.addWidget(dialog)
        dialog.refresh()
        assert dialog._cache_value.text() == "—"
        assert "210.0 MiB" in dialog._editor_rss_value.text()


class TestMainWindowStatsWiring:
    def test_filmstrip_with_bytes_updates_panel_and_label(self, qtbot) -> None:  # type: ignore[no-untyped-def]
        main = MainWindow(restore_settings=False, auto_preview=False, completion=False)
        qtbot.addWidget(main)
        main.stats.on_filmstrip(
            {**HOST_STATS, "event": Event.FILMSTRIP, "done": False, "capped": False}
        )
        assert "312 frames" in main.preview_panel.cache_status
        assert "12.0 MiB" in main.preview_panel.cache_status
        assert "cache" in main._mem_label.text()
        assert "12.0 MiB" in main._mem_label.text()

    def test_legacy_filmstrip_without_bytes_still_works(self, qtbot) -> None:  # type: ignore[no-untyped-def]
        main = MainWindow(restore_settings=False, auto_preview=False, completion=False)
        qtbot.addWidget(main)
        main.stats.on_filmstrip({"frames": 5, "done": True, "capped": False})
        assert "5 frames" in main.preview_panel.cache_status
        assert "5 frames" in main._mem_label.text()

    def test_stats_event_updates_dialog_when_open(self, qtbot) -> None:  # type: ignore[no-untyped-def]
        main = MainWindow(restore_settings=False, auto_preview=False, completion=False)
        qtbot.addWidget(main)
        main.stats.show_dialog()
        dialog = main.stats.dialog
        assert dialog is not None
        main.stats.on_stats(HOST_STATS)
        assert "312 frames" in dialog._cache_value.text()
        dialog.close()

    def test_controller_stats_dispatch(self, qtbot) -> None:  # type: ignore[no-untyped-def]
        from manim_studio.editor.preview_controller import PreviewController

        controller = PreviewController()
        with qtbot.waitSignal(controller.statsReceived, timeout=2000) as blocker:
            controller._dispatch(dict(HOST_STATS))
        assert blocker.args[0]["frames"] == 312

    def test_stats_command_shape(self) -> None:
        assert Command.STATS == "stats"
