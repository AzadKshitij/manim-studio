"""End-to-end control-surface tests: every button drives the right command.

The window is real; the preview host is a recording stub, so one flow can exercise
the transport bar, the main toolbar, the menu actions and the block context menu
together and assert the exact command sequence the editor would send.  This is the
"all the buttons at once" contract, complementing the focused per-widget tests.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from PySide6.QtCore import Qt

from manim_studio.editor import main_window as main_window_module
from manim_studio.editor.main_window import MainWindow

SCENE_SOURCE = (
    "from manimlib import Scene\n"
    "\n"
    "\n"
    "class Controls(Scene):\n"
    "    def construct(self):\n"
    "        circle = Circle()\n"
    "        self.play(ShowCreation(circle), run_time=0.3)\n"
    "        self.wait(0.5)\n"
    "        self.play(Rotate(circle, 90 * DEG), run_time=1.0)\n"
)

LOG = {
    "event": "log",
    "blocks": [
        {"name": "ShowCreation", "start": 0.0, "duration": 1.0, "index": 0},
        {"name": "Wait", "start": 1.0, "duration": 0.5, "index": 1},
        {"name": "Transform", "start": 1.5, "duration": 2.0, "index": 2},
    ],
    "total_duration": 3.5,
    "call_sites": [7, 8, 9],
    "sites": [
        {"id": "Controls.construct:play:aa:0", "occ": 0},
        {"id": "Controls.construct:wait:bb:0", "occ": 0},
        {"id": "Controls.construct:play:cc:0", "occ": 0},
    ],
    "patches": {"applied": 0, "orphaned": []},
    "fps": 30,
}
SITES = [site["id"] for site in LOG["sites"]]  # type: ignore[index, union-attr]


class RecordingController:
    """Records every command the window sends (stands in for the preview host)."""

    def __init__(self) -> None:
        self.commands: list[tuple[str, tuple]] = []
        self.patches: list[dict] = []
        self.is_running = True

    def _record(self, name: str, *payload: object) -> bool:
        self.commands.append((name, payload))
        return True

    def load(self, path, scene_name=None, *, fresh: bool = False) -> bool:
        if fresh:
            return self._record("load_fresh", str(path), scene_name)
        return self._record("load", str(path), scene_name)

    def reload(self, path=None) -> bool:
        return self._record("reload", str(path) if path is not None else None)

    def play(self, from_index: int = 0, to_index=None, rate: float = 1.0) -> bool:
        return self._record("play", int(from_index), to_index, float(rate))

    def stop_playback(self) -> bool:
        return self._record("stop")

    def seek(self, index: int) -> bool:
        return self._record("seek", int(index))

    def scrub(self, time_seconds: float) -> bool:
        return self._record("scrub", float(time_seconds))

    def set_patches(self, patches, time_seconds: float = 0.0) -> bool:
        self.patches = list(patches)
        return self._record("set_patches", list(patches), float(time_seconds))

    def exec_code(self, code: str) -> bool:
        return self._record("exec", code)

    def shutdown(self) -> None:
        pass

    # -- helpers -----------------------------------------------------------

    def names(self) -> list[str]:
        return [name for name, _ in self.commands]

    def last(self, name: str) -> tuple | None:
        for command, payload in reversed(self.commands):
            if command == name:
                return payload
        return None

    def count(self, name: str) -> int:
        return sum(1 for command, _ in self.commands if command == name)


class StubExports:
    def __init__(self) -> None:
        self.stills: list[tuple] = []
        self.preview_renders: list[tuple] = []

    def export_still(self, path, scene_name, output, *, animation_range=None) -> bool:
        self.stills.append((str(path), scene_name, str(output), animation_range))
        return True

    def render_preview_video(self, path, scene_name, output) -> bool:
        self.preview_renders.append((str(path), scene_name, str(output)))
        return True

    def export_video(self, *args, **kwargs) -> bool:
        return True

    def shutdown(self) -> None:
        pass


@pytest.fixture
def window(qtbot, tmp_path: Path):  # type: ignore[no-untyped-def]
    scene = tmp_path / "controls.py"
    scene.write_text(SCENE_SOURCE, encoding="utf-8")
    main = MainWindow(restore_settings=False, embedded=True, auto_preview=False, completion=False)
    qtbot.addWidget(main)
    main.resize(1200, 800)
    main.show()
    recorder = RecordingController()
    main.controller = recorder  # type: ignore[assignment]
    main.exports = StubExports()  # type: ignore[assignment]
    assert main.open_path(scene) is True
    main.watcher.stop()
    main.events.on_log(LOG)
    main._on_trace_log(LOG)
    return main


def _click(qtbot, widget) -> None:  # type: ignore[no-untyped-def]
    qtbot.mouseClick(widget, Qt.MouseButton.LeftButton)


class TestPreviewPanelHasNoTransportButtons:
    """The embedded viewport keeps only its status header (image-2 regression)."""

    def test_preview_panel_has_no_reload_play_stop(self, window: MainWindow) -> None:
        assert not hasattr(window.preview_panel, "_reload_button")
        assert not hasattr(window.preview_panel, "_play_button")
        assert not hasattr(window.preview_panel, "_stop_button")

    def test_preview_panel_has_no_transport_signals(self, window: MainWindow) -> None:
        for name in ("reloadRequested", "playRequested", "stopRequested"):
            assert not hasattr(window.preview_panel, name)


class TestTransportBarButtons:
    def test_play_pause_button(self, window: MainWindow, qtbot) -> None:
        recorder = window.controller
        window.events.on_status("paused", {})  # host reports not playing
        _click(qtbot, window.transport_bar._play_button)
        assert recorder.last("play") == (0, None, 1.0)  # type: ignore[union-attr]
        window.events.on_status("playing", {})
        _click(qtbot, window.transport_bar._play_button)
        assert recorder.count("stop") == 1  # type: ignore[union-attr]

    def test_step_buttons(self, window: MainWindow, qtbot) -> None:
        recorder = window.controller
        window.scrub_to_time(1.0)
        _click(qtbot, window.transport_bar._forward_button)
        assert recorder.last("scrub") == (1.0 + window.timeline.frame_step,)  # type: ignore[union-attr]
        _click(qtbot, window.transport_bar._back_button)
        assert recorder.last("scrub") == (1.0,)  # type: ignore[union-attr]

    def test_start_and_end_buttons(self, window: MainWindow, qtbot) -> None:
        recorder = window.controller
        _click(qtbot, window.transport_bar._start_button)
        assert recorder.last("scrub") == (0.0,)  # type: ignore[union-attr]
        _click(qtbot, window.transport_bar._end_button)
        assert recorder.last("scrub") == (3.5,)  # type: ignore[union-attr]

    def test_loop_checkbox_bounds_playback(self, window: MainWindow, qtbot) -> None:
        recorder = window.controller
        window.timeline.set_range(1.0, 1.5)  # block 1
        _click(qtbot, window.transport_bar._loop_check)
        assert window.transport_bar.loop_enabled() is True
        _click(qtbot, window.transport_bar._play_button)
        assert recorder.last("play") == (1, 1, 1.0)  # type: ignore[union-attr]

    def test_speed_combo_reaches_play(self, window: MainWindow, qtbot) -> None:
        recorder = window.controller
        window.transport_bar.set_speed(2.0)
        _click(qtbot, window.transport_bar._play_button)
        assert recorder.last("play") == (0, None, 2.0)  # type: ignore[union-attr]

    def test_zoom_buttons(self, window: MainWindow, qtbot) -> None:
        _click(qtbot, window.transport_bar._zoom_in_button)
        zoomed = window.timeline.view().span
        _click(qtbot, window.transport_bar._fit_button)
        assert window.timeline.view().span > zoomed
        window.timeline.set_range(1.0, 1.5)
        _click(qtbot, window.transport_bar._selection_button)
        assert window.timeline.view().span < 3.5


class TestToolbarAndMenu:
    def test_toolbar_reload_play_stop(self, window: MainWindow) -> None:
        recorder = window.controller
        # Reload re-issues `load` with the explicit path + scene (by design).
        window._toolbar_actions["Reload"].trigger()
        assert recorder.count("load") == 1  # type: ignore[union-attr]
        window._toolbar_actions["Play"].trigger()
        assert recorder.count("play") == 1  # type: ignore[union-attr]
        window._toolbar_actions["Stop"].trigger()
        assert recorder.count("stop") == 1  # type: ignore[union-attr]

    def test_menu_reload_fresh_asks_the_host_to_discard_its_cache(self, window: MainWindow) -> None:
        recorder = window.controller
        window.reload_preview_fresh()
        assert recorder.count("load_fresh") == 1  # type: ignore[union-attr]
        assert recorder.count("load") == 0  # type: ignore[union-attr]
        window.reload_preview()
        assert recorder.count("load") == 1  # type: ignore[union-attr]

    def test_menu_reload_play_stop(self, window: MainWindow) -> None:
        recorder = window.controller
        window.reload_preview()
        assert recorder.count("load") == 1  # type: ignore[union-attr]
        window.request_play()
        assert recorder.count("play") == 1  # type: ignore[union-attr]
        window.request_stop()
        assert recorder.count("stop") == 1  # type: ignore[union-attr]


class TestContextMenuButtons:
    def _action(self, window: MainWindow, index: int, text: str):
        for action in window.timeline.context_menu(index).actions():
            if action.text() == text:
                return action
        raise AssertionError(f"no action {text!r}")

    def test_play_this_block(self, window: MainWindow) -> None:
        recorder = window.controller
        self._action(window, 2, "Play this block").trigger()
        assert recorder.last("play") == (2, None, 1.0)  # type: ignore[union-attr]

    def test_loop_this_block_toggles(self, window: MainWindow) -> None:
        recorder = window.controller
        action = self._action(window, 1, "Loop this block")
        assert action.isCheckable() is True
        action.trigger()
        assert window.timeline.block_is_looped(1) is True
        assert recorder.last("play") == (1, 1, 1.0)  # type: ignore[union-attr]
        # Clicking again (checkbox on) undoes the loop.
        self._action(window, 1, "Loop this block").trigger()
        assert window.timeline.block_is_looped(1) is False
        assert window.transport_bar.loop_enabled() is False
        assert window.timeline.range() is None

    def test_copy_and_paste_timing(self, window: MainWindow) -> None:
        recorder = window.controller
        self._action(window, 0, "Copy timing").trigger()  # duration 1.0
        self._action(window, 2, "Paste timing").trigger()
        assert recorder.last("set_patches")[0] == [  # type: ignore[union-attr]
            {"op": "set_duration", "site": SITES[2], "seconds": 1.0}
        ]

    def test_reset_retime(self, window: MainWindow) -> None:
        recorder = window.controller
        window._on_block_resized(1, 3.0)
        assert recorder.count("set_patches") == 1  # type: ignore[union-attr]
        self._action(window, 1, "Reset retime").trigger()
        assert recorder.count("set_patches") == 2  # type: ignore[union-attr]
        # With no edits left the host is told to run the untouched scene again.
        assert recorder.last("set_patches")[0] == []  # type: ignore[union-attr]

    def test_jump_to_code(self, window: MainWindow) -> None:
        self._action(window, 2, "Jump to code").trigger()
        assert window.editor.current_line() == 9

    def test_zoom_to_block(self, window: MainWindow) -> None:
        self._action(window, 2, "Zoom to block").trigger()
        assert window.timeline.view().span < 3.5

    def test_set_as_export_range(self, window: MainWindow, monkeypatch: pytest.MonkeyPatch) -> None:
        captured: dict = {}

        class _Dialog:
            def __init__(self, *args: object, **kwargs: object) -> None:
                captured.update(kwargs)

            def exec(self) -> int:
                return 0

        monkeypatch.setattr(main_window_module, "ExportDialog", _Dialog)
        self._action(window, 2, "Set as export range").trigger()
        assert captured["initial_range"] == (2, 2)

    def test_render_still_at_block_end(self, window: MainWindow) -> None:
        exports = window.exports
        self._action(window, 1, "Render still at block end").trigger()
        assert exports.stills[-1][3] == (0, 2)  # type: ignore[union-attr]


class TestFullPlaybackFlow:
    """One flow that exercises several controls in sequence, as a user would."""

    def test_loop_speed_scrub_retime_then_stop(self, window: MainWindow) -> None:
        recorder = window.controller
        # 1. slow the review down and loop block 2.
        window.transport_bar.set_speed(0.5)
        window.timeline.set_range(1.5, 3.5)
        window.transport_bar.set_loop(True)
        window.request_play()
        assert recorder.last("play") == (2, 2, 0.5)  # type: ignore[union-attr]

        # 2. playback finishing on its own repeats the loop...
        window._on_stopped_loop({"user_stopped": False})
        assert recorder.count("play") == 2  # type: ignore[union-attr]
        # ...but a user stop does not.
        window._on_stopped_loop({"user_stopped": True})
        assert recorder.count("play") == 2  # type: ignore[union-attr]

        # 3. scrub, step, jump and seek all reach the host.
        window.scrub_to_time(1.25)
        assert recorder.last("scrub") == (1.25,)  # type: ignore[union-attr]
        window._on_transport_step(1)
        window.seek_to_block(2)
        assert recorder.last("seek") == (2,)  # type: ignore[union-attr]

        # 4. retime then undo (separate timeline-edit stack).
        window.timeline.blockResized.emit(0, 2.0)
        assert window.timeline.log.block_by_index(0).duration == pytest.approx(2.0)
        assert recorder.last("set_patches")[0] == [  # type: ignore[union-attr]
            {"op": "set_duration", "site": SITES[0], "seconds": 2.0}
        ]
        window.timeline.undoRequested.emit()
        assert recorder.last("set_patches")[0] == []  # type: ignore[union-attr]

        # 5. stop everything.
        window.request_stop()
        assert recorder.count("stop") == 1  # type: ignore[union-attr]

    def test_insert_hold_end_to_end(
        self, window: MainWindow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        scene = tmp_path / "controls.py"
        monkeypatch.setattr(
            main_window_module,
            "QInputDialog",
            type("_D", (), {"getDouble": staticmethod(lambda *a, **k: (0.25, True))}),
        )
        window.timeline.set_playhead(0.0)
        window.insert_hold_at_playhead()
        text = scene.read_text(encoding="utf-8")
        assert "        self.wait(0.25)\n" in text
        assert text.index("self.wait(0.25)") < text.index("self.play(ShowCreation")
