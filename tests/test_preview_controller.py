"""Integration tests for the Qt-side preview controller (real host process)."""

from __future__ import annotations

from pathlib import Path

import pytest

from manim_studio.editor.preview_controller import PreviewController

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

    def test_set_timing_round_trip(self, qtbot) -> None:
        controller = PreviewController()
        try:
            controller.start(SCENE_FILE, embedded=True, size=(320, 180))
            with qtbot.waitSignal(controller.logReceived, timeout=90000) as log_blocker:
                pass
            blocks = log_blocker.args[0]["blocks"]
            target = blocks[0]["duration"] * 2.0
            with qtbot.waitSignal(controller.timingChanged, timeout=30000) as timing_blocker:
                assert controller.set_timing([{"index": 0, "duration": target}], 0.0) is True
            payload = timing_blocker.args[0]
            assert payload["blocks"][0]["duration"] == pytest.approx(target)
            assert payload["blocks"][1]["start"] == pytest.approx(blocks[0]["start"] + target)
        finally:
            controller.shutdown()

    def test_set_patches_round_trip(self, qtbot) -> None:
        controller = PreviewController()
        try:
            controller.start(SCENE_FILE, embedded=True, size=(320, 180))
            with qtbot.waitSignal(controller.logReceived, timeout=90000) as log_blocker:
                pass
            base = log_blocker.args[0]
            first_site = base["sites"][0]["id"]
            patch = {"op": "set_duration", "site": first_site, "seconds": 4.0}
            with qtbot.waitSignal(
                controller.logReceived,
                timeout=90000,
                check_params_cb=lambda message: message["patches"]["applied"] == 1,
            ) as blocker:
                assert controller.set_patches([patch], 0.0) is True
            patched = blocker.args[0]
            assert patched["blocks"][0]["duration"] == pytest.approx(4.0)
            assert patched["blocks"][1]["start"] == pytest.approx(4.0)
            # Every reload re-sends the remembered patches, so they survive it.
            with qtbot.waitSignal(controller.logReceived, timeout=90000) as again:
                assert controller.reload() is True
            assert again.args[0]["blocks"][0]["duration"] == pytest.approx(4.0)
        finally:
            controller.shutdown()

    def test_patches_are_remembered_even_without_a_host(self) -> None:
        controller = PreviewController()
        patch = {"op": "delete", "site": "S.construct:wait:abc:0"}
        assert controller.set_patches([patch], 1.5) is False
        assert controller.patches == [patch]

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

    def test_decode_latest_frame_keeps_only_newest(self) -> None:
        import os

        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        import gc

        from PySide6.QtWidgets import QApplication

        from manim_studio import ipc

        QApplication.instance() or QApplication([])
        controller = PreviewController()
        first = bytes([10, 10, 10, 255] * 4)  # 2x2
        second = bytes([200, 200, 200, 255] * 4)
        data = ipc.encode_frame(2, 2, first) + ipc.encode_frame(2, 2, second)
        image = controller.decode_latest_frame(data)
        assert image is not None
        # The image wraps the decompressed buffer instead of copying it; PySide6
        # keeps that buffer alive, so the pixels survive a collection.
        gc.collect()
        assert image.pixelColor(0, 0).red() == 200
        assert image.width() == 2 and image.height() == 2
        # A partial burst produces no image and keeps parser state.
        assert controller.decode_latest_frame(b"") is None


@pytest.mark.gl
def test_patches_set_before_start_apply_to_the_first_load(qtbot) -> None:
    """The host loads its first scene from argv, so start() must hand it the edits."""
    probe = PreviewController()
    try:
        probe.start(SCENE_FILE, embedded=True, size=(320, 180))
        with qtbot.waitSignal(probe.logReceived, timeout=90000) as first:
            pass
    finally:
        probe.shutdown()
    site = first.args[0]["sites"][0]["id"]
    controller = PreviewController()
    controller.patches = [{"op": "set_duration", "site": site, "seconds": 5.0}]
    try:
        controller.start(SCENE_FILE, embedded=True, size=(320, 180))
        with qtbot.waitSignal(controller.logReceived, timeout=90000) as blocker:
            pass
        message = blocker.args[0]
        assert message["patches"]["applied"] == 1
        assert message["blocks"][0]["duration"] == pytest.approx(5.0)
    finally:
        controller.shutdown()
