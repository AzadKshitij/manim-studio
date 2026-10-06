"""Offscreen smoke tests for the editor main window."""

from __future__ import annotations

from pathlib import Path

import pytest
from PySide6.QtGui import QImage

from manim_editor.editor.main_window import MainWindow

SCENE_FILE = Path(__file__).parent / "scenes" / "feature_showcase.py"


class _StubController:
    """Records `load` commands so tests can assert what the window sends."""

    def __init__(self) -> None:
        self.loads: list[tuple[str, str | None]] = []
        self.is_running = True

    def load(self, path, scene_name: str | None = None) -> bool:
        self.loads.append((str(path), scene_name))
        return True

    def shutdown(self) -> None:
        pass


SAMPLE_LOG_EVENT = {
    "event": "log",
    "blocks": [
        {"name": "ShowCreation", "start": 0.0, "duration": 1.0, "index": 0},
        {"name": "Wait", "start": 1.0, "duration": 0.5, "index": 1},
        {"name": "Transform", "start": 1.5, "duration": 2.0, "index": 2},
    ],
    "stats": {"play": 2, "wait": 1},
    "total_duration": 3.5,
}


@pytest.fixture
def window(qtbot) -> MainWindow:
    main = MainWindow(restore_settings=False, embedded=True, auto_preview=False)
    qtbot.addWidget(main)
    main.resize(1200, 800)
    main.show()
    return main


class TestConstruction:
    def test_widgets_exist(self, window: MainWindow) -> None:
        assert window.editor is not None
        assert window.timeline is not None
        assert window.preview_panel is not None
        assert window.shell_dock is not None

    def test_toolbar_has_scene_combo(self, window: MainWindow) -> None:
        assert window._scene_combo.count() == 0

    def test_completions_loaded(self, window: MainWindow) -> None:
        assert len(window._api_names) > 100


class TestOpenAndSave:
    def test_open_scene(self, window: MainWindow) -> None:
        assert window.open_path(SCENE_FILE) is True
        assert window.current_path == SCENE_FILE.resolve()
        assert window._scene_combo.currentText() == "FeatureShowcase"
        assert "FeatureShowcase" in window.editor.text()
        assert window.watcher.path == SCENE_FILE.resolve()

    def test_open_does_not_send_redundant_load(self, window: MainWindow, tmp_path: Path) -> None:
        """Populating the scene combo must not look like a user scene switch."""
        target = tmp_path / "two.py"
        target.write_text(
            "from manimlib import Scene\n\n\n"
            "class Alpha(Scene):\n    pass\n\n\n"
            "class Beta(Scene):\n    pass\n",
            encoding="utf-8",
        )
        original = window.controller
        stub = _StubController()
        window.controller = stub  # type: ignore[assignment]
        try:
            assert window.open_path(target) is True
            assert stub.loads == []
            window._scene_combo.setCurrentIndex(1)  # a real user switch does send load
            assert stub.loads and stub.loads[-1][1] == "Beta"
        finally:
            window.controller = original  # type: ignore[assignment]

    def test_reload_sends_explicit_scene_name(self, window: MainWindow, tmp_path: Path) -> None:
        target = tmp_path / "two.py"
        target.write_text(
            "from manimlib import Scene\n\n\n"
            "class Alpha(Scene):\n    pass\n\n\n"
            "class Beta(Scene):\n    pass\n",
            encoding="utf-8",
        )
        original = window.controller
        stub = _StubController()
        window.controller = stub  # type: ignore[assignment]
        try:
            window.open_path(target)
            window.reload_preview()
            assert stub.loads[-1] == (str(target.resolve()), "Alpha")
        finally:
            window.controller = original  # type: ignore[assignment]

    def test_scene_rename_falls_back_to_first(self, window: MainWindow, tmp_path: Path) -> None:
        target = tmp_path / "rename.py"
        target.write_text(
            "from manimlib import Scene\n\n\n"
            "class Alpha(Scene):\n    pass\n\n\n"
            "class Beta(Scene):\n    pass\n",
            encoding="utf-8",
        )
        original = window.controller
        stub = _StubController()
        window.controller = stub  # type: ignore[assignment]
        try:
            window.open_path(target)
            assert window._scene_combo.currentText() == "Alpha"
            target.write_text(
                "from manimlib import Scene\n\n\n"
                "class Gamma(Scene):\n    pass\n\n\n"
                "class Beta(Scene):\n    pass\n",
                encoding="utf-8",
            )
            window._refresh_scene_list(target)
            assert window._scene_combo.currentText() == "Gamma"
            window.reload_preview()
            assert stub.loads[-1][1] == "Gamma"
        finally:
            window.controller = original  # type: ignore[assignment]

    def test_open_broken_file_rejected(self, window: MainWindow, tmp_path: Path) -> None:
        broken = tmp_path / "broken.py"
        broken.write_text("def f(:\n", encoding="utf-8")
        assert window.open_path(broken) is False
        assert window.current_path is None

    def test_save_buffer(self, window: MainWindow, tmp_path: Path) -> None:
        target = tmp_path / "scene.py"
        target.write_text(
            "from manimlib import Scene\n\n\nclass Demo(Scene):\n    pass\n", encoding="utf-8"
        )
        assert window.open_path(target) is True
        window.editor.set_text("from manimlib import Scene\n\n\nclass Demo(Scene):\n    x = 2\n")
        assert window.editor.is_modified()
        window.watcher.stop()  # avoid fs events from the write in this test
        window.save_buffer()
        assert "x = 2" in target.read_text(encoding="utf-8")
        assert not window.editor.is_modified()

    def test_create_scene_from_template(self, window: MainWindow, tmp_path: Path) -> None:
        target = window.create_scene_file(tmp_path / "my_intro.py", "circle_to_square")
        assert target is not None and target.is_file()
        text = target.read_text(encoding="utf-8")
        assert "class MyIntro(Scene):" in text
        assert window.open_path(target) is True
        assert window._scene_combo.currentText() == "MyIntro"


class TestTraceAndTimeline:
    def test_log_event_populates_timeline(self, window: MainWindow) -> None:
        window._on_log(SAMPLE_LOG_EVENT)
        assert window.timeline.log.num_blocks == 3
        assert window.timeline.log.total_duration == pytest.approx(3.5)
        assert window._total_duration == pytest.approx(3.5)

    def test_seek_updates_playhead(self, window: MainWindow) -> None:
        window._on_log(SAMPLE_LOG_EVENT)
        window.seek_to_block(2)
        assert window.timeline.playhead_time == pytest.approx(3.5)

    def test_scrub_snaps_to_block(self, window: MainWindow) -> None:
        window._on_log(SAMPLE_LOG_EVENT)
        window.scrub_to_time(0.3)
        assert window.timeline.playhead_time == pytest.approx(1.0)  # end of block 0

    def test_tick_moves_playhead(self, window: MainWindow) -> None:
        window._on_log(SAMPLE_LOG_EVENT)
        window._on_tick(1.25, 1)
        assert window.timeline.playhead_time == pytest.approx(1.25)

    def test_malformed_log_is_ignored(self, window: MainWindow) -> None:
        window._on_log({"blocks": [{"nope": True}]})
        assert window.timeline.log.is_empty

    def test_error_marks_editor(self, window: MainWindow) -> None:
        window.editor.set_text("line1\nline2\n")
        window._on_error({"message": "ValueError: boom", "line": 2, "traceback": "trace"})
        assert window.editor.error_line == 2
        assert "boom" in window.shell_output.toPlainText()


class TestFramesAndPreview:
    def test_frame_updates_panel(self, window: MainWindow) -> None:
        image = QImage(32, 24, QImage.Format.Format_RGBA8888)
        image.fill(0xFF0000FF)
        window._on_frame(image)
        assert window.preview_panel.current_image is not None
        assert window.preview_panel.current_image.size().width() == 32

    def test_video_mode_ignores_live_frames(self, window: MainWindow) -> None:
        window._enter_video_mode()
        image = QImage(16, 16, QImage.Format.Format_RGBA8888)
        window._on_frame(image)
        assert window.preview_panel.current_image is None

    def test_preview_video_path(self, window: MainWindow) -> None:
        assert window.preview_video_path() is None
        window.open_path(SCENE_FILE)
        path = window.preview_video_path()
        assert path is not None
        assert path.name == "feature_showcase_preview.mp4"
        assert path.parent.name == ".manim-editor"

    def test_embedded_toggle(self, window: MainWindow) -> None:
        window.set_embedded(False)
        assert window.preview_panel.is_embedded is False
        window.set_embedded(True)
        assert window.preview_panel.is_embedded is True


class TestEditing:
    def test_insert_template(self, window: MainWindow) -> None:
        window.editor.set_text("class A(Scene):\n    def construct(self):\n        ")
        window.editor.gotoLine(2)
        window.insert_template("wait")
        assert "self.wait(1.0)" in window.editor.text()

    def test_shell_without_host(self, window: MainWindow) -> None:
        window.run_shell_code("print('hi')")
        assert "(preview host is not running)" in window.shell_output.toPlainText()

    def test_host_log_appends(self, window: MainWindow) -> None:
        window._append_shell_output("hello from host")
        assert "hello from host" in window.shell_output.toPlainText()

    def test_host_state_label(self, window: MainWindow) -> None:
        window._on_host_state("crashed")
        assert "crashed" in window._host_state_label.text()
