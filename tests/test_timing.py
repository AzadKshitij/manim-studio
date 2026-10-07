"""Unit tests for the pure timing-override model."""

from __future__ import annotations

import pytest

from manim_studio.timeline_model import TimelineBlock, TimelineError, TimelineLog
from manim_studio.timing import TimingOverrides


def make_log(*specs: tuple[str, float, float]) -> TimelineLog:
    return TimelineLog.from_iterable(
        TimelineBlock(index=i, name=name, start=start, duration=duration)
        for i, (name, start, duration) in enumerate(specs)
    )


@pytest.fixture
def base() -> TimelineLog:
    return make_log(("A", 0.0, 1.0), ("B", 1.0, 2.0), ("C", 3.0, 0.5))


class TestSetDuration:
    def test_changing_a_duration_shifts_later_blocks(self, base: TimelineLog) -> None:
        overrides = TimingOverrides(base)
        assert overrides.set_duration(1, 4.0) is True
        assert [(b.start, b.duration) for b in overrides.log().blocks] == [
            (0.0, 1.0),
            (1.0, 4.0),
            (5.0, 0.5),
        ]

    def test_setting_the_same_value_is_a_no_op(self, base: TimelineLog) -> None:
        overrides = TimingOverrides(base)
        assert overrides.set_duration(0, 1.0) is False
        assert overrides.is_empty
        assert overrides.can_undo is False

    def test_durations_returns_a_copy(self, base: TimelineLog) -> None:
        overrides = TimingOverrides(base)
        overrides.set_duration(0, 2.0)
        snapshot = overrides.durations
        overrides.set_duration(1, 3.0)
        assert dict(snapshot) == {0: 2.0}

    def test_unknown_index_raises(self, base: TimelineLog) -> None:
        with pytest.raises(TimelineError, match="no block"):
            TimingOverrides(base).set_duration(7, 1.0)

    @pytest.mark.parametrize("duration", [-1.0, float("nan"), float("inf")])
    def test_invalid_duration_raises(self, base: TimelineLog, duration: float) -> None:
        with pytest.raises(TimelineError):
            TimingOverrides(base).set_duration(0, duration)


class TestReset:
    def test_reset_block_drops_one_override(self, base: TimelineLog) -> None:
        overrides = TimingOverrides(base)
        overrides.set_duration(0, 2.0)
        overrides.set_duration(1, 3.0)
        assert overrides.reset_block(0) is True
        assert overrides.durations == {1: 3.0}

    def test_reset_block_without_override(self, base: TimelineLog) -> None:
        overrides = TimingOverrides(base)
        assert overrides.reset_block(0) is False
        assert overrides.can_undo is False

    def test_reset_clears_everything(self, base: TimelineLog) -> None:
        overrides = TimingOverrides(base)
        overrides.set_duration(0, 2.0)
        overrides.set_duration(1, 3.0)
        assert overrides.reset() is True
        assert overrides.is_empty
        assert overrides.log() == base

    def test_reset_when_empty(self, base: TimelineLog) -> None:
        assert TimingOverrides(base).reset() is False


class TestUndoRedo:
    def test_undo_and_redo_restore_history(self, base: TimelineLog) -> None:
        overrides = TimingOverrides(base)
        overrides.set_duration(0, 2.0)
        overrides.set_duration(1, 3.0)
        assert overrides.undo() is True
        assert overrides.durations == {0: 2.0}
        assert overrides.can_redo is True
        assert overrides.redo() is True
        assert overrides.durations == {0: 2.0, 1: 3.0}

    def test_a_new_edit_clears_the_redo_stack(self, base: TimelineLog) -> None:
        overrides = TimingOverrides(base)
        overrides.set_duration(0, 2.0)
        overrides.undo()
        assert overrides.can_redo is True
        overrides.set_duration(1, 3.0)
        assert overrides.can_redo is False

    def test_undo_without_history(self, base: TimelineLog) -> None:
        overrides = TimingOverrides(base)
        assert overrides.undo() is False
        assert overrides.redo() is False

    def test_no_op_edit_does_not_create_history(self, base: TimelineLog) -> None:
        overrides = TimingOverrides(base)
        overrides.set_duration(0, 1.0)  # same as base
        assert overrides.undo() is False


class TestLog:
    def test_overrides_apply_in_ascending_order(self, base: TimelineLog) -> None:
        overrides = TimingOverrides(base)
        overrides.set_duration(1, 3.0)  # B: 1.0 -> 4.0
        overrides.set_duration(2, 1.0)  # C: 4.0 -> 5.0
        assert [(b.start, b.duration) for b in overrides.log().blocks] == [
            (0.0, 1.0),
            (1.0, 3.0),
            (4.0, 1.0),
        ]

    def test_base_is_untouched(self, base: TimelineLog) -> None:
        overrides = TimingOverrides(base)
        overrides.set_duration(0, 9.0)
        assert overrides.base == base
        assert overrides.log() is not base


class TestPayload:
    def test_payload_is_sorted_by_index(self, base: TimelineLog) -> None:
        overrides = TimingOverrides(base)
        overrides.set_duration(2, 1.5)
        overrides.set_duration(0, 2.5)
        assert overrides.to_payload() == [
            {"index": 0, "duration": 2.5},
            {"index": 2, "duration": 1.5},
        ]

    def test_empty_payload(self, base: TimelineLog) -> None:
        assert TimingOverrides(base).to_payload() == []
