"""Offscreen tests for the interactive timeline widget."""

from __future__ import annotations

import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QPixmap
from PySide6.QtTest import QSignalSpy

from manim_studio.editor.timeline_widget import (
    FRAME_STEP,
    SCROLLBAR_HEIGHT,
    TimelineWidget,
    block_color,
    hash_name,
)
from manim_studio.timeline_model import TimelineBlock, TimelineLog


def make_log() -> TimelineLog:
    return TimelineLog(
        (
            TimelineBlock(0, "ShowCreation", 0.0, 1.0),
            TimelineBlock(1, "Wait", 1.0, 1.0),
            TimelineBlock(2, "Transform", 2.0, 2.0),
        )
    )


@pytest.fixture
def timeline(qtbot) -> TimelineWidget:
    widget = TimelineWidget()
    qtbot.addWidget(widget)
    widget.resize(600, 110)
    widget.set_log(make_log())
    widget.show()
    widget.setFocus()
    return widget


class TestHelpers:
    def test_hash_is_deterministic(self) -> None:
        assert hash_name("ShowCreation") == hash_name("ShowCreation")
        assert hash_name("ShowCreation") != hash_name("Transform")

    def test_block_color_stable(self) -> None:
        assert block_color("Wait") == block_color("Wait")
        assert block_color("Wait") != block_color("Rotate")


class TestPainting:
    def test_paints_with_blocks(self, timeline: TimelineWidget) -> None:
        pixmap = QPixmap(timeline.size())
        timeline.render(pixmap)
        assert not pixmap.isNull()

    def test_paints_without_trace(self, qtbot) -> None:
        widget = TimelineWidget()
        qtbot.addWidget(widget)
        widget.resize(400, 110)
        pixmap = QPixmap(widget.size())
        widget.render(pixmap)
        assert not pixmap.isNull()

    def test_playhead_updates(self, timeline: TimelineWidget) -> None:
        timeline.set_playhead(1.5)
        assert timeline.playhead_time == pytest.approx(1.5)
        timeline.set_playing(True)
        timeline.render(QPixmap(timeline.size()))


class TestScrubbing:
    def test_click_positions_playhead_exactly(self, timeline: TimelineWidget, qtbot) -> None:
        spy = QSignalSpy(timeline.scrubRequested)
        qtbot.mouseClick(timeline, Qt.MouseButton.LeftButton, pos=QPoint(225, 60))
        assert spy.count() >= 1
        assert spy.at(spy.count() - 1)[0] == pytest.approx(1.5, abs=0.02)
        assert timeline.playhead_time == pytest.approx(1.5, abs=0.02)

    def test_drag_scrubs_continuously(self, timeline: TimelineWidget, qtbot) -> None:
        spy = QSignalSpy(timeline.scrubRequested)
        qtbot.mousePress(timeline, Qt.MouseButton.LeftButton, pos=QPoint(100, 60))
        qtbot.mouseMove(timeline, QPoint(200, 60))
        qtbot.mouseMove(timeline, QPoint(300, 60))
        qtbot.mouseMove(timeline, QPoint(400, 60))
        qtbot.mouseRelease(timeline, Qt.MouseButton.LeftButton, pos=QPoint(400, 60))
        values = [spy.at(i)[0] for i in range(spy.count())]
        assert len(values) >= 4  # one per move plus press/release
        assert values == sorted(values)  # monotonically rightwards
        assert values[-1] == pytest.approx(400 / 600 * 4.0, abs=0.02)

    def test_scrub_is_not_snapped_to_blocks(self, timeline: TimelineWidget, qtbot) -> None:
        spy = QSignalSpy(timeline.scrubRequested)
        qtbot.mouseClick(timeline, Qt.MouseButton.LeftButton, pos=QPoint(375, 60))
        assert spy.at(spy.count() - 1)[0] == pytest.approx(2.5, abs=0.02)

    def test_hover_tracks_blocks(self, timeline: TimelineWidget, qtbot) -> None:
        qtbot.mouseMove(timeline, QPoint(60, 60))  # inside block 0
        assert timeline._hover_index == 0
        qtbot.mouseMove(timeline, QPoint(510, 60))  # inside block 2
        assert timeline._hover_index == 2


class TestActiveBlock:
    def test_set_active_block(self, timeline: TimelineWidget) -> None:
        timeline.set_active_block(2)
        assert timeline.active_block == 2
        timeline.render(QPixmap(timeline.size()))
        timeline.set_active_block(None)
        assert timeline.active_block is None

    def test_set_log_clears_active(self, timeline: TimelineWidget) -> None:
        timeline.set_active_block(1)
        timeline.set_log(make_log())
        assert timeline.active_block is None


class TestSnapping:
    def test_defaults_to_on(self, timeline: TimelineWidget) -> None:
        assert timeline.snapping is True
        timeline.set_snapping(False)
        assert timeline.snapping is False

    def test_scrub_snaps_onto_a_block_edge(self, timeline: TimelineWidget, qtbot) -> None:
        # 2.0s is the end of block 1; x=306 is 2.04s — inside the 8px snap window
        # but outside the 5px edge-grab window, so it scrubs (not retimes).
        spy = QSignalSpy(timeline.scrubRequested)
        qtbot.mouseClick(timeline, Qt.MouseButton.LeftButton, pos=QPoint(306, 60))
        assert spy.count() >= 1
        assert spy.at(spy.count() - 1)[0] == pytest.approx(2.0, abs=1e-6)

    def test_scrub_stays_free_when_snapping_is_off(self, timeline: TimelineWidget, qtbot) -> None:
        timeline.set_snapping(False)
        spy = QSignalSpy(timeline.scrubRequested)
        qtbot.mouseClick(timeline, Qt.MouseButton.LeftButton, pos=QPoint(306, 60))
        assert spy.count() >= 1
        assert spy.at(spy.count() - 1)[0] == pytest.approx(306 / 600 * 4.0, abs=0.02)

    def test_retime_snaps_to_a_neighbouring_edge(self, timeline: TimelineWidget, qtbot) -> None:
        # Drag block 0's right edge to ~2.013s; it should snap onto 2.0.
        resized = QSignalSpy(timeline.blockResized)
        qtbot.mousePress(timeline, Qt.MouseButton.LeftButton, pos=QPoint(150, 60))
        qtbot.mouseMove(timeline, QPoint(302, 60))
        qtbot.mouseRelease(timeline, Qt.MouseButton.LeftButton, pos=QPoint(302, 60))
        assert resized.count() == 1
        assert resized.at(0)[1] == pytest.approx(2.0, abs=1e-6)

    def test_set_fps_ignores_bad_values(self, timeline: TimelineWidget) -> None:
        timeline.set_fps(60)
        assert timeline._fps == pytest.approx(60.0)
        timeline.set_fps(None)
        timeline.set_fps("nope")
        assert timeline._fps == pytest.approx(60.0)


class TestResizing:
    def test_right_edge_press_resizes_instead_of_scrubbing(
        self, timeline: TimelineWidget, qtbot
    ) -> None:
        scrub = QSignalSpy(timeline.scrubRequested)
        resized = QSignalSpy(timeline.blockResized)
        qtbot.mousePress(timeline, Qt.MouseButton.LeftButton, pos=QPoint(150, 60))
        assert timeline._mode == "resize"
        assert scrub.count() == 0
        qtbot.mouseMove(timeline, QPoint(225, 60))  # 1.5 s
        qtbot.mouseRelease(timeline, Qt.MouseButton.LeftButton, pos=QPoint(225, 60))
        assert resized.count() == 1
        index, duration = resized.at(0)
        assert index == 0
        assert duration == pytest.approx(1.5, abs=0.02)

    def test_interior_press_still_scrubs(self, timeline: TimelineWidget, qtbot) -> None:
        scrub = QSignalSpy(timeline.scrubRequested)
        qtbot.mouseClick(timeline, Qt.MouseButton.LeftButton, pos=QPoint(75, 60))
        assert scrub.count() >= 1

    def test_shared_boundary_resizes_the_earlier_block(
        self, timeline: TimelineWidget, qtbot
    ) -> None:
        # x=300 is the end of block 1 and the start of block 2; it must retime block 1.
        resized = QSignalSpy(timeline.blockResized)
        qtbot.mousePress(timeline, Qt.MouseButton.LeftButton, pos=QPoint(300, 60))
        qtbot.mouseRelease(timeline, Qt.MouseButton.LeftButton, pos=QPoint(300, 60))
        assert resized.count() == 1
        assert resized.at(0)[0] == 1

    def test_drag_preview_is_visual_only_until_release(
        self, timeline: TimelineWidget, qtbot
    ) -> None:
        qtbot.mousePress(timeline, Qt.MouseButton.LeftButton, pos=QPoint(150, 60))
        qtbot.mouseMove(timeline, QPoint(300, 60))  # 2.0 s
        display = timeline._display_log()
        assert display.block_by_index(0).duration == pytest.approx(2.0, abs=0.02)
        assert display.block_by_index(1).start == pytest.approx(2.0, abs=0.02)
        # the authoritative log is only updated when the host confirms
        assert timeline.log.block_by_index(0).duration == 1.0
        qtbot.mouseRelease(timeline, Qt.MouseButton.LeftButton, pos=QPoint(300, 60))
        assert timeline.log.block_by_index(0).duration == 1.0

    def test_resize_clamps_to_one_frame(self, timeline: TimelineWidget, qtbot) -> None:
        resized = QSignalSpy(timeline.blockResized)
        qtbot.mousePress(timeline, Qt.MouseButton.LeftButton, pos=QPoint(150, 60))
        qtbot.mouseMove(timeline, QPoint(0, 60))  # would be negative -> clamp
        qtbot.mouseRelease(timeline, Qt.MouseButton.LeftButton, pos=QPoint(0, 60))
        assert resized.count() == 1
        assert resized.at(0)[1] == pytest.approx(1.0 / 30.0, abs=1e-6)

    def test_ctrl_double_click_activates_the_block(self, timeline: TimelineWidget, qtbot) -> None:
        activated = QSignalSpy(timeline.blockActivated)
        qtbot.mouseDClick(
            timeline,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.ControlModifier,
            QPoint(60, 60),
        )
        assert activated.count() == 1
        assert activated.at(0)[0] == 0

    def test_plain_double_click_requests_a_duration_edit(
        self, timeline: TimelineWidget, qtbot
    ) -> None:
        duration = QSignalSpy(timeline.blockDurationEditRequested)
        activated = QSignalSpy(timeline.blockActivated)
        qtbot.mouseDClick(timeline, Qt.MouseButton.LeftButton, pos=QPoint(60, 60))
        assert duration.count() == 1
        assert duration.at(0)[0] == 0
        assert activated.count() == 0

    def test_set_log_clears_a_pending_preview(self, timeline: TimelineWidget, qtbot) -> None:
        qtbot.mousePress(timeline, Qt.MouseButton.LeftButton, pos=QPoint(150, 60))
        timeline.set_log(make_log())
        assert timeline._resize_index is None
        assert timeline._display_log().block_by_index(0).duration == 1.0


class TestViewNavigation:
    def test_plain_wheel_pans(self, timeline: TimelineWidget) -> None:
        timeline.wheelEvent(_wheel(120, Qt.KeyboardModifier.ControlModifier))  # zoom in first
        before = timeline.view().start
        timeline.wheelEvent(_wheel(-120))
        assert timeline.view().start > before
        assert timeline.view().span < 4.0

    def test_ctrl_wheel_zooms(self, timeline: TimelineWidget) -> None:
        before = timeline.view().span
        timeline.wheelEvent(_wheel(120, Qt.KeyboardModifier.ControlModifier))
        assert timeline.view().span < before

    def test_zoom_out_clamps_to_fit(self, timeline: TimelineWidget) -> None:
        timeline.wheelEvent(_wheel(120, Qt.KeyboardModifier.ControlModifier))
        timeline.wheelEvent(_wheel(-120, Qt.KeyboardModifier.ControlModifier))
        assert timeline.view().span == pytest.approx(4.0, abs=0.01)

    def test_zoom_to_block_frames_the_block(self, timeline: TimelineWidget) -> None:
        # Block 2 spans [2,4] -> span 2.3s, start clamped so the view stays inside [0,4]
        timeline.zoom_to_block(2)
        assert timeline.view().span == pytest.approx(2.0 * 1.15, abs=0.05)
        assert timeline.view().start == pytest.approx(4.0 - 2.3, abs=0.05)

    def test_fit_resets(self, timeline: TimelineWidget) -> None:
        timeline.zoom_to_block(2)
        timeline.fit()
        assert timeline.view().span == pytest.approx(4.0, abs=0.01)
        assert timeline.view().start == 0.0


class TestRange:
    def test_set_and_clear_range(self, timeline: TimelineWidget) -> None:
        spy = QSignalSpy(timeline.rangeChanged)
        timeline.set_range(1.0, 2.0)
        assert timeline.range() == pytest.approx((1.0, 2.0))
        assert spy.count() == 1
        timeline.clear_range()
        assert timeline.range() is None

    def test_shift_drag_on_the_ruler_sets_a_range(self, timeline: TimelineWidget, qtbot) -> None:
        qtbot.mousePress(
            timeline,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.ShiftModifier,
            pos=QPoint(150, 5),
        )
        qtbot.mouseMove(timeline, QPoint(300, 5))
        qtbot.mouseRelease(
            timeline,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.ShiftModifier,
            pos=QPoint(300, 5),
        )
        assert timeline.range() == pytest.approx((1.0, 2.0), abs=0.05)

    def test_set_log_clears_the_range(self, timeline: TimelineWidget) -> None:
        timeline.set_range(0.5, 1.5)
        timeline.set_log(make_log())
        assert timeline.range() is None


class TestContextMenu:
    @staticmethod
    def _action(menu, text: str):
        for action in menu.actions():
            if action.text() == text:
                return action
        raise AssertionError(f"no action {text!r} in {[a.text() for a in menu.actions()]}")

    def test_block_menu_lists_the_actions(self, timeline: TimelineWidget) -> None:
        texts = [a.text() for a in timeline.context_menu(1).actions() if a.text()]
        assert texts == [
            "Play this block",
            "Loop this block",
            "Set as export range",
            "Render still at block end",
            "Copy timing",
            "Paste timing",
            "Reset retime",
            "Delete this call",
            "Jump to code",
            "Zoom to block",
        ]

    def test_loop_blocks_offer_a_per_iteration_retime(self, timeline: TimelineWidget) -> None:
        assert "Retime this iteration only…" not in [
            a.text() for a in timeline.context_menu(1).actions()
        ]
        timeline.set_block_markers({1: 3}, [])
        iteration = QSignalSpy(timeline.blockIterationEditRequested)
        self._action(timeline.context_menu(1), "Retime this iteration only…").trigger()
        assert iteration.at(0)[0] == 1

    def test_delete_signal(self, timeline: TimelineWidget) -> None:
        deleted = QSignalSpy(timeline.blockDeleteRequested)
        self._action(timeline.context_menu(2), "Delete this call").trigger()
        assert deleted.at(0)[0] == 2

    def test_block_markers_are_queryable_and_ignore_trivial_fan_out(
        self, timeline: TimelineWidget
    ) -> None:
        timeline.set_block_markers({0: 1, 1: 4}, [2])
        assert [timeline.iterations(i) for i in range(3)] == [1, 4, 1]
        assert timeline.is_edited(2) and not timeline.is_edited(0)

    def test_play_and_loop_and_export_signals(self, timeline: TimelineWidget) -> None:
        play = QSignalSpy(timeline.blockPlayRequested)
        loop = QSignalSpy(timeline.blockLoopRequested)
        export = QSignalSpy(timeline.blockExportRangeRequested)
        still = QSignalSpy(timeline.blockStillRequested)
        menu = timeline.context_menu(2)
        self._action(menu, "Play this block").trigger()
        self._action(menu, "Loop this block").trigger()
        self._action(menu, "Set as export range").trigger()
        self._action(menu, "Render still at block end").trigger()
        assert [play.at(0)[0], loop.at(0)[0], export.at(0)[0], still.at(0)[0]] == [2, 2, 2, 2]

    def test_jump_and_reset_signals(self, timeline: TimelineWidget) -> None:
        jump = QSignalSpy(timeline.blockActivated)
        reset = QSignalSpy(timeline.blockResetTimingRequested)
        menu = timeline.context_menu(1)
        self._action(menu, "Jump to code").trigger()
        self._action(menu, "Reset retime").trigger()
        assert jump.at(0)[0] == 1
        assert reset.at(0)[0] == 1

    def test_copy_enables_paste_and_paste_retimes(self, timeline: TimelineWidget) -> None:
        assert self._action(timeline.context_menu(0), "Paste timing").isEnabled() is False
        self._action(timeline.context_menu(0), "Copy timing").trigger()
        assert timeline.timing_clipboard == pytest.approx(1.0)
        menu = timeline.context_menu(2)
        paste = self._action(menu, "Paste timing")
        assert paste.isEnabled() is True
        resized = QSignalSpy(timeline.blockResized)
        paste.trigger()
        assert resized.count() == 1
        assert resized.at(0)[0] == 2
        assert resized.at(0)[1] == pytest.approx(1.0)

    def test_zoom_to_block_action_frames_the_block(self, timeline: TimelineWidget) -> None:
        self._action(timeline.context_menu(2), "Zoom to block").trigger()
        assert timeline.view().span < 4.0

    def test_empty_area_menu_offers_fit(self, timeline: TimelineWidget) -> None:
        assert [a.text() for a in timeline.context_menu(None).actions()] == ["Fit timeline"]

    def test_loop_action_is_checkable(self, timeline: TimelineWidget) -> None:
        action = self._action(timeline.context_menu(1), "Loop this block")
        assert action.isCheckable() is True
        assert action.isChecked() is False

    def test_loop_action_reflects_the_looped_block(self, timeline: TimelineWidget) -> None:
        timeline.set_range(1.0, 2.0)  # block 1 spans [1.0, 2.0]
        timeline.set_looping(True)
        assert self._action(timeline.context_menu(1), "Loop this block").isChecked() is True
        assert self._action(timeline.context_menu(0), "Loop this block").isChecked() is False
        timeline.set_looping(False)
        assert self._action(timeline.context_menu(1), "Loop this block").isChecked() is False


class TestZoomControls:
    def test_zoom_by_scales_the_span(self, timeline: TimelineWidget) -> None:
        before = timeline.view().span
        timeline.zoom_by(2.0)
        assert timeline.view().span < before
        timeline.zoom_by(0.5)
        assert timeline.view().span == pytest.approx(before, abs=0.05)

    def test_fit_selection_frames_the_range(self, timeline: TimelineWidget) -> None:
        timeline.set_range(1.0, 2.0)
        timeline.fit_selection()
        assert timeline.view().span == pytest.approx(1.15, abs=0.05)
        assert timeline.view().start == pytest.approx(1.0 - 0.05 * 1.15, abs=0.05)

    def test_fit_selection_falls_back_to_the_active_block(self, timeline: TimelineWidget) -> None:
        timeline.set_active_block(2)  # block 2 spans [2, 4]
        timeline.fit_selection()
        assert timeline.view().span == pytest.approx(2.0 * 1.15, abs=0.05)

    def test_fit_selection_without_a_selection_fits_all(self, timeline: TimelineWidget) -> None:
        timeline.zoom_to_block(2)
        timeline.fit_selection()
        assert timeline.view().span == pytest.approx(4.0, abs=0.01)


class TestKeyboard:
    def test_arrow_steps_one_frame(self, timeline: TimelineWidget, qtbot) -> None:
        spy = QSignalSpy(timeline.scrubRequested)
        qtbot.keyClick(timeline, Qt.Key.Key_Right)
        assert spy.at(spy.count() - 1)[0] == pytest.approx(FRAME_STEP, abs=1e-6)
        qtbot.keyClick(timeline, Qt.Key.Key_Left)
        assert spy.at(spy.count() - 1)[0] == pytest.approx(0.0, abs=1e-6)

    def test_shift_arrow_steps_one_second(self, timeline: TimelineWidget, qtbot) -> None:
        spy = QSignalSpy(timeline.scrubRequested)
        qtbot.keyClick(timeline, Qt.Key.Key_Right, Qt.KeyboardModifier.ShiftModifier)
        assert spy.at(spy.count() - 1)[0] == pytest.approx(1.0, abs=1e-6)

    def test_home_and_end(self, timeline: TimelineWidget, qtbot) -> None:
        spy = QSignalSpy(timeline.scrubRequested)
        qtbot.keyClick(timeline, Qt.Key.Key_End)
        assert spy.at(spy.count() - 1)[0] == pytest.approx(4.0, abs=0.02)
        qtbot.keyClick(timeline, Qt.Key.Key_Home)
        assert spy.at(spy.count() - 1)[0] == pytest.approx(0.0, abs=0.02)

    def test_space_requests_play_toggle(self, timeline: TimelineWidget, qtbot) -> None:
        spy = QSignalSpy(timeline.playToggleRequested)
        qtbot.keyClick(timeline, Qt.Key.Key_Space)
        assert spy.count() == 1


class TestScrollbar:
    """The bottom scrollbar mirrors and drives the zoomed view window."""

    def test_disabled_and_zero_range_when_not_zoomed(self, timeline: TimelineWidget) -> None:
        bar = timeline.scrollbar
        assert bar.maximum() == 0
        assert bar.isEnabled() is False

    def test_enabled_after_zoom(self, timeline: TimelineWidget) -> None:
        timeline.wheelEvent(_wheel(120, Qt.KeyboardModifier.ControlModifier))
        bar = timeline.scrollbar
        assert bar.maximum() > 0
        assert bar.isEnabled() is True
        # pageStep reflects the visible span in milliseconds.
        assert bar.pageStep() == pytest.approx(timeline.view().span * 1000, abs=2)

    def test_geometry_sits_at_the_bottom(self, timeline: TimelineWidget) -> None:
        bar = timeline.scrollbar
        assert bar.height() == SCROLLBAR_HEIGHT
        assert bar.y() == timeline.height() - SCROLLBAR_HEIGHT
        assert bar.width() == timeline.width()

    def test_dragging_the_scrollbar_pans_the_view(self, timeline: TimelineWidget) -> None:
        timeline.wheelEvent(_wheel(120, Qt.KeyboardModifier.ControlModifier))
        bar = timeline.scrollbar
        viewChanged = QSignalSpy(timeline.viewChanged)
        bar.setValue(0)
        assert timeline.view().start == pytest.approx(0.0, abs=1e-6)
        bar.setValue(bar.maximum())
        assert timeline.view().start == pytest.approx(bar.maximum() / 1000.0, abs=1e-6)
        assert viewChanged.count() >= 1

    def test_panning_updates_the_scrollbar_value(self, timeline: TimelineWidget) -> None:
        timeline.wheelEvent(_wheel(120, Qt.KeyboardModifier.ControlModifier))
        bar = timeline.scrollbar
        before = bar.value()
        timeline.wheelEvent(_wheel(-120))  # plain wheel pans right
        assert timeline.view().start * 1000 > before
        assert bar.value() == pytest.approx(timeline.view().start * 1000, abs=2)

    def test_fit_resets_the_scrollbar(self, timeline: TimelineWidget) -> None:
        timeline.wheelEvent(_wheel(120, Qt.KeyboardModifier.ControlModifier))
        assert timeline.scrollbar.maximum() > 0
        timeline.fit()
        assert timeline.scrollbar.maximum() == 0
        assert timeline.scrollbar.isEnabled() is False

    def test_zoom_to_block_enables_the_scrollbar(self, timeline: TimelineWidget) -> None:
        timeline.zoom_to_block(2)
        assert timeline.scrollbar.maximum() > 0

    def test_sync_does_not_echo_into_extra_signals(self, timeline: TimelineWidget) -> None:
        # Synchronising the bar must not look like a user pan.
        spy = QSignalSpy(timeline.viewChanged)
        timeline.wheelEvent(_wheel(120, Qt.KeyboardModifier.ControlModifier))
        before = spy.count()
        timeline._sync_scrollbar()
        assert spy.count() == before


def _wheel(delta: int, modifiers: Qt.KeyboardModifier = Qt.KeyboardModifier.NoModifier):
    from PySide6.QtGui import QWheelEvent

    return QWheelEvent(
        QPoint(300, 60).toPointF(),
        QPoint(300, 60).toPointF(),
        QPoint(0, 0),
        QPoint(0, delta),
        Qt.MouseButton.NoButton,
        modifiers,
        Qt.ScrollPhase.NoScrollPhase,
        False,
    )


class TestLanes:
    """One lane row per animated mobject, drawn under the block strip."""

    @staticmethod
    def _clips():  # type: ignore[no-untyped-def]
        from manim_studio.tracer import ClipTrace

        return [
            [
                ClipTrace("ShowCreation", "Circle", 0, 0.0, 1.0),
                ClipTrace("FadeIn", "Square", 1, 0.0, 1.0),
            ],
            [],
            [ClipTrace("Rotate", "Circle", 0, 0.0, 2.0)],
        ]

    def test_no_clips_means_no_lanes_and_the_old_height(self, timeline: TimelineWidget) -> None:
        assert timeline.lane_count() == 0
        from manim_studio.tracer import ClipTrace

        base = timeline.minimumHeight()
        timeline.set_clips(self._clips())
        assert timeline.lane_count() == 2  # two thin rows still fit the base height
        timeline.set_clips([[ClipTrace("A", "M", lane, 0.0, 1.0) for lane in range(5)]])
        assert timeline.lane_count() == 5
        assert timeline.minimumHeight() > base  # room for the lane rows
        timeline.set_clips([])
        assert timeline.minimumHeight() == base

    def test_lanes_are_capped(self, timeline: TimelineWidget) -> None:
        from manim_studio.tracer import ClipTrace

        timeline.set_clips([[ClipTrace("A", "M", lane, 0.0, 1.0) for lane in range(20)]])
        assert timeline.lane_count() == 6

    def test_clip_at_finds_the_bar_under_a_point(self, timeline: TimelineWidget) -> None:
        timeline.resize(800, 260)
        timeline.set_clips(self._clips())
        view = timeline.view()
        top = timeline._lanes_top()
        # block 0 spans 0-1s: lane 0 holds ShowCreation, lane 1 holds FadeIn
        x = view.time_to_x(0.5)
        first = timeline.clip_at(x, top + 2)
        second = timeline.clip_at(x, top + 15 + 2)
        assert first is not None and first[1].animation == "ShowCreation" and first[0].index == 0
        assert second is not None and second[1].animation == "FadeIn"
        assert timeline.clip_at(x, 5) is None  # ruler, not a lane
        assert timeline.clip_at(view.time_to_x(1.2), top + 2) is None  # the wait block has none

    def test_painting_with_lanes_does_not_raise(self, timeline: TimelineWidget) -> None:
        timeline.resize(800, 260)
        timeline.set_clips(self._clips())
        timeline.grab()  # exercises paintEvent and _draw_lanes


class TestScrollAfterStretch:
    """Lengthening a section must keep later blocks reachable (scrollbar + drag)."""

    @staticmethod
    def _stretched(timeline: TimelineWidget, extra: float) -> TimelineLog:
        first = timeline.log.blocks[0]
        return timeline.log.scaled_block_duration(first.index, first.duration + extra)

    def test_stretching_keeps_the_zoom_and_enables_the_scrollbar(
        self, timeline: TimelineWidget
    ) -> None:
        span_before = timeline.view().span
        assert timeline.scrollbar.isEnabled() is False  # everything fits
        timeline.set_log(self._stretched(timeline, 5.0), keep_view=True)
        view = timeline.view()
        assert view.span == pytest.approx(span_before)  # the zoom did not silently change
        assert view.total == pytest.approx(timeline.log.total_duration)
        assert timeline.scrollbar.isEnabled() is True
        assert timeline.scrollbar.maximum() > 0

    def test_the_scrollbar_reaches_the_pushed_out_blocks(self, timeline: TimelineWidget) -> None:
        timeline.set_log(self._stretched(timeline, 5.0), keep_view=True)
        last = timeline.log.blocks[-1]
        assert timeline.view().end < last.end  # off-screen to the right
        timeline.scrollbar.setValue(timeline.scrollbar.maximum())
        assert timeline.view().end == pytest.approx(timeline.log.total_duration, abs=1e-3)
        assert timeline.view().time_to_x(last.end) <= timeline.width() + 1

    def test_shrinking_clamps_the_window(self, timeline: TimelineWidget) -> None:
        timeline.set_log(self._stretched(timeline, 5.0), keep_view=True)
        timeline.scrollbar.setValue(timeline.scrollbar.maximum())
        timeline.set_log(make_log(), keep_view=True)
        assert timeline.view().end <= timeline.log.total_duration + 1e-6
        assert timeline.view().start >= 0.0

    def test_dragging_a_block_edge_past_the_end_is_possible_and_scrollable(
        self, timeline: TimelineWidget, qtbot
    ) -> None:
        timeline.set_snapping(False)
        last = timeline.log.blocks[-1]
        view = timeline.view()
        edge_x = int(view.time_to_x(last.end))
        qtbot.mousePress(timeline, Qt.MouseButton.LeftButton, pos=QPoint(edge_x - 1, 40))
        assert timeline._mode == "resize"
        # Far past the right edge of the widget: the old clamp would have capped this.
        qtbot.mouseMove(timeline, QPoint(timeline.width() + 400, 40))
        assert timeline._resize_duration is not None
        assert timeline._resize_duration > last.duration + 1.0
        assert timeline.scrollbar.maximum() > 0  # the grown timeline is scrollable
        qtbot.mouseRelease(
            timeline, Qt.MouseButton.LeftButton, pos=QPoint(timeline.width() + 400, 40)
        )

    def test_holding_the_edge_near_the_window_edge_autoscrolls(
        self, timeline: TimelineWidget, qtbot
    ) -> None:
        timeline.set_snapping(False)
        timeline.set_log(self._stretched(timeline, 8.0), keep_view=True)
        first_end = timeline.log.blocks[0].end
        view = timeline.view()
        qtbot.mousePress(
            timeline, Qt.MouseButton.LeftButton, pos=QPoint(int(view.time_to_x(first_end)) - 1, 40)
        )
        assert timeline._mode == "resize"
        qtbot.mouseMove(timeline, QPoint(timeline.width() - 4, 40))
        start_before = timeline.view().start
        timeline._autoscroll_tick()
        assert timeline.view().start > start_before
        qtbot.mouseRelease(
            timeline, Qt.MouseButton.LeftButton, pos=QPoint(timeline.width() - 4, 40)
        )
        assert timeline._autoscroll.isActive() is False
