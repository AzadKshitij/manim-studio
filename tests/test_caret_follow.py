"""A caret that enters a play()/wait() call selects its block and moves the playhead."""

from __future__ import annotations

import pytest

from manim_studio.editor.main_window import MainWindow

#: Three blocks; block 0 has its call on line 10, block 1 on line 12, block 2 on line 14.
#: Lines 12-13 therefore belong to block 1.
TRACE = {
    "event": "log",
    "blocks": [
        {"name": "ShowCreation", "start": 0.0, "duration": 1.0, "index": 0},
        {"name": "Wait", "start": 1.0, "duration": 0.5, "index": 1},
        {"name": "Transform", "start": 1.5, "duration": 2.0, "index": 2},
    ],
    "stats": {"play": 2, "wait": 1},
    "total_duration": 3.5,
    "call_sites": [10, 12, 14],
}


@pytest.fixture
def window(qtbot) -> MainWindow:  # type: ignore[no-untyped-def]
    win = MainWindow(restore_settings=False, auto_preview=False, completion=False)
    qtbot.addWidget(win)
    win.events.on_log(TRACE)
    return win


def test_entering_a_call_selects_its_block(window: MainWindow) -> None:
    window.editor.cursorMoved.emit(13, 1)
    assert window.timeline.selected_block == 1


def test_entering_a_call_moves_the_playhead_to_its_block(window: MainWindow) -> None:
    window.editor.cursorMoved.emit(13, 1)
    assert window.timeline.playhead_time == pytest.approx(1.0)


def test_moving_within_the_same_call_keeps_the_playhead_the_user_set(window: MainWindow) -> None:
    window.editor.cursorMoved.emit(13, 1)
    window.timeline.set_playhead(0.25)  # the user scrubbed away from the call
    window.editor.cursorMoved.emit(12, 1)  # still inside block 1
    assert window.timeline.playhead_time == pytest.approx(0.25)


def test_a_caret_outside_any_call_changes_nothing(window: MainWindow) -> None:
    window.timeline.set_playhead(0.25)
    window.editor.cursorMoved.emit(5, 1)
    assert window.timeline.selected_block is None
    assert window.timeline.playhead_time == pytest.approx(0.25)


def test_during_playback_the_caret_selects_but_does_not_move_the_playhead(
    window: MainWindow,
) -> None:
    window._playback_active = True
    window.timeline.set_playhead(0.25)
    window.editor.cursorMoved.emit(13, 1)
    assert window.timeline.selected_block == 1
    assert window.timeline.playhead_time == pytest.approx(0.25)


def test_a_fresh_trace_highlights_the_caret_block_without_moving_the_playhead(
    window: MainWindow,
) -> None:
    window.editor.set_text("\n".join(f"line {number}" for number in range(1, 21)))
    window.editor.go_to_line(13)
    window.timeline.set_playhead(0.25)
    window._on_trace_log(TRACE)
    assert window.timeline.active_block == 1
    assert window.timeline.playhead_time == pytest.approx(0.25)
