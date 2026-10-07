"""Repeatable performance benchmarks for the frame pipeline.

These are the numbers the optimization work is judged against. They never assert
absolute timings (those are machine-dependent); they assert the work completed
and, when ``MANIM_STUDIO_PERF_REPORT`` points at a file, record a JSON report so
a later run can be diffed against the committed baseline in ``docs/perf/``.

Micro benchmark (codec + editor + parser), no GL needed::

    uv run pytest tests/test_perf.py -m slow -s

End-to-end benchmark against a real 720p preview host::

    uv run pytest tests/test_perf.py -m gl -s

Record both into one file::

    $env:MANIM_STUDIO_PERF_REPORT = "docs/perf/latest.json"
    uv run pytest tests/test_perf.py -m "slow or gl" -s
"""

from __future__ import annotations

import os
import queue
import socket
import subprocess
import time
from typing import Any

import pytest

from manim_studio import benchmark
from manim_studio.ipc import Command, Event
from manim_studio.preview_host import FramePump
from tests.host_harness import HostPeer

WIDTH, HEIGHT = 1280, 720


def _render_like_frame(width: int, height: int) -> bytes:
    """A deterministic gradient+shapes frame with the edge content a render has."""
    import numpy as np

    yy, xx = np.mgrid[0:height, 0:width]
    rgba = np.empty((height, width, 4), dtype=np.uint8)
    rgba[..., 0] = ((xx * 255) // max(width - 1, 1)).astype(np.uint8)
    rgba[..., 1] = ((yy * 255) // max(height - 1, 1)).astype(np.uint8)
    rgba[..., 2] = 51
    rgba[..., 3] = 255
    cx, cy, radius = width // 3, height // 2, max(1, height // 5)
    disc = (xx - cx) ** 2 + (yy - cy) ** 2 <= radius**2
    rgba[disc] = (240, 240, 240, 255)
    for i in range(4):
        x0 = width // 2 + i * 40
        rgba[:, x0 : x0 + 12, :3] = (30 * (i + 1)) % 256
    return rgba.tobytes()


def _record(report: dict[str, Any]) -> None:
    print(benchmark.format_report(report))
    target = os.environ.get("MANIM_STUDIO_PERF_REPORT")
    if target:
        path = benchmark.write_report(report, target)
        print(f"report written to {path}")


def _drain_frames(peer: HostPeer) -> int:
    count = 0
    while True:
        try:
            peer.frames.get_nowait()
        except queue.Empty:
            return count
        count += 1


@pytest.mark.slow
class TestMicroBenchmark:
    def test_frame_codecs_and_parser(self, qapp) -> None:
        rgba = _render_like_frame(WIDTH, HEIGHT)
        micro = benchmark.bench_codecs(rgba, repeats=15)
        pipeline_codec = "zstd-1" if "zstd-1" in micro else "zlib-1"
        report = {
            "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "machine": benchmark.machine_info(),
            "frame": {"width": WIDTH, "height": HEIGHT, "bytes": len(rgba)},
            "micro": micro,
            "editor": benchmark.bench_editor_frame(WIDTH, HEIGHT, rgba),
            "parser": benchmark.bench_parser(WIDTH, HEIGHT, rgba, repeats=15),
            "pump": _pump_handoff(rgba, encode_ref_ms=micro[pipeline_codec]["encode_ms"]),
        }
        _record(report)
        assert report["micro"]["zlib-1"]["encode_ms"] >= 0.0
        assert report["editor"]["wrap_ms"] >= 0.0
        assert report["parser"]["round_trip_ms"] >= 0.0
        assert report["pump"]["handoff_ms"] >= 0.0


def _pump_handoff(rgba: bytes, *, encode_ref_ms: float) -> dict[str, float]:
    """Caller-side ``FramePump.offer`` cost per 720p frame.

    Encoding runs on the pump thread now, so the caller (GL/idle loop) only pays
    a lock + buffer swap. Compare this with ``encode_ref_ms`` for the same codec.
    """
    sender, receiver = socket.socketpair()
    pump = FramePump(sender)
    pump.start()
    try:
        handoff_ms = (
            benchmark.median_seconds(lambda: pump.offer(rgba, WIDTH, HEIGHT), repeats=50) * 1000.0
        )
    finally:
        pump.close()
        receiver.close()
    return {
        "handoff_ms": round(handoff_ms, 4),
        "encode_ref_ms": encode_ref_ms,
        "caller_speedup": round(encode_ref_ms / max(handoff_ms, 1e-6), 1),
    }


@pytest.mark.gl
class TestEndToEndBenchmark:
    def test_host_pipeline(self) -> None:
        peer = HostPeer()
        started = time.monotonic()
        process = peer.start_host("--size", f"{WIDTH}x{HEIGHT}")
        try:
            try:
                peer.accept_control()
                peer.accept_frames()
            except (TimeoutError, OSError) as exc:
                pytest.skip(f"preview host did not start (GL unavailable?): {exc}")

            log_event = peer.wait_event(lambda event: event.get("event") == Event.LOG)
            trace_seconds = time.monotonic() - started
            blocks = log_event.get("blocks") or []
            total_duration = max(
                (float(b["start"]) + float(b["duration"]) for b in blocks), default=0.0
            )
            peer.wait_frame(timeout=60)  # the initial (final-frame) stream

            # Crawl throughput: the idle loop renders + encodes every cached frame,
            # so this is where inline encoding shows up as a slow crawl.
            crawl_started = time.monotonic()
            strip_event = peer.wait_event(
                lambda event: event.get("event") == Event.FILMSTRIP and event.get("done"),
                timeout=300,
            )
            crawl_seconds = time.monotonic() - crawl_started
            crawl_frames = int(strip_event.get("frames") or 0)

            # Playback cadence: wall time to run the whole scene versus its scripted
            # duration.  A blocking encode on the GL thread pushes this above 1.0.
            _drain_frames(peer)
            play_started = time.monotonic()
            peer.send(Command.PLAY, **{"from": 0})
            stopped: dict[str, Any] | None = None
            deadline = play_started + 300
            play_frames = 0
            while time.monotonic() < deadline:
                play_frames += _drain_frames(peer)
                try:
                    event = peer.events.get_nowait()
                except queue.Empty:
                    time.sleep(0.005)
                    continue
                if event.get("event") == Event.STOPPED:
                    stopped = event
                    break
                peer.seen.append(event)
            play_seconds = time.monotonic() - play_started

            peer.send(Command.STATS)
            stats_event = peer.wait_event(
                lambda event: event.get("event") == Event.STATS, timeout=60
            )

            report = {
                "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                "machine": benchmark.machine_info(),
                "frame": {"width": WIDTH, "height": HEIGHT, "bytes": WIDTH * HEIGHT * 4},
                "end_to_end": {
                    "trace_seconds": round(trace_seconds, 3),
                    "crawl_frames": crawl_frames,
                    "crawl_seconds": round(crawl_seconds, 3),
                    "crawl_fps": round(crawl_frames / max(crawl_seconds, 1e-6), 2),
                    "total_duration": round(total_duration, 3),
                    "play_frames": play_frames,
                    "play_seconds": round(play_seconds, 3),
                    "playback_fps": round(play_frames / max(play_seconds, 1e-6), 2),
                    "play_realtime_ratio": round(play_seconds / max(total_duration, 1e-6), 3),
                    "host_timings": stats_event.get("timings", {}),
                    "stopped": stopped is not None,
                },
            }
            _record(report)
            assert crawl_frames > 0
            assert play_frames > 0
        finally:
            if process.poll() is None:
                peer.send(Command.QUIT)
                try:
                    process.wait(timeout=30)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
            peer.close()
