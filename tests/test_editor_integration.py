"""Full-stack integration: the real editor window driving the real preview host."""

from __future__ import annotations

from pathlib import Path

import pytest

from manim_editor.editor.main_window import MainWindow

SCENE_FILE = Path(__file__).parent / "scenes" / "feature_showcase.py"


@pytest.mark.gl
class TestEditorLiveHost:
    """Editor window + QProcess host + IPC + QImage frames, end to end."""

    def test_full_loop(self, qtbot) -> None:
        window = MainWindow(
            restore_settings=False,
            embedded=True,
            auto_preview=True,
            preview_size=(320, 180),
        )
        qtbot.addWidget(window)
        window.resize(1000, 700)
        window.show()

        logs: list[dict] = []
        frames: list[object] = []
        window.controller.logReceived.connect(logs.append)
        window.controller.frameReceived.connect(frames.append)

        try:
            assert window.open_path(SCENE_FILE) is True

            # 1. Trace arrives and populates the timeline.
            qtbot.waitUntil(
                lambda: bool(logs) and window.timeline.log.num_blocks >= 12, timeout=90000
            )
            assert window.timeline.log.total_duration > 1.0

            # 2. Live frames arrive and reach the preview panel.
            qtbot.waitUntil(lambda: len(frames) > 0, timeout=60000)
            assert window.preview_panel.current_image is not None
            assert window.preview_panel.current_image.width() == 320

            # 3. Timeline seek drives the host and updates the playhead.
            window.seek_to_block(2)
            block = window.timeline.log.block_by_index(2)
            qtbot.waitUntil(
                lambda: window.timeline.playhead_time == pytest.approx(block.end, abs=0.1),
                timeout=60000,
            )

            # 4. Live shell evaluates in the scene namespace.
            with qtbot.waitSignal(window.controller.execResultReceived, timeout=30000):
                window.run_shell_code("print('editor shell ok')")
            assert "editor shell ok" in window.shell_output.toPlainText()

            # 5. Play then stop through the toolbar paths.
            window.request_play()
            qtbot.waitUntil(lambda: window.timeline._playing, timeout=15000)
            with qtbot.waitSignal(window.controller.stoppedReceived, timeout=30000) as stopped:
                window.request_stop()
            assert stopped.args[0]["user_stopped"] is True
        finally:
            window.controller.shutdown()
