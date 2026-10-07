"""Offscreen smoke tests for the editor main window."""

from __future__ import annotations

from pathlib import Path

import pytest
from PySide6.QtGui import QImage

from manim_editor.editor import main_window as main_window_module
from manim_editor.editor.code_editor import PY_KEYWORD
from manim_editor.editor.main_window import MainWindow

SCENE_FILE = Path(__file__).parent / "scenes" / "feature_showcase.py"


def _byte_offset(text: str, needle: str) -> int:
    """Scintilla positions are UTF-8 byte offsets, not character offsets."""
    return len(text[: text.index(needle)].encode("utf-8"))


class _StubController:
    """Records commands so tests can assert what the window sends."""

    def __init__(self) -> None:
        self.loads: list[tuple[str, str | None]] = []
        self.reloads: list[str | None] = []
        self.scrubs: list[float] = []
        self.seeks: list[int] = []
        self.plays: list[int] = []
        self.stops = 0
        self.is_running = True

    def load(self, path, scene_name: str | None = None) -> bool:
        self.loads.append((str(path), scene_name))
        return True

    def reload(self, path=None) -> bool:
        self.reloads.append(str(path) if path is not None else None)
        return True

    def scrub(self, time_seconds: float) -> bool:
        self.scrubs.append(float(time_seconds))
        return True

    def seek(self, index: int) -> bool:
        self.seeks.append(int(index))
        return True

    def play(self, from_index: int = 0) -> bool:
        self.plays.append(int(from_index))
        return True

    def stop_playback(self) -> bool:
        self.stops += 1
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
    main = MainWindow(restore_settings=False, embedded=True, auto_preview=False, completion=False)
    qtbot.addWidget(main)
    main.resize(1200, 800)
    main.show()
    return main


class TestConstruction:
    def test_widgets_exist(self, window: MainWindow) -> None:
        assert window.editor is not None
        assert window.timeline is not None
        assert window.preview_panel is not None
        assert window.shell is not None

    def test_window_has_icon(self, window: MainWindow) -> None:
        assert not window.windowIcon().isNull()

    def test_toolbar_has_scene_combo(self, window: MainWindow) -> None:
        assert window._scene_combo.count() == 0

    def test_completions_loaded(self, window: MainWindow) -> None:
        assert len(window._api_names) > 100

    def test_completion_disabled_by_flag(self, window: MainWindow) -> None:
        assert window.completions.worker is None


class TestCompletionWiring:
    def test_worker_created_when_enabled(self, qtbot) -> None:
        main = MainWindow(restore_settings=False, embedded=True, auto_preview=False)
        qtbot.addWidget(main)
        worker = main.completions.worker
        assert worker is not None
        assert worker.isRunning() is False
        worker.stop()

    def test_request_round_trips_through_jedi(self, qtbot) -> None:
        main = MainWindow(restore_settings=False, embedded=True, auto_preview=False)
        qtbot.addWidget(main)
        worker = main.completions.worker
        assert worker is not None
        source = "values = [1, 2, 3]\nvalues.ap"
        main.editor.set_text(source)
        offset = len(source)
        try:
            with qtbot.waitSignal(worker.completionsReady, timeout=15000) as blocker:
                main._on_completions_requested(source, offset)
            got_offset, items = blocker.args
            assert got_offset == offset
            assert any(item.insert == "append" for item in items)
        finally:
            main.completions.stop()


class TestOpenAndSave:
    def test_open_scene(self, window: MainWindow) -> None:
        assert window.open_path(SCENE_FILE) is True
        assert window.current_path == SCENE_FILE.resolve()
        assert window._scene_combo.currentText() == "FeatureShowcase"
        assert "FeatureShowcase" in window.editor.text()
        assert window.watcher.path == SCENE_FILE.resolve()

    def test_open_scene_highlights_tokens(self, window: MainWindow) -> None:
        assert window.open_path(SCENE_FILE) is True
        text = window.editor.text()
        # The loaded buffer is lexed and styled through the Pygments path.
        assert window.editor.styleAt(_byte_offset(text, "import")) == PY_KEYWORD
        assert window.editor.styleAt(_byte_offset(text, "class")) == PY_KEYWORD

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

    def test_save_without_changes_does_not_reload(self, window: MainWindow, tmp_path: Path) -> None:
        target = tmp_path / "scene.py"
        target.write_text(
            "from manimlib import Scene\n\n\nclass Demo(Scene):\n    pass\n", encoding="utf-8"
        )
        original = window.controller
        stub = _StubController()
        window.controller = stub  # type: ignore[assignment]
        try:
            assert window.open_path(target) is True
            window.watcher.stop()
            window.save_buffer()
            assert stub.reloads == []
        finally:
            window.controller = original  # type: ignore[assignment]

    def test_save_with_changes_reloads_once(self, window: MainWindow, tmp_path: Path) -> None:
        target = tmp_path / "scene.py"
        target.write_text(
            "from manimlib import Scene\n\n\nclass Demo(Scene):\n    pass\n", encoding="utf-8"
        )
        original = window.controller
        stub = _StubController()
        window.controller = stub  # type: ignore[assignment]
        try:
            assert window.open_path(target) is True
            window.editor.set_text(
                "from manimlib import Scene\n\n\nclass Demo(Scene):\n    x = 2\n"
            )
            window.watcher.stop()
            window.save_buffer()
            assert stub.reloads == [None]
            assert not window.editor.is_modified()
        finally:
            window.controller = original  # type: ignore[assignment]

    def test_save_identical_text_skips_reload(self, window: MainWindow, tmp_path: Path) -> None:
        """Scintilla keeps the modified flag after retyping identical text, so the
        content comparison must still suppress the redundant re-render."""
        target = tmp_path / "scene.py"
        target.write_text(
            "from manimlib import Scene\n\n\nclass Demo(Scene):\n    pass\n", encoding="utf-8"
        )
        original = window.controller
        stub = _StubController()
        window.controller = stub  # type: ignore[assignment]
        try:
            assert window.open_path(target) is True
            window.editor.set_text(target.read_text(encoding="utf-8"))
            assert window.editor.is_modified()  # save point moved, content identical
            window.watcher.stop()
            window.save_buffer()
            assert stub.reloads == []
            assert not window.editor.is_modified()
        finally:
            window.controller = original  # type: ignore[assignment]

    def test_save_crlf_file_without_changes_does_not_reload(
        self, window: MainWindow, tmp_path: Path
    ) -> None:
        target = tmp_path / "scene.py"
        target.write_bytes(
            b"from manimlib import Scene\r\n\r\n\r\nclass Demo(Scene):\r\n    pass\r\n"
        )
        original = window.controller
        stub = _StubController()
        window.controller = stub  # type: ignore[assignment]
        try:
            assert window.open_path(target) is True
            window.watcher.stop()
            window.save_buffer()
            assert stub.reloads == []
        finally:
            window.controller = original  # type: ignore[assignment]

    def test_file_touched_without_change_does_not_reload(
        self, window: MainWindow, tmp_path: Path
    ) -> None:
        target = tmp_path / "scene.py"
        target.write_text(
            "from manimlib import Scene\n\n\nclass Demo(Scene):\n    pass\n", encoding="utf-8"
        )
        original = window.controller
        stub = _StubController()
        window.controller = stub  # type: ignore[assignment]
        try:
            assert window.open_path(target) is True
            window.watcher.stop()
            # Same bytes rewritten (a touch or a no-op formatter): the watcher fires
            # but nothing actually changed, so no reload and no re-render.
            target.write_text(target.read_text(encoding="utf-8"), encoding="utf-8")
            window._on_file_changed(str(target))
            assert stub.loads == []
            assert stub.reloads == []
        finally:
            window.controller = original  # type: ignore[assignment]

    def test_create_scene_from_template(self, window: MainWindow, tmp_path: Path) -> None:
        target = window.create_scene_file(tmp_path / "my_intro.py", "circle_to_square")
        assert target is not None and target.is_file()
        text = target.read_text(encoding="utf-8")
        assert "class MyIntro(Scene):" in text
        assert window.open_path(target) is True
        assert window._scene_combo.currentText() == "MyIntro"

    def test_create_scene_with_explicit_name(self, window: MainWindow, tmp_path: Path) -> None:
        target = window.create_scene_file(
            tmp_path / "my_intro.py", "circle_to_square", "CustomName"
        )
        assert target is not None
        assert "class CustomName(Scene):" in target.read_text(encoding="utf-8")

    def test_syntax_error_then_recovery(self, window: MainWindow, tmp_path: Path) -> None:
        """A broken save must not wedge the editor, and fixing it must recover."""
        target = tmp_path / "scene.py"
        target.write_text(
            "from manimlib import Scene\n\n\nclass Demo(Scene):\n    pass\n",
            encoding="utf-8",
        )
        assert window.open_path(target) is True
        assert window._scene_combo.currentText() == "Demo"

        target.write_text("def broken(:\n", encoding="utf-8")
        window._on_file_changed(str(target))  # must not raise
        assert window._scene_combo.count() == 0
        assert "def broken(:" in window.editor.text()

        target.write_text(
            "from manimlib import Scene\n\n\nclass Fixed(Scene):\n    pass\n",
            encoding="utf-8",
        )
        window._on_file_changed(str(target))
        assert window._scene_combo.currentText() == "Fixed"


def _drive_new_scene(
    monkeypatch: pytest.MonkeyPatch,
    save_path: Path,
    name_replies: list[tuple[str, bool]],
    template_key: str = "circle_to_square",
) -> tuple[list[str], list[str]]:
    """Run ``new_scene_dialog`` with scripted dialog replies.

    Returns the recorded warnings and the suggested save locations, in order.
    """
    warnings: list[str] = []
    suggestions: list[str] = []
    replies = list(name_replies)

    class _InputDialog:
        @staticmethod
        def getItem(*args: object, **kwargs: object) -> tuple[str, bool]:
            return f"{template_key} — scripted", True

        @staticmethod
        def getText(*args: object, **kwargs: object) -> tuple[str, bool]:
            return replies.pop(0)

    class _FileDialog:
        @staticmethod
        def getSaveFileName(*args: object, **kwargs: object) -> tuple[str, str]:
            suggestions.append(str(args[2]))
            return str(save_path), "Python files (*.py)"

    class _MessageBox:
        @staticmethod
        def warning(parent: object, title: str, text: str, *args: object) -> None:
            warnings.append(text)

    monkeypatch.setattr(main_window_module, "QInputDialog", _InputDialog)
    monkeypatch.setattr(main_window_module, "QFileDialog", _FileDialog)
    monkeypatch.setattr(main_window_module, "QMessageBox", _MessageBox)
    return warnings, suggestions


class TestNewSceneDialog:
    def test_uses_chosen_scene_name(
        self, window: MainWindow, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        target = tmp_path / "picked.py"
        warnings, suggestions = _drive_new_scene(monkeypatch, target, [("IntroAnimation", True)])
        window.new_scene_dialog()
        assert warnings == []
        assert target.is_file()
        assert "class IntroAnimation(Scene):" in target.read_text(encoding="utf-8")
        assert suggestions and suggestions[0].endswith("intro_animation.py")
        assert window.current_path == target.resolve()
        assert window._scene_combo.currentText() == "IntroAnimation"

    def test_invalid_names_warn_and_retry(
        self, window: MainWindow, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        target = tmp_path / "retry.py"
        replies = [("not a name", True), ("class", True), ("SecondTry", True)]
        warnings, _ = _drive_new_scene(monkeypatch, target, replies)
        window.new_scene_dialog()
        assert len(warnings) == 2
        assert "class SecondTry(Scene):" in target.read_text(encoding="utf-8")

    def test_cancel_at_name_prompt_creates_nothing(
        self, window: MainWindow, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        target = tmp_path / "cancelled.py"
        _drive_new_scene(monkeypatch, target, [("Whatever", False)])
        window.new_scene_dialog()
        assert not target.exists()
        assert window.current_path is None


class TestTraceAndTimeline:
    def test_log_event_populates_timeline(self, window: MainWindow) -> None:
        window.events.on_log(SAMPLE_LOG_EVENT)
        assert window.timeline.log.num_blocks == 3
        assert window.timeline.log.total_duration == pytest.approx(3.5)
        assert window.events.total_duration == pytest.approx(3.5)

    def test_seek_updates_playhead(self, window: MainWindow) -> None:
        window.events.on_log(SAMPLE_LOG_EVENT)
        window.seek_to_block(2)
        assert window.timeline.playhead_time == pytest.approx(3.5)

    def test_scrub_is_continuous(self, window: MainWindow) -> None:
        original = window.controller
        stub = _StubController()
        window.controller = stub  # type: ignore[assignment]
        try:
            window.open_path(SCENE_FILE)
            window.events.on_log(SAMPLE_LOG_EVENT)
            window.scrub_to_time(0.3)  # inside block 0, not snapped to a boundary
            assert stub.scrubs[-1] == pytest.approx(0.3)
            assert window.timeline.playhead_time == pytest.approx(0.3)
            assert window.events.play_from_index == 0
            window.scrub_to_time(99.0)  # clamped to total
            assert stub.scrubs[-1] == pytest.approx(3.5)
        finally:
            window.controller = original  # type: ignore[assignment]

    def test_successful_reload_clears_error_marker(
        self, window: MainWindow, tmp_path: Path
    ) -> None:
        target = tmp_path / "scene.py"
        target.write_text(
            "from manimlib import Scene\n\n\nclass Demo(Scene):\n    pass\n",
            encoding="utf-8",
        )
        assert window.open_path(target) is True
        window.editor.set_error_marker(2, "NameError: boom")
        assert window.editor.error_line == 2

        window.events.on_log(SAMPLE_LOG_EVENT)  # a trace succeeded
        assert window.editor.error_line is None

        # The host-restart path must clear the marker too.
        window.editor.set_error_marker(2, "NameError: boom")
        window.reload_preview()
        assert window.editor.error_line is None

    def test_play_toggle(self, window: MainWindow) -> None:
        original = window.controller
        stub = _StubController()
        window.controller = stub  # type: ignore[assignment]
        try:
            window.open_path(SCENE_FILE)
            window.events.on_log(SAMPLE_LOG_EVENT)
            window.events.on_status("paused", {})  # host reports not playing
            window._toggle_play()
            assert stub.plays == [0]
            window.events.on_status("playing", {})
            window._toggle_play()
            assert stub.stops == 1
        finally:
            window.controller = original  # type: ignore[assignment]

    def test_tick_moves_playhead(self, window: MainWindow) -> None:
        window.events.on_log(SAMPLE_LOG_EVENT)
        window.events.on_tick(1.25, 1, False)
        assert window.timeline.playhead_time == pytest.approx(1.25)

    def test_malformed_log_is_ignored(self, window: MainWindow) -> None:
        window.events.on_log({"blocks": [{"nope": True}]})
        assert window.timeline.log.is_empty

    def test_error_marks_editor(self, window: MainWindow) -> None:
        window.editor.set_text("line1\nline2\n")
        window.events.on_error({"message": "ValueError: boom", "line": 2, "traceback": "trace"})
        assert window.editor.error_line == 2
        assert "boom" in window.shell.output.toPlainText()


class TestFramesAndPreview:
    def test_frame_updates_panel(self, window: MainWindow) -> None:
        image = QImage(32, 24, QImage.Format.Format_RGBA8888)
        image.fill(0xFF0000FF)
        window.events.on_frame(image)
        assert window.preview_panel.current_image is not None
        assert window.preview_panel.current_image.size().width() == 32

    def test_video_mode_ignores_live_frames(self, window: MainWindow) -> None:
        window.video.enter()
        image = QImage(16, 16, QImage.Format.Format_RGBA8888)
        window.events.on_frame(image)
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
        window.shell.submit_text("print('hi')")
        assert "(preview host is not running)" in window.shell.output.toPlainText()

    def test_host_log_appends(self, window: MainWindow) -> None:
        window.shell.append("hello from host")
        assert "hello from host" in window.shell.output.toPlainText()

    def test_host_state_label(self, window: MainWindow) -> None:
        window.events.on_host_state("crashed")
        assert "crashed" in window._host_state_label.text()
