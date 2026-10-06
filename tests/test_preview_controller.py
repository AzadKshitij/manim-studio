"""Integration tests for the Qt-side preview controller (real host process)."""

from __future__ import annotations

from pathlib import Path

import pytest

from manim_editor.editor.preview_controller import PreviewController

SCENE_FILE = Path(__file__).parent / "scenes" / "feature_showcase.py"


@pytest.mark.gl
class TestPreviewControllerIntegration:
    def test_trace_frames_and_exec(self, qtbot) -> None:
        controller = PreviewController()
        frames: list[object] = []
        controller.frameReceived.connect(frames.append)
        try:
            controller.start(SCENE_FILE, embedded=True, size=(320, 180))
            with qtbot.waitSignal(controller.logReceived, timeout=90000) as log_blocker:
                pass
            message = log_blocker.args[0]
            assert len(message["blocks"]) >= 12
            assert message["stats"]["play"] >= 10

            qtbot.waitUntil(lambda: len(frames) > 0, timeout=60000)
            assert frames[0].width() == 320 and frames[0].height() == 180

            assert controller.is_connected
            with qtbot.waitSignal(controller.execResultReceived, timeout=30000) as exec_blocker:
                assert controller.exec_code("print('controller ok')") is True
            assert "controller ok" in exec_blocker.args[0]["stdout"]

            with qtbot.waitSignal(controller.tickReceived, timeout=30000):
                assert controller.seek(2) is True
        finally:
            controller.shutdown()

    def test_crash_reports_state(self, qtbot, tmp_path: Path) -> None:
        controller = PreviewController()
        try:
            controller.start(tmp_path / "missing_scene.py", embedded=True, size=(160, 90))
            with qtbot.waitSignal(controller.errorReceived, timeout=90000) as blocker:
                pass
            assert (
                "missing_scene" in blocker.args[0]["message"]
                or "cannot import" in blocker.args[0]["message"]
            )
        finally:
            controller.shutdown()

    def test_send_without_host(self) -> None:
        controller = PreviewController()
        assert controller.send("ping") is False
        assert controller.is_running is False
