"""Repeatable frame-pipeline benchmarks (pure stdlib, optional codecs).

Optimization work is judged against recorded numbers, so the measurement lives
in one place: :func:`bench_codecs` times encode/decode of one RGBA frame for
every codec the environment offers, :func:`bench_parser` times the editor-side
``FrameParser`` round trip, and :func:`write_report` merges a run into a JSON
file so a baseline and a later run can be diffed.

Callers supply the raw pixels, so this module needs neither numpy nor a GL
context and stays import-safe everywhere.  :func:`bench_editor_frame` measures
the Qt-side per-frame work and imports PySide6 lazily, so the module itself
still pulls in no third-party code at load time.
"""

from __future__ import annotations

import itertools
import json
import os
import platform
import sys
import time
import zlib
from functools import partial
from pathlib import Path
from typing import TYPE_CHECKING, Any

from manim_studio import ipc

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

__all__ = [
    "REALTIME_FPS",
    "bench_codecs",
    "bench_editor_frame",
    "bench_parser",
    "codec_candidates",
    "format_report",
    "machine_info",
    "median_seconds",
    "realtime_fps_stats",
    "write_report",
]


def median_seconds(operation: Callable[[], Any], *, repeats: int = 7, warmup: int = 1) -> float:
    """Median wall-clock seconds for ``operation`` after ``warmup`` throwaways."""
    for _ in range(max(0, warmup)):
        operation()
    samples: list[float] = []
    for _ in range(max(1, repeats)):
        start = time.perf_counter()
        operation()
        samples.append(time.perf_counter() - start)
    samples.sort()
    return samples[len(samples) // 2]


def codec_candidates() -> list[tuple[str, Callable[[bytes], bytes], Callable[[bytes], bytes]]]:
    """Named ``(encode, decode)`` payload codecs available in this environment.

    zlib is always present; zstandard and lz4 are included when importable so a
    benchmark can compare them before one is chosen as the wire codec.
    """
    candidates: list[tuple[str, Callable[[bytes], bytes], Callable[[bytes], bytes]]] = [
        ("zlib-1", lambda data: zlib.compress(data, 1), zlib.decompress),
        ("zlib-6", lambda data: zlib.compress(data, 6), zlib.decompress),
    ]
    try:
        import zstandard
    except ImportError:
        pass
    else:
        for level in (1, 3):
            compressor = zstandard.ZstdCompressor(level=level)
            decompressor = zstandard.ZstdDecompressor()
            candidates.append((f"zstd-{level}", compressor.compress, decompressor.decompress))
    try:
        import lz4.block
    except ImportError:
        pass
    else:
        candidates.append(("lz4-block", lz4.block.compress, lz4.block.decompress))
    return candidates


def bench_codecs(rgba: bytes, *, repeats: int = 7, warmup: int = 1) -> dict[str, Any]:
    """Time payload encode/decode plus the compression ratio for each codec."""
    results: dict[str, Any] = {}
    for name, encode, decode in codec_candidates():
        blob = encode(rgba)
        if decode(blob) != rgba:
            raise ValueError(f"codec {name} did not round-trip")
        encode_ms = median_seconds(partial(encode, rgba), repeats=repeats, warmup=warmup) * 1000.0
        decode_ms = median_seconds(partial(decode, blob), repeats=repeats, warmup=warmup) * 1000.0
        results[name] = {
            "encode_ms": round(encode_ms, 3),
            "decode_ms": round(decode_ms, 3),
            "payload_bytes": len(blob),
            "ratio": round(len(rgba) / max(len(blob), 1), 2),
        }
    return results


def bench_parser(
    width: int,
    height: int,
    rgba: bytes,
    *,
    repeats: int = 7,
    warmup: int = 1,
    chunk: int = 1 << 16,
) -> dict[str, Any]:
    """Time the full ``encode_frame`` -> ``FrameParser`` round trip in ``chunk`` bursts."""
    blob = ipc.encode_frame(width, height, rgba)

    def round_trip() -> int:
        parser = ipc.FrameParser()
        frames: list[ipc.Frame] = []
        for start in range(0, len(blob), chunk):
            frames.extend(parser.feed(blob[start : start + chunk]))
        if len(frames) != 1 or frames[0].rgba != rgba:
            raise ValueError("parser round-trip failed")
        return len(frames)

    round_trip()
    decode_ms = median_seconds(round_trip, repeats=repeats, warmup=warmup) * 1000.0
    return {"payload_bytes": len(blob), "round_trip_ms": round(decode_ms, 3)}


def bench_editor_frame(
    width: int,
    height: int,
    rgba: bytes,
    *,
    viewport: tuple[int, int] = (960, 540),
    repeats: int = 20,
    warmup: int = 3,
) -> dict[str, float]:
    """Time the editor's per-frame Qt work (PySide6 imported lazily).

    The GUI thread pays this for every frame: wrapping the decompressed buffer in
    a ``QImage``, the defensive copy ``decode_latest_frame`` used to make, the
    ``QPixmap`` conversion and both scaling modes.  A ``QApplication`` must exist.
    """
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QImage, QPixmap

    stride = width * 4
    fmt = QImage.Format.Format_RGBA8888
    owning = QImage(rgba, width, height, stride, fmt).copy()
    view_w, view_h = viewport

    def wrap() -> QImage:
        return QImage(rgba, width, height, stride, fmt)

    def wrap_copy() -> QImage:
        return QImage(rgba, width, height, stride, fmt).copy()

    def from_image() -> QPixmap:
        return QPixmap.fromImage(owning)

    def scale_smooth() -> QPixmap:
        return QPixmap.fromImage(owning).scaled(
            view_w,
            view_h,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )

    def scale_fast() -> QPixmap:
        return QPixmap.fromImage(owning).scaled(
            view_w,
            view_h,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.FastTransformation,
        )

    def milliseconds(operation: Callable[[], Any]) -> float:
        return round(median_seconds(operation, repeats=repeats, warmup=warmup) * 1000.0, 4)

    return {
        "wrap_ms": milliseconds(wrap),
        "wrap_copy_ms": milliseconds(wrap_copy),
        "from_image_ms": milliseconds(from_image),
        "scale_smooth_ms": milliseconds(scale_smooth),
        "scale_fast_ms": milliseconds(scale_fast),
    }


REALTIME_FPS = 30


def realtime_fps_stats(
    arrival_times: Sequence[float],
    *,
    target_fps: int = REALTIME_FPS,
    scene_seconds: float | None = None,
    wall_seconds: float | None = None,
) -> dict[str, Any]:
    """Delivered-frame rate and smoothness from per-frame arrival timestamps (seconds).

    ``fps`` is frames delivered per second over the span between the first and last
    arrival; ``p5_fps`` is the instantaneous rate at the 95th-percentile frame
    interval, i.e. what the viewer sees during the worst 1-in-20 hitch.  A stream
    ``sustains_realtime`` when it averages at least 95% of ``target_fps`` and the
    worst-case hitch stays under two target frame intervals.

    The host deliberately skips frames identical to the previous one (static waits),
    so delivered ``fps`` understates what the viewer sees.  When ``scene_seconds``
    (scripted duration) and ``wall_seconds`` (time the playback took) are given,
    ``effective_fps`` = ``target_fps`` x scene/wall is the real-time rate of the
    scene itself and is what ``sustains_realtime`` is judged on.
    """
    times = sorted(float(t) for t in arrival_times)
    if len(times) < 2:
        return {
            "target_fps": target_fps,
            "frames": len(times),
            "fps": 0.0,
            "effective_fps": 0.0,
            "p5_fps": 0.0,
            "interval_p50_ms": 0.0,
            "interval_p95_ms": 0.0,
            "interval_max_ms": 0.0,
            "sustains_realtime": False,
        }
    intervals = sorted(b - a for a, b in itertools.pairwise(times))
    span = times[-1] - times[0]
    p95 = intervals[min(len(intervals) - 1, int(len(intervals) * 0.95))]
    p50 = intervals[len(intervals) // 2]
    fps = (len(times) - 1) / span if span > 0 else 0.0
    budget = 1.0 / target_fps
    effective = target_fps * scene_seconds / wall_seconds if scene_seconds and wall_seconds else fps
    return {
        "target_fps": target_fps,
        "frames": len(times),
        "fps": round(fps, 2),
        "effective_fps": round(effective, 2),
        "p5_fps": round(1.0 / p95, 2) if p95 > 0 else 0.0,
        "interval_p50_ms": round(p50 * 1000.0, 2),
        "interval_p95_ms": round(p95 * 1000.0, 2),
        "interval_max_ms": round(intervals[-1] * 1000.0, 2),
        "sustains_realtime": effective >= 0.95 * target_fps and intervals[-1] < 2.0 * budget,
    }


def machine_info() -> dict[str, Any]:
    """A few facts that make two report files comparable."""
    return {
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "processor": platform.processor(),
        "cpu_count": os.cpu_count(),
    }


def write_report(report: dict[str, Any], path: str | os.PathLike[str]) -> Path:
    """Merge ``report`` into the JSON file at ``path`` and return the path."""
    target = Path(path)
    if target.exists():
        try:
            existing = json.loads(target.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            existing = None
        if isinstance(existing, dict):
            existing.update(report)
            report = existing
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return target


def format_report(report: dict[str, Any]) -> str:
    """Human-readable one-line-per-metric rendering of a benchmark report."""
    lines: list[str] = []
    frame = report.get("frame")
    if isinstance(frame, dict):
        lines.append(
            f"frame: {frame.get('width')}x{frame.get('height')} ({frame.get('bytes')} bytes RGBA)"
        )
    micro = report.get("micro")
    if isinstance(micro, dict):
        for name, entry in sorted(micro.items()):
            if isinstance(entry, dict) and "encode_ms" in entry:
                lines.append(
                    f"{name}: encode {entry['encode_ms']} ms / decode {entry['decode_ms']} ms "
                    f"(ratio {entry['ratio']}x)"
                )
    parser = report.get("parser")
    if isinstance(parser, dict):
        lines.append(f"parser round trip: {parser.get('round_trip_ms')} ms")
    pump = report.get("pump")
    if isinstance(pump, dict):
        lines.append(
            f"pump handoff: {pump.get('handoff_ms')} ms/frame "
            f"(vs {pump.get('encode_ref_ms')} ms encode, {pump.get('caller_speedup')}x)"
        )
    editor = report.get("editor")
    if isinstance(editor, dict):
        for name, value in sorted(editor.items()):
            lines.append(f"editor {name}: {value} ms")
    end_to_end = report.get("end_to_end")
    if isinstance(end_to_end, dict):
        for name, value in sorted(end_to_end.items()):
            lines.append(f"e2e {name}: {value}")
    realtime = report.get("realtime")
    if isinstance(realtime, dict):
        lines.append(
            f"realtime: {realtime.get('effective_fps')} effective fps of "
            f"{realtime.get('target_fps')} (delivered {realtime.get('fps')} fps, "
            f"start latency {realtime.get('start_latency_ms')} ms, "
            f"worst-5% {realtime.get('p5_fps')} fps, interval p50 "
            f"{realtime.get('interval_p50_ms')} ms / p95 {realtime.get('interval_p95_ms')} ms / "
            f"max {realtime.get('interval_max_ms')} ms) -> "
            f"{'SUSTAINED' if realtime.get('sustains_realtime') else 'BELOW REALTIME'}"
        )
    reload_report = report.get("reload")
    if isinstance(reload_report, dict):
        for name, entry in reload_report.items():
            if not isinstance(entry, dict):
                continue
            phases = entry.get("load_timings") or {}
            detail = " ".join(f"{key}={value}" for key, value in phases.items())
            lines.append(
                f"reload {name}: log {entry.get('reload_to_log_s')}s, "
                f"crawl done {entry.get('reload_to_crawl_done_s')}s [{detail}]"
            )
    return "\n".join(lines)
