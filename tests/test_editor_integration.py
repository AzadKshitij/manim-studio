"""Full-stack integration: the real editor window driving the real preview host."""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from manim_editor.editor.code_editor import PY_KEYWORD
from manim_editor.editor.main_window import MainWindow

SCENE_FILE = Path(__file__).parent / "scenes" / "feature_showcase.py"


@pytest.mark.gl
class TestEditorLiveHost:
    """Editor window + QProcess host + IPC + QImage frames, end to end."""

    def test_full_loop(self, qtbot, tmp_path: Path) -> None:
        import shutil

        # Work on a copy so the watcher-driven reload can be exercised safely.
        scene_copy = tmp_path / "feature_showcase.py"
        shutil.copy(SCENE_FILE, scene_copy)

        window = MainWindow(
            restore_settings=False,
            embedded=True,
            auto_preview=True,
            preview_size=(320, 180),
            completion=False,
        )
        qtbot.addWidget(window)
        window.resize(1000, 700)
        window.show()

        logs: list[dict] = []
        frames: list[object] = []
        errors: list[dict] = []
        window.controller.logReceived.connect(logs.append)
        window.controller.frameReceived.connect(frames.append)
        window.controller.errorReceived.connect(errors.append)

        try:
            assert window.open_path(scene_copy) is True

            # 1. Trace arrives exactly once (no redundant load at startup).
            qtbot.waitUntil(
                lambda: bool(logs) and window.timeline.log.num_blocks >= 12, timeout=90000
            )
            assert len(logs) == 1

            # 1b. The loaded scene is syntax-highlighted through the Pygments path.
            offset = len(window.editor.text()[: window.editor.text().index("import")].encode())
            assert window.editor.styleAt(offset) == PY_KEYWORD

            # 2. Live frames arrive and reach the preview panel.
            qtbot.waitUntil(lambda: len(frames) > 0, timeout=60000)
            assert window.preview_panel.current_image is not None
            assert window.preview_panel.current_image.width() == 320

            # 3. Continuous scrub poses the scene at an exact time and streams a frame.
            scrub_ticks: list[tuple[float, int]] = []
            window.controller.tickReceived.connect(
                lambda seconds, index, scrubbing: scrub_ticks.append((seconds, index))
            )
            before_image = window.preview_panel.current_image
            frames_before = len(frames)
            window.scrub_to_time(0.15)
            qtbot.waitUntil(
                lambda: any(abs(seconds - 0.15) < 1e-6 for seconds, _ in scrub_ticks),
                timeout=60000,
            )
            qtbot.waitUntil(lambda: len(frames) > frames_before, timeout=30000)
            scrubbed_image = window.preview_panel.current_image
            assert scrubbed_image is not None and before_image is not None
            # Mid-animation content must differ from the final frame shown on load.
            assert bytes(scrubbed_image.bits()) != bytes(before_image.bits())
            assert not errors, f"scrub produced errors: {errors}"

            # 4. The filmstrip fills in the background and reports progress.
            filmstrip: list[dict] = []
            window.controller.filmstripReceived.connect(filmstrip.append)
            qtbot.waitUntil(lambda: any(m.get("done") for m in filmstrip), timeout=120000)
            assert window.preview_panel.cache_status.startswith("cache: ")
            assert int(filmstrip[-1]["frames"]) >= 50

            # 4. An external file change triggers a watcher-driven reload with no errors.
            logs.clear()
            scene_copy.write_text(
                scene_copy.read_text(encoding="utf-8") + "\n# touched externally\n",
                encoding="utf-8",
            )
            deadline = time.time() + 90
            while not logs and time.time() < deadline:
                qtbot.wait(250)
            assert logs, (
                f"no reload log after file change; errors={errors}; "
                f"modified={window.editor.is_modified()}; watched={window.watcher.path}; "
                f"running={window.controller.is_running}; "
                f"connected={window.controller.is_connected}"
            )
            assert not errors, f"reload produced errors: {errors}"

            # 4. Timeline seek drives the host and updates the playhead.
            window.seek_to_block(2)
            block = window.timeline.log.block_by_index(2)
            qtbot.waitUntil(
                lambda: window.timeline.playhead_time == pytest.approx(block.end, abs=0.1),
                timeout=60000,
            )

            # 5. Live shell evaluates in the scene namespace.
            with qtbot.waitSignal(window.controller.execResultReceived, timeout=30000):
                window.shell.submit_text("print('editor shell ok')")
            assert "editor shell ok" in window.shell.output.toPlainText()

            # 6. Play then stop through the toolbar paths.
            window.request_play()
            qtbot.waitUntil(lambda: window.timeline._playing, timeout=15000)
            with qtbot.waitSignal(window.controller.stoppedReceived, timeout=30000) as stopped:
                window.request_stop()
            assert stopped.args[0]["user_stopped"] is True
        finally:
            window.controller.shutdown()
