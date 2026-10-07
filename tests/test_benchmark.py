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
                "end_to_end": {"crawl_fps": 42.0},
            }
        )
        assert "8x4" in text
        assert "zlib-1: encode 0.5 ms / decode 0.2 ms (ratio 3.0x)" in text
        assert "parser round trip: 0.7 ms" in text
        assert "e2e crawl_fps: 42.0" in text

    def test_empty_report(self) -> None:
        assert benchmark.format_report({}) == ""
