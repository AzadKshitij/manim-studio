"""Unit tests for the pure stats helpers."""

from __future__ import annotations

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
