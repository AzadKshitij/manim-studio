"""contact_sheet: sample times, block lookup and grid geometry (pure logic, no GL, no PIL)."""

from __future__ import annotations

import math
from itertools import pairwise

import pytest

from manim_studio.contact_sheet import (
    SheetError,
    SheetLayout,
    block_at_time,
    format_label,
    normalize_times,
    parse_times,
    plan_sheet,
    sample_times,
    split_times,
    truncate_label,
)
from manim_studio.timeline_model import TimelineBlock


def _blocks(*spans: tuple[str, float, float]) -> list[TimelineBlock]:
    """(name, start, duration) triples -> blocks with consecutive indices."""
    return [
        TimelineBlock(index=i, name=name, start=start, duration=duration)
        for i, (name, start, duration) in enumerate(spans)
    ]


class TestSampleTimes:
    def test_even_spacing_ends_on_the_total(self) -> None:
        sampling = sample_times(2.0, 0.5)
        assert sampling.times == (0.0, 0.5, 1.0, 1.5, 2.0)
        assert sampling.step == 0.5
        assert sampling.thinned is False

    def test_short_final_step_is_kept_as_the_end(self) -> None:
        assert sample_times(1.2, 0.5).times == (0.0, 0.5, 1.0, 1.2)

    def test_zero_length_scene_gives_one_sample(self) -> None:
        assert sample_times(0.0, 0.5).times == (0.0,)

    def test_end_on_a_multiple_is_not_duplicated(self) -> None:
        assert sample_times(1.0, 0.5).times == (0.0, 0.5, 1.0)

    def test_start_and_end_restrict_the_range(self) -> None:
        assert sample_times(4.0, 1.0, start=1.0, end=3.0).times == (1.0, 2.0, 3.0)

    def test_max_frames_thins_and_keeps_first_and_last(self) -> None:
        sampling = sample_times(10.0, 0.1, max_frames=5)
        assert len(sampling.times) == 5
        assert sampling.times[0] == 0.0
        assert sampling.times[-1] == 10.0
        assert sampling.thinned is True
        assert sampling.step >= 0.1

    def test_thinned_step_is_the_spacing_actually_used(self) -> None:
        sampling = sample_times(10.0, 0.1, max_frames=5)
        assert sampling.step == pytest.approx(2.5)
        assert sampling.times == (0.0, 2.5, 5.0, 7.5, 10.0)

    def test_times_are_strictly_ascending_and_unique(self) -> None:
        for every in (0.07, 0.3, 1.0):
            times = sample_times(3.3, every).times
            assert all(a < b for a, b in pairwise(times))

    def test_range_is_clamped_into_the_scene(self) -> None:
        sampling = sample_times(2.0, 0.5, start=-5.0, end=50.0)
        assert sampling.times[0] == 0.0
        assert sampling.times[-1] == 2.0

    @pytest.mark.parametrize("every", [0.0, -1.0, math.nan, math.inf])
    def test_bad_spacing_raises(self, every: float) -> None:
        with pytest.raises(SheetError):
            sample_times(2.0, every)

    def test_start_after_end_raises(self) -> None:
        with pytest.raises(SheetError):
            sample_times(4.0, 1.0, start=3.0, end=1.0)

    def test_max_frames_below_two_raises(self) -> None:
        with pytest.raises(SheetError):
            sample_times(4.0, 1.0, max_frames=1)

    def test_non_finite_total_raises(self) -> None:
        with pytest.raises(SheetError):
            sample_times(math.nan, 0.5)


class TestParseTimes:
    def test_splits_strips_and_sorts(self) -> None:
        assert parse_times("3, 0 ,1.5", 10.0) == (0.0, 1.5, 3.0)

    def test_clamps_into_the_scene(self) -> None:
        assert parse_times("-1,99", 2.0) == (0.0, 2.0)

    def test_removes_duplicates(self) -> None:
        assert parse_times("1, 1.0, 1.000000", 5.0) == (1.0,)

    @pytest.mark.parametrize("text", ["", "   ", "abc", "1,,2", "nan", "1,inf"])
    def test_rejects_empty_and_non_numbers(self, text: str) -> None:
        with pytest.raises(SheetError):
            parse_times(text, 5.0)


class TestSplitAndNormalizeTimes:
    def test_split_keeps_order_and_does_not_clamp(self) -> None:
        assert split_times("3, -1 ,99") == (3.0, -1.0, 99.0)

    @pytest.mark.parametrize("text", ["", "  ", "abc", "1,,2", "nan", "1,inf"])
    def test_split_rejects_bad_input(self, text: str) -> None:
        with pytest.raises(SheetError):
            split_times(text)

    def test_normalize_clamps_sorts_and_drops_duplicates(self) -> None:
        assert normalize_times([2.0, 0.0, 99.0, 2.0], 2.0) == (0.0, 2.0)

    def test_normalize_rejects_an_empty_list(self) -> None:
        with pytest.raises(SheetError):
            normalize_times([], 2.0)


class TestBlockAtTime:
    BLOCKS = _blocks(("A", 0.0, 1.0), ("B", 1.0, 1.0), ("C", 2.0, 1.0))

    def test_inside_each_block(self) -> None:
        assert block_at_time(self.BLOCKS, 0.0) is self.BLOCKS[0]

    def test_boundary_belongs_to_the_next_block(self) -> None:
        assert block_at_time(self.BLOCKS, 1.0) is self.BLOCKS[1]

    def test_just_before_the_end_is_the_last_block(self) -> None:
        assert block_at_time(self.BLOCKS, 2.999) is self.BLOCKS[2]

    def test_end_of_trace_clamps_to_the_last_block(self) -> None:
        assert block_at_time(self.BLOCKS, 3.0) is self.BLOCKS[2]

    def test_before_the_trace_clamps_to_the_first_block(self) -> None:
        assert block_at_time(self.BLOCKS, -1.0) is self.BLOCKS[0]

    def test_empty_trace_has_no_block(self) -> None:
        assert block_at_time([], 1.0) is None


class TestLabels:
    def test_truncate_keeps_short_text(self) -> None:
        assert truncate_label("abc", 8) == "abc"

    def test_truncate_uses_ascii_dots(self) -> None:
        assert truncate_label("0123456789", 8) == "01234..."

    def test_truncate_tiny_budget_has_no_dots(self) -> None:
        assert truncate_label("0123456789", 3) == "012"

    def test_truncate_rejects_negative_budget(self) -> None:
        with pytest.raises(SheetError):
            truncate_label("abc", -1)

    def test_format_without_block(self) -> None:
        assert format_label(1.5, None) == "t=1.50s"

    def test_format_with_block(self) -> None:
        block = TimelineBlock(index=3, name="Create", start=1.0, duration=0.5)
        assert format_label(1.5, block) == "t=1.50s #3 Create"


class TestPlanSheet:
    BLOCKS = _blocks(("Create", 0.0, 1.0), ("Wait", 1.0, 1.0), ("Fade", 2.0, 1.0))

    @staticmethod
    def _rects(layout: SheetLayout) -> list[tuple[int, int, int, int]]:
        return [(c.x, c.y, c.width, c.height) for c in layout.cells]

    def test_single_frame_is_one_cell(self) -> None:
        layout = plan_sheet([0.5], self.BLOCKS, (640, 360))
        assert (layout.columns, layout.rows) == (1, 1)
        assert len(layout.cells) == 1

    def test_five_frames_use_three_columns_and_two_rows(self) -> None:
        layout = plan_sheet([0.0, 0.5, 1.0, 1.5, 2.0], self.BLOCKS, (640, 360))
        assert (layout.columns, layout.rows) == (3, 2)

    def test_defaults_match_the_square_rule(self) -> None:
        for count, expected in [(1, 1), (2, 2), (4, 2), (5, 3), (9, 3), (20, 5)]:
            layout = plan_sheet([float(i) for i in range(count)], self.BLOCKS, (640, 360))
            assert layout.columns == expected

    def test_every_cell_is_inside_the_sheet(self) -> None:
        layout = plan_sheet([0.0, 0.5, 1.0, 1.5, 2.0], self.BLOCKS, (640, 360))
        for cell in layout.cells:
            assert cell.x >= 0 and cell.y >= 0
            assert cell.x + cell.width <= layout.width
            assert cell.label_y + layout.label_height <= layout.height

    def test_image_rects_never_overlap(self) -> None:
        layout = plan_sheet([float(i) * 0.5 for i in range(7)], self.BLOCKS, (640, 360))
        rects = self._rects(layout)
        for i, (ax, ay, aw, ah) in enumerate(rects):
            for bx, by, bw, bh in rects[i + 1 :]:
                disjoint = ax + aw <= bx or bx + bw <= ax or ay + ah <= by or by + bh <= ay
                assert disjoint

    def test_label_strip_sits_under_each_image(self) -> None:
        layout = plan_sheet([0.0, 1.5], self.BLOCKS, (640, 360))
        for cell in layout.cells:
            assert cell.label_y == cell.y + cell.height

    def test_cells_never_scale_up(self) -> None:
        layout = plan_sheet([0.0], self.BLOCKS, (320, 180))
        assert layout.cell_width <= 320
        assert layout.cell_height == round(layout.cell_width * 180 / 320)

    def test_explicit_columns_clamp_to_the_frame_count(self) -> None:
        layout = plan_sheet([0.0, 1.0], self.BLOCKS, (640, 360), columns=99)
        assert layout.columns == 2
        assert layout.rows == 1

    def test_sheet_size_matches_the_formula(self) -> None:
        layout = plan_sheet([0.0, 0.5, 1.0, 1.5], self.BLOCKS, (640, 360), columns=2)
        expected_width = 2 * 8 + 2 * layout.cell_width + 1 * 8
        expected_height = 2 * 8 + 2 * (layout.cell_height + 22) + 1 * 8
        assert (layout.width, layout.height) == (expected_width, expected_height)

    def test_cells_carry_block_and_label(self) -> None:
        layout = plan_sheet([0.5, 1.5, 2.5], self.BLOCKS, (640, 360))
        assert [c.block_name for c in layout.cells] == ["Create", "Wait", "Fade"]
        assert [c.block_index for c in layout.cells] == [0, 1, 2]
        assert layout.cells[1].label == "t=1.50s #1 Wait"

    def test_labels_respect_the_budget(self) -> None:
        layout = plan_sheet([0.0, 1.0, 2.0], self.BLOCKS, (640, 360), max_width=400)
        budget = max(8, layout.cell_width // 8)
        assert all(len(c.label) <= budget for c in layout.cells)

    def test_no_blocks_leaves_the_block_fields_empty(self) -> None:
        layout = plan_sheet([0.0], [], (640, 360))
        assert layout.cells[0].block_index is None
        assert layout.cells[0].block_name == ""
        assert layout.cells[0].label == "t=0.00s"

    def test_empty_times_raises(self) -> None:
        with pytest.raises(SheetError):
            plan_sheet([], self.BLOCKS, (640, 360))

    @pytest.mark.parametrize("frame", [(0, 360), (640, 0), (-1, 10)])
    def test_non_positive_frame_raises(self, frame: tuple[int, int]) -> None:
        with pytest.raises(SheetError):
            plan_sheet([0.0], self.BLOCKS, frame)
