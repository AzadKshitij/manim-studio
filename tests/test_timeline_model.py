"""Unit tests for the pure timeline model."""

from __future__ import annotations

import json

import pytest

from manim_studio.timeline_model import (
    TimelineBlock,
    TimelineError,
    TimelineLog,
    TimelineView,
    block_for_line,
    dump_log,
    format_duration,
    load_log,
    parse_call_lines,
    parse_log,
    save_log,
    snap_index,
)


def make_log(*specs: tuple[str, float, float]) -> TimelineLog:
    return TimelineLog.from_iterable(
        TimelineBlock(index=i, name=name, start=start, duration=duration)
        for i, (name, start, duration) in enumerate(specs)
    )


class TestTimelineBlock:
    def test_to_dict_shape_matches_spec(self) -> None:
        block = TimelineBlock(index=2, name="ShowCreation", start=1.5, duration=0.75)
        assert block.to_dict() == {
            "name": "ShowCreation",
            "start": 1.5,
            "duration": 0.75,
            "index": 2,
        }

    def test_from_dict_round_trip(self) -> None:
        block = TimelineBlock(index=0, name="Wait", start=0.0, duration=1.0)
        assert TimelineBlock.from_dict(block.to_dict()) == block

    @pytest.mark.parametrize(
        "kwargs",
        [
            {"index": -1, "name": "X", "start": 0, "duration": 1},
            {"index": 0, "name": "", "start": 0, "duration": 1},
            {"index": 0, "name": "  ", "start": 0, "duration": 1},
            {"index": 0, "name": "X", "start": -0.5, "duration": 1},
            {"index": 0, "name": "X", "start": float("nan"), "duration": 1},
            {"index": 0, "name": "X", "start": 0, "duration": -1},
            {"index": 0, "name": "X", "start": 0, "duration": float("inf")},
        ],
    )
    def test_invalid_blocks_rejected(self, kwargs: dict) -> None:
        with pytest.raises(TimelineError):
            TimelineBlock(**kwargs)

    @pytest.mark.parametrize(
        "data",
        [
            "not-a-dict",
            [],
            {},
            {"name": "X", "start": 0, "duration": 1},  # missing index
            {"name": 3, "start": 0, "duration": 1, "index": 0},
            {"name": "X", "start": "zero", "duration": 1, "index": 0},
        ],
    )
    def test_from_dict_rejects_bad_data(self, data: object) -> None:
        with pytest.raises(TimelineError):
            TimelineBlock.from_dict(data)

    def test_end_property(self) -> None:
        assert TimelineBlock(0, "X", 2.0, 0.5).end == 2.5


class TestTimelineLog:
    def test_empty_log(self) -> None:
        log = TimelineLog(())
        assert log.is_empty and log.total_duration == 0.0
        assert log.block_at(5.0) is None and log.index_at(5.0) is None

    def test_total_duration_uses_last_end(self) -> None:
        log = make_log(("A", 0.0, 1.0), ("B", 1.0, 2.5))
        assert log.total_duration == 3.5
        assert log.num_blocks == 2

    def test_duplicate_indexes_rejected(self) -> None:
        with pytest.raises(TimelineError, match="duplicate"):
            TimelineLog((TimelineBlock(1, "A", 0.0, 1.0), TimelineBlock(1, "B", 1.0, 1.0)))

    def test_out_of_order_rejected(self) -> None:
        with pytest.raises(TimelineError, match="ordered"):
            TimelineLog((TimelineBlock(2, "A", 0.0, 1.0), TimelineBlock(1, "B", 1.0, 1.0)))

    def test_block_at_boundaries(self) -> None:
        log = make_log(("A", 0.0, 1.0), ("B", 1.0, 1.0), ("C", 2.0, 0.5))
        assert log.index_at(-1.0) == 0  # clamp left
        assert log.index_at(0.0) == 0
        assert log.index_at(0.999) == 0
        assert log.index_at(1.0) == 1  # half-open interval
        assert log.index_at(2.2) == 2
        assert log.index_at(99.0) == 2  # clamp right

    def test_block_by_index(self) -> None:
        log = make_log(("A", 0.0, 1.0))
        assert log.block_by_index(0) is not None
        assert log.block_by_index(5) is None

    def test_snap_index_matches_log(self) -> None:
        log = make_log(("A", 0.0, 1.0), ("B", 1.0, 1.0))
        assert snap_index(log, 1.2) == 1

    def test_scaled_block_duration_extends_and_shifts_later_blocks(self) -> None:
        log = make_log(("A", 0.0, 1.0), ("B", 1.0, 2.0), ("C", 3.0, 0.5))
        edited = log.scaled_block_duration(1, 3.0)
        assert [(b.index, b.start, b.duration) for b in edited.blocks] == [
            (0, 0.0, 1.0),
            (1, 1.0, 3.0),
            (2, 4.0, 0.5),
        ]
        assert edited.total_duration == 4.5

    def test_scaled_block_duration_shortens_and_pulls_later_blocks(self) -> None:
        log = make_log(("A", 0.0, 1.0), ("B", 1.0, 2.0), ("C", 3.0, 0.5))
        edited = log.scaled_block_duration(0, 0.25)
        assert [(b.start, b.duration) for b in edited.blocks] == [
            (0.0, 0.25),
            (0.25, 2.0),
            (2.25, 0.5),
        ]

    def test_scaled_block_factor(self) -> None:
        log = make_log(("A", 0.0, 2.0), ("B", 2.0, 1.0))
        edited = log.scaled_block(0, 2.5)
        assert edited.blocks[0].duration == 5.0
        assert edited.blocks[1].start == 5.0

    @pytest.mark.parametrize("duration", [-0.1, float("nan"), float("inf")])
    def test_scaled_block_duration_rejects_bad_values(self, duration: float) -> None:
        log = make_log(("A", 0.0, 1.0))
        with pytest.raises(TimelineError):
            log.scaled_block_duration(0, duration)

    def test_scaled_block_unknown_index(self) -> None:
        log = make_log(("A", 0.0, 1.0))
        with pytest.raises(TimelineError, match="no block"):
            log.scaled_block(9, 2.0)
        with pytest.raises(TimelineError, match="no block"):
            log.scaled_block_duration(9, 2.0)

    @pytest.mark.parametrize("factor", [0.0, -1.0, float("nan")])
    def test_scaled_block_rejects_bad_factor(self, factor: float) -> None:
        log = make_log(("A", 0.0, 1.0))
        with pytest.raises(TimelineError):
            log.scaled_block(0, factor)

    def test_scaled_block_duration_is_pure(self) -> None:
        log = make_log(("A", 0.0, 1.0), ("B", 1.0, 1.0))
        log.scaled_block_duration(0, 5.0)
        assert log.blocks[0].duration == 1.0  # original untouched


class TestSerialization:
    def test_dump_log_is_bare_list(self) -> None:
        blocks = [TimelineBlock(0, "Wait", 0.0, 1.0)]
        assert dump_log(blocks) == [{"name": "Wait", "start": 0.0, "duration": 1.0, "index": 0}]

    def test_parse_bare_list(self) -> None:
        log = parse_log([{"name": "Wait", "start": 0.0, "duration": 1.0, "index": 0}])
        assert log.num_blocks == 1 and log.blocks[0].name == "Wait"

    def test_parse_versioned_object(self) -> None:
        log = parse_log(
            {"version": 1, "blocks": [{"name": "Wait", "start": 0.0, "duration": 1.0, "index": 0}]}
        )
        assert log.num_blocks == 1

    @pytest.mark.parametrize("payload", [42, "nope", {"no_blocks": []}, {"blocks": 3}])
    def test_parse_rejects_bad_payloads(self, payload: object) -> None:
        with pytest.raises(TimelineError):
            parse_log(payload)

    def test_save_and_load_round_trip(self, tmp_path) -> None:
        log = make_log(("ShowCreation", 0.0, 0.5), ("Wait", 0.5, 1.0))
        path = tmp_path / "log.json"
        save_log(path, log)
        assert json.loads(path.read_text(encoding="utf-8")) == log.to_dicts()
        assert load_log(path) == log

    def test_load_reports_invalid_json(self, tmp_path) -> None:
        path = tmp_path / "log.json"
        path.write_text("{nope", encoding="utf-8")
        with pytest.raises(TimelineError, match="invalid JSON"):
            load_log(path)


class TestFormatDuration:
    @pytest.mark.parametrize(
        ("seconds", "expected"),
        [(0.0, "0.0s"), (3.25, "3.2s"), (59.9, "59.9s"), (60.0, "1:00.0"), (125.5, "2:05.5")],
    )
    def test_format(self, seconds: float, expected: str) -> None:
        assert format_duration(seconds) == expected

    def test_negative_and_nan_clamp_to_zero(self) -> None:
        assert format_duration(-5) == "0.0s"
        assert format_duration(float("nan")) == "0.0s"


class TestTimelineView:
    def test_defaults_fit_whole_trace(self) -> None:
        view = TimelineView(total=10.0, width=1000.0)
        assert view.start == 0.0 and view.span == 10.0
        assert view.time_to_x(5.0) == pytest.approx(500.0)
        assert view.x_to_time(500.0) == pytest.approx(5.0)
        assert not view.is_zoomed

    def test_x_to_time_clamps(self) -> None:
        view = TimelineView(total=10.0, width=100.0)
        assert view.x_to_time(-50.0) == 0.0
        assert view.x_to_time(10_000.0) == 10.0

    def test_zoom_keeps_anchor_time_fixed(self) -> None:
        view = TimelineView(total=10.0, width=1000.0)
        zoomed = view.zoom_at(250.0, 4.0)
        assert zoomed.span == pytest.approx(2.5)
        assert zoomed.time_to_x(2.5) == pytest.approx(250.0, abs=0.5)
        assert zoomed.is_zoomed

    def test_zoom_respects_min_and_max_span(self) -> None:
        view = TimelineView(total=10.0, width=1000.0)
        assert view.zoom_at(0.0, 1e9, min_span=0.2).span == pytest.approx(0.2)
        assert view.zoom_at(0.0, 1e9).span == pytest.approx(0.05)  # default min_span
        assert view.zoom_at(0.0, 0.001).span == pytest.approx(10.0)  # cannot zoom out past fit

    def test_zoom_invalid_factor(self) -> None:
        with pytest.raises(TimelineError):
            TimelineView(total=1.0, width=10.0).zoom_at(0.0, 0.0)

    def test_pan_clamps_to_bounds(self) -> None:
        view = TimelineView(total=10.0, width=1000.0, span=2.0, start=1.0)
        assert view.pan_px(-10_000.0).start == 0.0
        assert view.pan_px(10_000.0).start == pytest.approx(8.0)  # total - span

    def test_fit_resets(self) -> None:
        view = TimelineView(total=10.0, width=1000.0, span=2.0, start=4.0).fit()
        assert view.start == 0.0 and view.span == pytest.approx(10.0)

    def test_zero_duration_trace_is_stable(self) -> None:
        view = TimelineView(total=0.0, width=100.0)
        assert view.span is not None and view.span > 0
        assert 0.0 <= view.x_to_time(50.0) <= 1e-6


class TestParseCallLines:
    def test_integers_survive_and_blanks_become_none(self) -> None:
        assert parse_call_lines([12, None, 7]) == (12, None, 7)

    def test_non_list_is_empty(self) -> None:
        assert parse_call_lines("nope") == ()
        assert parse_call_lines(None) == ()

    def test_bools_and_floats_are_rejected(self) -> None:
        assert parse_call_lines([True, 3.5, "4"]) == (None, None, None)


class TestBlockForLine:
    LINES = (4, None, 9, 15)

    def test_caret_on_a_call_site(self) -> None:
        assert block_for_line(self.LINES, 4) == 0
        assert block_for_line(self.LINES, 9) == 2
        assert block_for_line(self.LINES, 15) == 3

    def test_caret_between_calls_belongs_to_the_earlier_block(self) -> None:
        assert block_for_line(self.LINES, 8) == 0
        assert block_for_line(self.LINES, 100) == 3

    def test_caret_before_the_first_call_has_no_block(self) -> None:
        assert block_for_line(self.LINES, 3) is None

    def test_blocks_without_call_sites_are_skipped(self) -> None:
        assert block_for_line((None, None), 5) is None
        assert block_for_line((None, 6, None), 6) == 1

    def test_empty(self) -> None:
        assert block_for_line((), 1) is None
