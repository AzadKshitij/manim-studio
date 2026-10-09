"""Unit tests for the pure benchmark helpers."""

from __future__ import annotations

import json
from pathlib import Path

from manim_studio import benchmark


def _frame(width: int = 32, height: int = 24) -> bytes:
    data = bytearray()
    for y in range(height):
        for x in range(width):
            data += bytes(((x * 8) % 256, (y * 8) % 256, 51, 255))
    return bytes(data)


class TestMedianSeconds:
    def test_runs_warmup_plus_repeats(self) -> None:
        calls = 0

        def operation() -> None:
            nonlocal calls
            calls += 1

        benchmark.median_seconds(operation, repeats=5, warmup=2)
        assert calls == 7

    def test_zero_warmup_is_allowed(self) -> None:
        calls = 0

        def operation() -> None:
            nonlocal calls
            calls += 1

        benchmark.median_seconds(operation, repeats=1, warmup=0)
        assert calls == 1


class TestCodecCandidates:
    def test_always_includes_zlib_levels(self) -> None:
        names = [name for name, _, _ in benchmark.codec_candidates()]
        assert "zlib-1" in names
        assert "zlib-6" in names


class TestBenchCodecs:
    def test_reports_ratio_and_round_trips(self) -> None:
        rgba = _frame()
        results = benchmark.bench_codecs(rgba, repeats=3, warmup=0)
        assert "zlib-1" in results
        entry = results["zlib-1"]
        assert entry["encode_ms"] >= 0
        assert entry["decode_ms"] >= 0
        assert entry["payload_bytes"] > 0
        assert entry["ratio"] > 1.0


class TestBenchParser:
    def test_round_trip_reports_payload(self) -> None:
        rgba = _frame(16, 16)
        result = benchmark.bench_parser(16, 16, rgba, repeats=3, warmup=0, chunk=64)
        assert result["payload_bytes"] > 0
        assert result["round_trip_ms"] >= 0


class TestMachineInfo:
    def test_has_stable_keys(self) -> None:
        info = benchmark.machine_info()
        assert {"python", "platform", "processor", "cpu_count"} <= set(info)


class TestWriteReport:
    def test_creates_parent_and_merges(self, tmp_path: Path) -> None:
        target = tmp_path / "nested" / "report.json"
        benchmark.write_report({"micro": {"zlib-1": {"encode_ms": 1.0}}}, target)
        benchmark.write_report({"parser": {"round_trip_ms": 2.0}}, target)
        stored = json.loads(target.read_text(encoding="utf-8"))
        assert stored["micro"]["zlib-1"]["encode_ms"] == 1.0
        assert stored["parser"]["round_trip_ms"] == 2.0

    def test_corrupt_existing_file_is_replaced(self, tmp_path: Path) -> None:
        target = tmp_path / "report.json"
        target.write_text("not json", encoding="utf-8")
        benchmark.write_report({"ok": True}, target)
        assert json.loads(target.read_text(encoding="utf-8")) == {"ok": True}


class TestFormatReport:
    def test_renders_micro_parser_and_e2e(self) -> None:
        text = benchmark.format_report(
            {
                "frame": {"width": 8, "height": 4, "bytes": 128},
                "micro": {"zlib-1": {"encode_ms": 0.5, "decode_ms": 0.2, "ratio": 3.0}},
                "parser": {"round_trip_ms": 0.7},
                "pump": {"handoff_ms": 0.03, "encode_ref_ms": 33.0, "caller_speedup": 1100.0},
                "end_to_end": {"crawl_fps": 42.0},
            }
        )
        assert "8x4" in text
        assert "zlib-1: encode 0.5 ms / decode 0.2 ms (ratio 3.0x)" in text
        assert "parser round trip: 0.7 ms" in text
        assert "pump handoff: 0.03 ms/frame" in text
        assert "e2e crawl_fps: 42.0" in text

    def test_empty_report(self) -> None:
        assert benchmark.format_report({}) == ""


class TestRealtimeFpsStats:
    def test_steady_30fps_sustains_realtime(self) -> None:
        stats = benchmark.realtime_fps_stats([i / 30 for i in range(91)], target_fps=30)
        assert stats["fps"] == 30.0
        assert stats["interval_max_ms"] == 33.33
        assert stats["sustains_realtime"] is True

    def test_slow_stream_is_below_realtime(self) -> None:
        stats = benchmark.realtime_fps_stats([i / 20 for i in range(41)], target_fps=30)
        assert stats["fps"] == 20.0
        assert stats["sustains_realtime"] is False

    def test_one_long_hitch_breaks_realtime_even_with_a_good_average(self) -> None:
        times = [i / 60 for i in range(60)]  # 60 fps for a second...
        times += [t + 0.5 for t in [1.0 + i / 60 for i in range(60)]]  # ...then a 0.5s stall
        stats = benchmark.realtime_fps_stats(times, target_fps=30)
        assert stats["fps"] > 28.5
        assert stats["interval_max_ms"] > 500
        assert stats["sustains_realtime"] is False

    def test_p5_fps_reflects_the_worst_intervals(self) -> None:
        times = [0.0]
        for i in range(100):
            times.append(times[-1] + (0.1 if i % 10 == 9 else 0.02))  # 1-in-10 slow frames
        stats = benchmark.realtime_fps_stats(times, target_fps=30)
        assert stats["p5_fps"] == 10.0

    def test_unsorted_input_is_handled(self) -> None:
        shuffled = [0.2, 0.0, 0.1, 0.3]
        assert benchmark.realtime_fps_stats(shuffled)["frames"] == 4

    def test_too_few_frames_reports_zero(self) -> None:
        for times in ([], [1.0]):
            stats = benchmark.realtime_fps_stats(times)
            assert stats["fps"] == 0.0 and stats["sustains_realtime"] is False

    def test_report_formats_the_realtime_line(self) -> None:
        stats = benchmark.realtime_fps_stats([i / 30 for i in range(31)])
        text = benchmark.format_report({"realtime": stats})
        assert "realtime: 30.0 effective fps of 30" in text and "SUSTAINED" in text


class TestEffectiveFps:
    def test_skipped_static_frames_do_not_lower_effective_fps(self) -> None:
        # 15 delivered frames over 1s (deduped waits), but the 1s scene played in 1s.
        stats = benchmark.realtime_fps_stats(
            [i / 15 for i in range(16)], target_fps=30, scene_seconds=1.0, wall_seconds=1.0
        )
        assert stats["fps"] == 15.0
        assert stats["effective_fps"] == 30.0

    def test_slow_playback_lowers_effective_fps(self) -> None:
        stats = benchmark.realtime_fps_stats(
            [i / 30 for i in range(31)], target_fps=30, scene_seconds=1.0, wall_seconds=1.25
        )
        assert stats["effective_fps"] == 24.0
        assert stats["sustains_realtime"] is False


class TestSkippedFrames:
    def _stream_with_static_wait(self) -> list[float]:
        times = [i / 30 for i in range(10)]  # 10 frames at 30 fps
        times += [times[-1] + 0.2 + i / 30 for i in range(10)]  # a 0.2 s static wait, then more
        return times

    def test_gaps_explained_by_skipped_frames_are_not_stalls(self) -> None:
        stats = benchmark.realtime_fps_stats(
            self._stream_with_static_wait(), scene_seconds=0.9, wall_seconds=0.9, skipped_frames=5
        )
        assert stats["missing_frames"] == 5
        assert stats["unexplained_missing"] == 0
        assert stats["sustains_realtime"] is True

    def test_gaps_beyond_the_skipped_frames_are_real_stalls(self) -> None:
        stats = benchmark.realtime_fps_stats(
            self._stream_with_static_wait(), scene_seconds=0.9, wall_seconds=0.9, skipped_frames=0
        )
        assert stats["unexplained_missing"] == 5
        assert stats["sustains_realtime"] is False
