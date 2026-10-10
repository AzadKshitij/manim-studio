"""End-to-end transport tests: real window, real preview host, real buttons.

Unlike the offscreen control-surface tests, this drives the actual
``MainWindow`` against a spawned ``manim_studio.preview_host`` and asserts the
host's behaviour (playback actually starts, pauses, loops, speeds up) - not just
which command was queued.  Skipped when no GL context is available.
"""

from __future__ import annotations

import shutil
import time
from pathlib import Path

import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QSignalSpy
from PySide6.QtWidgets import QApplication

from manim_studio.editor.main_window import MainWindow
from manim_studio.export import build_export_plan
from manim_studio.patches import SetDuration
from manim_studio.sidecar import load_sidecar, sidecar_path

SCENE_FILE = Path(__file__).parent / "scenes" / "feature_showcase.py"

pytestmark = pytest.mark.gl


def _click(qtbot, widget) -> None:  # type: ignore[no-untyped-def]
    qtbot.mouseClick(widget, Qt.MouseButton.LeftButton)


class LiveEditor:
    """One real window + host, with event recording helpers."""

    def __init__(self, window: MainWindow) -> None:
        self.window = window
        self.statuses: list[str] = []
        self.stopped: list[dict] = []
        self.errors: list[dict] = []
        window.controller.statusChanged.connect(lambda state, _message: self.statuses.append(state))
        window.controller.stoppedReceived.connect(self.stopped.append)
        window.controller.errorReceived.connect(self.errors.append)

    def wait_states(self, predicate, timeout: int = 30000) -> None:  # type: ignore[no-untyped-def]
        # Pump the Qt event loop: the status/stopped signals arrive on sockets that
        # only fire while the loop runs (a bare sleep would never observe them).
        deadline = time.monotonic() + timeout / 1000
        while time.monotonic() < deadline:
            QApplication.processEvents()
            if predicate():
                return
            time.sleep(0.02)
        raise TimeoutError("expected host state never arrived")

    def wait_status(self, state: str, timeout: int = 30000) -> None:
        self.wait_states(lambda: state in self.statuses, timeout)

    def play_count_statuses(self, state: str) -> int:
        return self.statuses.count(state)


@pytest.fixture
def live(qtbot, tmp_path: Path):  # type: ignore[no-untyped-def]
    scene = tmp_path / "feature_showcase.py"
    shutil.copy(SCENE_FILE, scene)
    window = MainWindow(
        path=str(scene),
        restore_settings=False,
        embedded=True,
        auto_preview=True,
        preview_size=(320, 180),
        completion=False,
    )
    qtbot.addWidget(window)
    window.resize(900, 600)
    window.show()
    editor = LiveEditor(window)
    try:
        qtbot.waitUntil(lambda: window.timeline.log.num_blocks >= 12, timeout=90000)
    except Exception:
        window.controller.shutdown()
        pytest.skip("preview host did not trace (GL unavailable?)")
    yield editor
    window.controller.shutdown()


# ---------------------------------------------------------------------------


def test_play_and_pause_buttons(live: LiveEditor, qtbot) -> None:  # type: ignore[no-untyped-def]
    window = live.window
    play_button = window.transport_bar._play_button
    total = window.events.total_duration
    assert total > 1.0

    # Play: the host actually starts and the button reads Pause.
    _click(qtbot, play_button)
    live.wait_status("playing")
    assert window.events.playing is True
    assert window._playback_active is True
    assert window.transport_bar.playing() is True

    # Pause: the host stops and the button returns to Play.
    before = len(live.stopped)
    _click(qtbot, play_button)
    live.wait_states(lambda: len(live.stopped) > before, timeout=15000)
    live.wait_status("idle")
    assert window.events.playing is False
    assert window._playback_active is False
    assert window.transport_bar.playing() is False
    assert live.stopped[-1]["user_stopped"] is True
    assert live.stopped[-1]["time"] < total  # stopped early, not at the end
    assert not live.errors


def test_loop_button_replays_and_pause_stops_it(live: LiveEditor, qtbot) -> None:  # type: ignore[no-untyped-def]
    window = live.window
    loop_check = window.transport_bar._loop_check

    # Enable looping (whole track) and play.
    _click(qtbot, loop_check)
    assert window.transport_bar.loop_enabled() is True
    _click(qtbot, window.transport_bar._play_button)
    live.wait_status("playing")

    # Two natural cycle endings prove the loop actually repeats.
    live.wait_states(
        lambda: sum(1 for s in live.stopped if not s["user_stopped"]) >= 2, timeout=60000
    )
    assert len(live.errors) == 0

    # The pause button stays "Pause" while looping...
    assert window._playback_active is True
    assert window.transport_bar._play_button.text() == "Pause"

    # ...and clicking it really pauses, then stays paused (no next cycle).
    _click(qtbot, window.transport_bar._play_button)
    live.wait_states(lambda: window.events.playing is False, timeout=15000)
    stopped = len(live.stopped)
    playing = live.statuses.count("playing")
    qtbot.wait(2500)  # give a buggy loop time to replay
    assert live.statuses.count("playing") == playing  # playback did not resume
    assert len(live.stopped) == stopped  # no further loop cycle
    assert window.transport_bar._play_button.text() == "Play"

    # Unchecking Loop leaves it off.
    _click(qtbot, loop_check)
    assert window.transport_bar.loop_enabled() is False


def test_step_and_jump_buttons(live: LiveEditor, qtbot) -> None:  # type: ignore[no-untyped-def]
    window = live.window
    ticks: list[float] = []
    window.controller.tickReceived.connect(lambda seconds, _i, _s: ticks.append(seconds))

    # Jump to the start, then step forward one frame.
    _click(qtbot, window.transport_bar._start_button)
    live.wait_states(lambda: window.timeline.playhead_time < 0.2, timeout=15000)
    step = window.timeline.frame_step
    ticks.clear()
    _click(qtbot, window.transport_bar._forward_button)
    live.wait_states(lambda: ticks and ticks[-1] > 0, timeout=15000)
    assert ticks[-1] == pytest.approx(step, abs=2 * step)

    # Jump to the end.
    total = window.events.total_duration
    _click(qtbot, window.transport_bar._end_button)
    live.wait_states(lambda: window.timeline.playhead_time > total - 0.2, timeout=15000)
    assert window.timeline.playhead_time == pytest.approx(total, abs=0.2)
    assert not live.errors


def test_speed_selector_makes_playback_faster(live: LiveEditor, qtbot) -> None:  # type: ignore[no-untyped-def]
    window = live.window
    total = window.events.total_duration

    # Baseline: a full 1x playthrough takes about `total` wall seconds.
    start = time.monotonic()
    _click(qtbot, window.transport_bar._play_button)
    live.wait_status("playing")
    live.wait_states(lambda: window.events.playing is False, timeout=int(total * 1000) + 30000)
    normal = time.monotonic() - start

    # 2x: a full playthrough is clearly faster but still covers the timeline.
    window.transport_bar.set_speed(2.0)
    before = len(live.stopped)
    start = time.monotonic()
    _click(qtbot, window.transport_bar._play_button)
    live.wait_status("playing")
    live.wait_states(
        lambda: len(live.stopped) > before and not live.stopped[-1]["user_stopped"], timeout=60000
    )
    fast = time.monotonic() - start
    assert live.stopped[-1]["time"] == pytest.approx(total, abs=0.5)  # reaches the end
    assert fast < normal * 0.85, (normal, fast)
    assert not live.errors


def _drag_right_edge(live: LiveEditor, qtbot, extra_seconds: float) -> tuple[int, float]:
    """Drag a block's right edge to stretch it by ``extra_seconds``.

    Returns ``(block_index, new_duration)``.  Snapping is disabled so the pointer
    lands on the exact duration the test asked for.
    """
    timeline = live.window.timeline
    timeline.fit()
    timeline.set_snapping(False)
    width = timeline.width()
    mid_y = timeline.height() // 2
    view = timeline.view()

    for index in range(timeline.log.num_blocks):
        block = timeline.log.block_by_index(index)
        if block is None:  # pragma: no cover - defensive
            continue
        edge_x = view.time_to_x(block.end)
        want = block.duration + extra_seconds
        target_x = view.time_to_x(block.start + want)
        if 10 < edge_x < width - 10 and target_x <= width - 2:
            new_duration = want
            break
    else:  # pragma: no cover - the showcase always has a usable block
        raise AssertionError("no block with room to stretch")

    resized = QSignalSpy(timeline.blockResized)
    qtbot.mousePress(timeline, Qt.MouseButton.LeftButton, pos=QPoint(int(edge_x), mid_y))
    assert timeline._mode == "resize"  # grabbing the edge, not scrubbing
    qtbot.mouseMove(timeline, QPoint(int(target_x), mid_y))
    qtbot.mouseRelease(timeline, Qt.MouseButton.LeftButton, pos=QPoint(int(target_x), mid_y))
    assert resized.count() == 1
    assert resized.at(0)[0] == index
    return index, new_duration


def test_retime_drag_stretches_the_timeline(live: LiveEditor, qtbot) -> None:  # type: ignore[no-untyped-def]
    """Dragging a block's right edge retimes the section and shifts later ones."""
    window = live.window
    old_total = window.events.total_duration
    index, new_duration = _drag_right_edge(live, qtbot, extra_seconds=1.0)

    block = window.timeline.log.block_by_index(index)
    assert block is not None
    assert block.duration == pytest.approx(new_duration, abs=0.05)
    # The override is applied in the editor immediately (optimistic), not only
    # once the host echoes it back.
    assert window.events.total_duration == pytest.approx(old_total + 1.0, abs=0.05)
    assert not live.errors


def test_retimed_section_plays_for_its_new_duration(live: LiveEditor, qtbot) -> None:  # type: ignore[no-untyped-def]
    """Playback must cover the retimed total, not the scene's original timing."""
    window = live.window
    old_total = window.events.total_duration
    _drag_right_edge(live, qtbot, extra_seconds=1.0)
    new_total = window.events.total_duration
    assert new_total > old_total + 0.5

    window.transport_bar.set_speed(2.0)  # keep the wall-clock bounded
    _click(qtbot, window.transport_bar._play_button)
    live.wait_status("playing")
    live.wait_states(lambda: window.events.playing is False, timeout=60000)

    ended = live.stopped[-1]
    assert ended["user_stopped"] is False
    # The host replayed the scene with the retimed run_times, so scene.time
    # reaches past the original total and lands near the stretched one.  The
    # tolerance matches the existing speed test: replayed wall-clock timing
    # drifts slightly from the traced durations.
    assert ended["time"] > old_total + 0.5
    assert ended["time"] == pytest.approx(new_total, abs=0.6)
    assert not live.errors


def test_timeline_scrollbar_follows_zoom(live: LiveEditor, qtbot) -> None:  # type: ignore[no-untyped-def]
    """The real window's zoom buttons drive the timeline scrollbar."""
    window = live.window
    timeline = window.timeline
    timeline.fit()
    bar = timeline.scrollbar
    assert bar.maximum() == 0  # whole timeline visible -> nothing to scroll

    _click(qtbot, window.transport_bar._zoom_in_button)
    assert timeline.view().span < timeline.log.total_duration
    assert bar.maximum() > 0
    assert bar.isEnabled() is True

    # Dragging the handle pans the zoomed view.
    bar.setValue(bar.maximum())
    assert timeline.view().start == pytest.approx(bar.maximum() / 1000.0, abs=1e-3)

    _click(qtbot, window.transport_bar._fit_button)
    assert bar.maximum() == 0


def _wait_for_trace(live: LiveEditor, trigger) -> None:  # type: ignore[no-untyped-def]
    """Run ``trigger`` and wait (pumping Qt) for the host's next ``log`` event."""
    spy = QSignalSpy(live.window.controller.logReceived)
    trigger()
    live.wait_states(lambda: spy.count() >= 1, timeout=90000)


def test_a_retime_is_stored_in_the_sidecar_and_exports_read_it(  # type: ignore[no-untyped-def]
    live: LiveEditor, qtbot
) -> None:
    """The edit lives next to the scene, not in it, and the export plan picks it up."""
    window = live.window
    source_before = window._path.read_text(encoding="utf-8")
    _index, new_duration = _drag_right_edge(live, qtbot, extra_seconds=0.5)

    patches = load_sidecar(window._path)
    assert len(patches) == 1 and isinstance(patches[0], SetDuration)
    assert patches[0].seconds == pytest.approx(new_duration, abs=0.05)
    assert build_export_plan(window._path).patches == patches
    assert window._path.read_text(encoding="utf-8") == source_before
    assert window._edits_label.text() == "1 timeline edit"


def test_a_retime_survives_a_code_edit(live: LiveEditor, qtbot) -> None:  # type: ignore[no-untyped-def]
    """Edits are anchored on the call, so adding code above does not lose them."""
    window = live.window
    index, new_duration = _drag_right_edge(live, qtbot, extra_seconds=0.5)
    live.wait_states(lambda: window.patches.applied_count == 1, timeout=90000)

    # Insert statements above every play(): line numbers shift, block indices do not.
    first_line = window.events.call_line(0)
    assert first_line is not None
    window.editor.insert_line_at(first_line, "        padding = 1")
    window.editor.insert_line_at(first_line, "        another = 2")
    _wait_for_trace(live, window.save_buffer)
    live.wait_states(lambda: window.patches.applied_count == 1, timeout=90000)

    assert window.patches.orphan_count == 0
    block = window.timeline.log.block_by_index(index)
    assert block is not None
    assert block.duration == pytest.approx(new_duration, abs=0.05)
    assert not live.errors


def test_an_edit_whose_call_disappears_is_orphaned_not_misapplied(  # type: ignore[no-untyped-def]
    live: LiveEditor, qtbot
) -> None:
    """Rewriting the retimed call orphans the edit; discarding it removes the sidecar."""
    window = live.window
    index, _new_duration = _drag_right_edge(live, qtbot, extra_seconds=0.5)
    live.wait_states(lambda: window.patches.applied_count == 1, timeout=90000)
    durations_before = [b.duration for b in window.timeline.log.blocks]

    line = window.events.call_line(index)
    assert line is not None
    lines = window.editor.text().split("\n")
    indent = lines[line - 1][: len(lines[line - 1]) - len(lines[line - 1].lstrip())]
    lines[line - 1] = f"{indent}self.wait(0.7)  # replaced"
    window.editor.replace_text("\n".join(lines))
    _wait_for_trace(live, window.save_buffer)
    live.wait_states(lambda: window.patches.orphan_count == 1, timeout=90000)

    assert window.patches.applied_count == 0
    assert "orphaned" in window._edits_label.text()
    assert "no longer exists" in window.patches.orphans[0].reason
    # The replaced call keeps its own timing: the stretch was not moved onto it.
    replaced = window.timeline.log.block_by_index(index)
    assert replaced is not None and replaced.duration == pytest.approx(0.7, abs=0.05)
    assert len(window.timeline.log.blocks) == len(durations_before)
    assert sidecar_path(window._path).exists()  # kept, so it can be re-anchored

    window.discard_orphaned_edits()
    assert window.patches.edit_count == 0
    assert not sidecar_path(window._path).exists()
