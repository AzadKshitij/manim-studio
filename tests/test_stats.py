"""Unit tests for the pure stats helpers."""

from __future__ import annotations

import threading

import pytest

from manim_studio import stats


class TestFormatBytes:
    def test_none_and_bad_input(self) -> None:
        assert stats.format_bytes(None) == "—"
        assert stats.format_bytes(-1) == "—"

    def test_boundaries(self) -> None:
        assert stats.format_bytes(0) == "0 B"
        assert stats.format_bytes(512) == "512 B"
        assert stats.format_bytes(1023) == "1023 B"
        assert stats.format_bytes(1024) == "1.0 KiB"
        assert stats.format_bytes(1536) == "1.5 KiB"
        assert stats.format_bytes(1024 * 1024) == "1.0 MiB"
        assert stats.format_bytes(1024**3) == "1.0 GiB"
        assert stats.format_bytes(1024**4) == "1.0 TiB"


class TestUsagePercent:
    def test_basic(self) -> None:
        assert stats.usage_percent(64, 256) == 25.0

    def test_invalid(self) -> None:
        assert stats.usage_percent(None, 256) is None
        assert stats.usage_percent(64, 0) is None
        assert stats.usage_percent(-1, 256) is None
        assert stats.usage_percent(300, 100) == 100.0


class TestCacheSummary:
    def test_full(self) -> None:
        text = stats.cache_summary(312, 12 * 1024 * 1024, 256 * 1024 * 1024, 7200)
        assert "312 frames" in text
        assert "12.0 MiB / 256.0 MiB" in text
        assert "(5%)" in text
        assert "cap 7200 frames" in text

    def test_missing_budget(self) -> None:
        text = stats.cache_summary(10, 512, None)
        assert "10 frames" in text
        assert "512 B" in text

    def test_missing_values(self) -> None:
        text = stats.cache_summary(None, None, None)
        assert "—" in text


class TestProcessRss:
    def test_returns_positive_or_none(self) -> None:
        value = stats.process_rss_bytes()
        assert value is None or (isinstance(value, int) and value > 0)


class TestTimingStats:
    def test_records_ewma_max_and_count(self) -> None:
        timings = stats.TimingStats(alpha=1.0)  # alpha 1.0 -> average tracks latest
        timings.record("render", 0.002)
        timings.record("render", 0.006)
        snapshot = timings.snapshot()
        assert snapshot["render"]["avg_ms"] == 6.0
        assert snapshot["render"]["max_ms"] == 6.0
        assert snapshot["render"]["count"] == 2

    def test_phase_context_manager(self) -> None:
        timings = stats.TimingStats()
        with timings.phase("scale"):
            pass
        assert list(timings.snapshot()) == ["scale"]
        assert timings.snapshot()["scale"]["count"] == 1

    def test_ignores_invalid_names_and_values(self) -> None:
        timings = stats.TimingStats()
        timings.record("", 0.001)
        timings.record("render", float("nan"))
        timings.record("render", -1.0)
        assert timings.snapshot() == {}

    def test_snapshot_is_sorted_by_name(self) -> None:
        timings = stats.TimingStats()
        timings.record("send", 0.001)
        timings.record("decode", 0.001)
        assert list(timings.snapshot()) == ["decode", "send"]

    def test_thread_safety_counts_every_record(self) -> None:
        timings = stats.TimingStats()

        def worker() -> None:
            for _ in range(500):
                timings.record("readback", 0.0001)

        threads = [threading.Thread(target=worker) for _ in range(4)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        assert timings.snapshot()["readback"]["count"] == 2000

    def test_reset(self) -> None:
        timings = stats.TimingStats()
        timings.record("render", 0.001)
        timings.reset()
        assert timings.snapshot() == {}

    def test_invalid_alpha(self) -> None:
        with pytest.raises(ValueError):
            stats.TimingStats(alpha=0)


class TestFormatTimings:
    def test_formats_one_line_per_phase(self) -> None:
        text = stats.format_timings({"render": {"avg_ms": 3.12, "max_ms": 7.2, "count": 120}})
        assert text == "render: 3.1 ms (max 7.2, n=120)"

    def test_empty_snapshot(self) -> None:
        assert stats.format_timings({}) == ""
