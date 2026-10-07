"""Tests for the preview host: pure components + real end-to-end GL integration."""

from __future__ import annotations

import argparse
import queue
import socket
import subprocess
import time
from pathlib import Path
from typing import Any

import pytest

from manim_studio.ipc import (
    Command,
    Event,
    Frame,
    FrameParser,
)
from manim_studio.preview_host import (
    FramePump,
    HostConfig,
    SceneHost,
    TickThrottler,
    parse_size,
)
from manim_studio.stats import TimingStats
from manim_studio.timeline_model import TimelineBlock
from tests.helpers import FakeScene
from tests.host_harness import SCENE_FILE, HostPeer

# ---------------------------------------------------------------------------
# Pure unit tests
# ---------------------------------------------------------------------------


class TestTickThrottler:
    def test_first_call_is_ready(self) -> None:
        assert TickThrottler(interval=0.1, clock=lambda: 0.0).ready()

    def test_rate_limited_until_interval_elapses(self) -> None:
        now = [0.0]
        throttler = TickThrottler(interval=0.1, clock=lambda: now[0])
        assert throttler.ready()  # t=0
        assert not throttler.ready()  # t=0
        now[0] = 0.05
        assert not throttler.ready()
        now[0] = 0.1
        assert throttler.ready()

    def test_reset(self) -> None:
        now = [0.0]
        throttler = TickThrottler(interval=10.0, clock=lambda: now[0])
        assert throttler.ready()
        assert not throttler.ready()
        throttler.reset()
        assert throttler.ready()


class TestParseSize:
    def test_valid(self) -> None:
        assert parse_size("1280x720") == (1280, 720)
        assert parse_size("320X180") == (320, 180)

    @pytest.mark.parametrize("text", ["nope", "100", "100x", "x100", "10x20"])
    def test_invalid(self, text: str) -> None:
        with pytest.raises(argparse.ArgumentTypeError):
            parse_size(text)


class TestApplyTiming:
    """Timing overrides rebase the host's blocks without touching the trace."""

    def _host(self) -> SceneHost:
        host = SceneHost(HostConfig(scene_path=Path("unused.py")))
        host._base_blocks = (
            TimelineBlock(0, "ShowCreation", 0.0, 1.0),
            TimelineBlock(1, "Wait", 1.0, 0.5),
            TimelineBlock(2, "Transform", 1.5, 2.0),
        )
        host.blocks = host._base_blocks
        return host

    def test_doubling_shifts_later_blocks(self) -> None:
        host = self._host()
        host._apply_timing([{"index": 0, "duration": 2.0}])
        assert [(b.start, b.duration) for b in host.blocks] == [
            (0.0, 2.0),
            (2.0, 0.5),
            (2.5, 2.0),
        ]
        assert host._timing_factors == {0: 2.0}

    def test_empty_timings_restore_the_base(self) -> None:
        host = self._host()
        host._apply_timing([{"index": 1, "duration": 1.5}])
        host._apply_timing([])
        assert host.blocks == host._base_blocks
        assert host._timing_factors == {}

    def test_unknown_or_invalid_entries_are_ignored(self) -> None:
        host = self._host()
        host._apply_timing([{"index": 9, "duration": 1.0}, {"index": -1, "duration": 1.0}])
        assert host._timing_factors == {}
        host._apply_timing([{"index": 0, "duration": 0.0}, {"index": 1, "duration": -2.0}])
        assert host._timing_factors == {}
        assert host.blocks == host._base_blocks


class TestStopRace:
    """A play queued before a STOP must not clear the pause (loop replay race)."""

    def _host(self) -> SceneHost:
        host = SceneHost(HostConfig(scene_path=Path("unused.py")))
        host.blocks = (TimelineBlock(0, "A", 0.0, 1.0),)
        return host

    def test_a_play_superseded_by_a_stop_is_ignored(self) -> None:
        host = self._host()
        host._stop_gen = 5
        seen: list[tuple[str, dict]] = []
        host.emit = lambda event, **payload: seen.append((event, payload))  # type: ignore[method-assign]
        host._cmd_play({"cmd": "play", "from": 0, "_stop_gen": 4})
        assert seen == [(Event.STATUS, {"state": "idle"})]

    def test_a_fresh_play_proceeds_past_the_guard(self) -> None:
        host = self._host()
        host._stop_gen = 5

        def boom(*_args: Any, **_kwargs: Any) -> Any:
            raise RuntimeError("reached make_scene")

        host._make_scene = boom  # type: ignore[method-assign]
        with pytest.raises(RuntimeError, match="reached make_scene"):
            host._cmd_play({"cmd": "play", "from": 0, "_stop_gen": 5})


class TestSceneCallLines:
    def test_keeps_only_lines_inside_the_scene_file(self, tmp_path: Path) -> None:
        scene = tmp_path / "scene.py"
        helper = tmp_path / "helper.py"
        lines = SceneHost._scene_call_lines(((str(scene), 12), (str(helper), 3), None), scene)
        assert lines == (12, None, None)


class TestFramePump:
    def _rgba(self, width: int, height: int, seed: int = 0) -> bytes:
        return bytes(((i + seed) * 13) % 256 for i in range(width * height * 4))

    def test_latest_frame_wins(self) -> None:
        sender, receiver = socket.socketpair()
        try:
            pump = FramePump(sender)
            first, second, third = (self._rgba(4, 4, seed) for seed in (1, 2, 3))
            pump.offer(first, 4, 4)
            pump.offer(second, 4, 4)
            pump.offer(third, 4, 4)
            assert pump.frames_dropped == 2
            pump.start()

            receiver.settimeout(10)
            parser = FrameParser()
            frames: list[Frame] = []
            deadline = time.monotonic() + 10
            while not frames and time.monotonic() < deadline:
                frames.extend(parser.feed(receiver.recv(65536)))
            assert len(frames) == 1
            assert frames[0].rgba == third
            deadline = time.monotonic() + 5
            while pump.frames_sent < 1 and time.monotonic() < deadline:
                time.sleep(0.01)
            assert pump.frames_sent >= 1
        finally:
            pump.close()
            receiver.close()

    def test_offer_after_close_is_ignored(self) -> None:
        sender, receiver = socket.socketpair()
        try:
            pump = FramePump(sender)
            pump.close()
            pump.offer(b"\x00" * 16, 2, 2)
            assert pump.frames_sent == 0
        finally:
            receiver.close()

    def test_bad_raw_frame_does_not_kill_pump(self) -> None:
        """Encoding on the pump thread must not let one bad frame stop the stream."""
        sender, receiver = socket.socketpair()
        try:
            pump = FramePump(sender)
            pump.start()
            pump.offer(b"\x00" * 7, 2, 2)  # wrong payload length -> ProtocolError
            time.sleep(0.05)
            pump.offer(self._rgba(2, 2), 2, 2)
            receiver.settimeout(10)
            parser = FrameParser()
            frames: list[Frame] = []
            deadline = time.monotonic() + 10
            while not frames and time.monotonic() < deadline:
                frames.extend(parser.feed(receiver.recv(65536)))
            assert len(frames) == 1
            assert frames[0].rgba == self._rgba(2, 2)
        finally:
            pump.close()
            receiver.close()

    def test_records_encode_and_send_timings(self) -> None:
        sender, receiver = socket.socketpair()
        timings = TimingStats()
        try:
            pump = FramePump(sender, timings=timings)
            pump.start()
            pump.offer(self._rgba(4, 4), 4, 4)
            deadline = time.monotonic() + 5
            while pump.frames_sent < 1 and time.monotonic() < deadline:
                time.sleep(0.01)
            snapshot = timings.snapshot()
            assert snapshot["encode"]["count"] == 1
            assert snapshot["send"]["count"] >= 1
        finally:
            pump.close()
            receiver.close()

    def test_identical_frames_are_deduped(self) -> None:
        sender, receiver = socket.socketpair()
        try:
            pump = FramePump(sender)
            pump.start()
            frame = self._rgba(4, 4)
            pump.offer(frame, 4, 4)
            deadline = time.monotonic() + 5
            while pump.frames_sent < 1 and time.monotonic() < deadline:
                time.sleep(0.01)
            pump.offer(frame, 4, 4)  # byte-identical: no re-encode, no send
            time.sleep(0.05)
            assert pump.frames_sent == 1
            assert pump.frames_deduped == 1
            pump.offer(self._rgba(4, 4, seed=9), 4, 4)  # changed: sent normally
            deadline = time.monotonic() + 5
            while pump.frames_sent < 2 and time.monotonic() < deadline:
                time.sleep(0.01)
            assert pump.frames_sent == 2
            assert pump.frames_deduped == 1
        finally:
            pump.close()
            receiver.close()

    def test_encoded_frame_resets_the_dedup_baseline(self) -> None:
        from manim_studio.ipc import encode_frame

        sender, receiver = socket.socketpair()
        try:
            pump = FramePump(sender)
            pump.start()
            frame = self._rgba(4, 4)
            pump.offer(frame, 4, 4)
            deadline = time.monotonic() + 5
            while pump.frames_sent < 1 and time.monotonic() < deadline:
                time.sleep(0.01)
            pump.offer_encoded(encode_frame(4, 4, self._rgba(4, 4, seed=5)))
            deadline = time.monotonic() + 5
            while pump.frames_sent < 2 and time.monotonic() < deadline:
                time.sleep(0.01)
            # The editor now shows the encoded frame, so the earlier raw frame is
            # no longer current and must be sent again instead of being deduped.
            pump.offer(frame, 4, 4)
            deadline = time.monotonic() + 5
            while pump.frames_sent < 3 and time.monotonic() < deadline:
                time.sleep(0.01)
            assert pump.frames_sent == 3
            assert pump.frames_deduped == 0
        finally:
            pump.close()
            receiver.close()


class TestSceneShortcuts:
    """The live shell must expose the manimgl-style scene API, offline-testable."""

    def _host(self):
        from manim_studio.preview_host import HostConfig, SceneHost

        host = SceneHost(HostConfig(scene_path=Path("unused.py")))
        host.scene = FakeScene()
        return host

    def test_shortcuts_cover_scene_api(self) -> None:
        shortcuts = self._host()._scene_shortcuts()
        expected = {
            "play",
            "wait",
            "add",
            "remove",
            "clear",
            "save_state",
            "undo",
            "redo",
            "reload",
            "checkpoint_paste",
            "clear_checkpoints",
        }
        assert expected <= set(shortcuts)
        assert shortcuts["self"] is shortcuts["scene"]

    def test_shortcuts_empty_without_scene(self) -> None:
        from manim_studio.preview_host import HostConfig, SceneHost

        host = SceneHost(HostConfig(scene_path=Path("unused.py")))
        assert host._scene_shortcuts() == {}
        host._clear_checkpoints()  # no-op without checkpoints

    def test_embed_is_neutralized(self) -> None:
        """scene.embed() would launch a blocking terminal inside the host."""
        from manim_studio.preview_host import HostConfig, SceneHost

        class EmbedScene:
            def embed(self, *args: object, **kwargs: object) -> str:
                return "terminal"

        host = SceneHost(HostConfig(scene_path=Path("unused.py")))
        scene = EmbedScene()
        host._neutralize_embed(scene)
        assert scene.embed() is None


def _drain_frames(peer: HostPeer) -> None:
    while True:
        try:
            peer.frames.get_nowait()
        except queue.Empty:
            return


def _content_pixels(frame: Frame) -> int:
    """Count pixels that are not the #333333 background (stroke coverage)."""
    count = 0
    rgba = frame.rgba
    for index in range(0, len(rgba), 4):
        distance = abs(rgba[index] - 51) + abs(rgba[index + 1] - 51) + abs(rgba[index + 2] - 51)
        if distance > 60:
            count += 1
    return count


def _top_colors(frame: Frame, limit: int = 4) -> list[tuple[int, tuple[int, int, int]]]:
    """Dominant RGB colours, for diagnostic messages."""
    counts: dict[tuple[int, int, int], int] = {}
    rgba = frame.rgba
    for index in range(0, len(rgba), 4):
        pixel = (rgba[index], rgba[index + 1], rgba[index + 2])
        counts[pixel] = counts.get(pixel, 0) + 1
    return sorted(((n, rgb) for rgb, n in counts.items()), reverse=True)[:limit]


# ---------------------------------------------------------------------------
# End-to-end integration against a spawned host
# ---------------------------------------------------------------------------


# ``HostPeer`` and the scene/repo constants live in ``tests/host_harness`` so the
# performance benchmarks can spawn the same host without importing a test module.


@pytest.fixture(scope="module")
def host_session() -> Any:
    """Spawn one real preview host for the whole module and drive it."""
    peer = HostPeer()
    process = peer.start_host()
    try:
        control_hello = peer.accept_control()
        frames_hello = peer.accept_frames()
    except (TimeoutError, OSError) as exc:
        process.kill()
        process.wait()
        peer.close()
        pytest.skip(f"preview host did not start (GL unavailable?): {exc}")

    try:
        log_event = peer.wait_event(lambda event: event.get("event") == Event.LOG)
        yield peer, process, control_hello, frames_hello, log_event
    finally:
        if process.poll() is None:
            peer.send(Command.QUIT)
            try:
                process.wait(timeout=30)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
        peer.close()


@pytest.mark.gl
class TestHostIntegration:
    def test_hello_events(self, host_session: Any) -> None:
        from manim_studio.ipc import PROTOCOL_VERSION

        _, _, control_hello, frames_hello, _ = host_session
        assert control_hello["role"] == "host"
        assert control_hello["protocol"] == PROTOCOL_VERSION
        assert control_hello["embedded"] is True
        assert control_hello["size"] == [320, 180]
        assert frames_hello["role"] == "host"

    def test_trace_event_has_blocks_and_stats(self, host_session: Any) -> None:
        _, _, _, _, log_event = host_session
        assert len(log_event["blocks"]) >= 12
        assert log_event["stats"]["play"] >= 10
        assert log_event["stats"]["undo"] == 1
        assert [b["index"] for b in log_event["blocks"]] == list(range(len(log_event["blocks"])))
        call_sites = log_event["call_sites"]
        assert len(call_sites) == len(log_event["blocks"])
        # The showcase calls play()/wait() directly, so most lines resolve to the file.
        assert sum(1 for line in call_sites if isinstance(line, int) and line > 0) >= 10

    def test_initial_frame_is_streamed(self, host_session: Any) -> None:
        peer = host_session[0]
        frame = peer.wait_frame(timeout=30)
        assert (frame.width, frame.height) == (320, 180)
        assert len(frame.rgba) == 320 * 180 * 4

    def test_exec_command_round_trip(self, host_session: Any) -> None:
        peer = host_session[0]
        peer.send(Command.EXEC, code="print(6 * 7)")
        result = peer.wait_event(lambda event: event.get("event") == Event.EXEC_RESULT)
        assert result["ok"] is True
        assert "42" in result["stdout"]

    def test_seek_command_emits_tick_and_frame(self, host_session: Any) -> None:
        peer = host_session[0]
        peer.send(Command.SEEK, index=2)
        tick = peer.wait_event(
            lambda event: event.get("event") == Event.TICK and event.get("index") == 2
        )
        assert tick["time"] > 0

    def test_play_then_stop(self, host_session: Any) -> None:
        peer = host_session[0]
        peer.send(Command.PLAY, **{"from": 0})
        peer.wait_event(
            lambda event: event.get("event") == Event.STATUS and event.get("state") == "playing"
        )
        time.sleep(0.4)
        peer.send(Command.STOP)
        stopped = peer.wait_event(lambda event: event.get("event") == Event.STOPPED)
        assert stopped["user_stopped"] is True
        assert stopped["time"] < 30  # stopped early, not after a full playthrough

    def test_play_at_double_speed_reaches_the_timeline_end(self, host_session: Any) -> None:
        """Review speed must not shorten scene time (the head reaches the end)."""
        peer, _, _, _, log_event = host_session
        total = float(log_event["total_duration"])
        peer.drain()
        peer.send(Command.PLAY, **{"from": 0, "rate": 2.0})
        peer.wait_event(
            lambda event: event.get("event") == Event.STATUS and event.get("state") == "playing"
        )
        stopped = peer.wait_event(lambda event: event.get("event") == Event.STOPPED, timeout=60)
        assert stopped["time"] == pytest.approx(total, abs=0.4), (stopped["time"], total)

    def test_play_double_speed_is_faster(self, host_session: Any) -> None:
        """2x really plays faster in wall-clock time (not just a relabelled 1x)."""
        peer = host_session[0]

        def run(rate: float) -> float:
            peer.drain()
            start = time.monotonic()
            peer.send(Command.PLAY, **{"from": 0, "rate": rate})
            peer.wait_event(lambda event: event.get("event") == Event.STOPPED, timeout=90)
            return time.monotonic() - start

        normal = run(1.0)
        fast = run(2.0)
        assert fast < normal * 0.85, (normal, fast)

    def test_repeated_reloads_are_error_free(self, host_session: Any) -> None:
        """Regression: reloading while an offscreen frame is bound used to raise
        GLError 1282 (deleted window_fbo) on every cycle after the first."""
        peer = host_session[0]
        peer.drain()
        for cycle in range(3):
            peer.send(Command.RELOAD)
            peer.wait_event(lambda event: event.get("event") == Event.LOG)
            peer.wait_event(
                lambda event: event.get("event") == Event.STATUS and event.get("state") == "idle"
            )
            errors = [event for event in peer.drain() if event.get("event") == Event.ERROR]
            assert not errors, f"reload cycle {cycle} failed: {errors}"

    def test_scrub_poses_between_blocks(self, host_session: Any) -> None:
        """Scrubbing mid-block must render an intermediate state (half-drawn circle)."""
        peer = host_session[0]
        peer.drain()

        def scrub_to(time_seconds: float) -> Frame:
            # Frames are latest-wins by design, so a scrub frame can be dropped when
            # the transport is busy; retry until one arrives.  Draining before each
            # request discards late frames from earlier requests.
            for _ in range(6):
                _drain_frames(peer)
                peer.send(Command.SCRUB, time=time_seconds)
                peer.wait_event(
                    lambda event: (
                        event.get("event") == Event.TICK
                        and event.get("scrubbing") is True
                        and abs(float(event.get("time", -1.0)) - time_seconds) < 1e-6
                    )
                )
                try:
                    return peer.wait_frame(timeout=5)
                except TimeoutError:
                    continue
            raise TimeoutError(f"no scrub frame arrived for t={time_seconds}")

        partial = scrub_to(0.15)  # mid ShowCreation (block 0 spans 0.0-0.3)
        complete = scrub_to(0.30)  # start of Wait => creation finished
        later = scrub_to(0.60)  # still fully drawn (Rotate block)
        partial_pixels = _content_pixels(partial)
        complete_pixels = _content_pixels(complete)
        later_pixels = _content_pixels(later)
        assert 0 < partial_pixels < complete_pixels, (
            f"partial={partial_pixels} complete={complete_pixels} later={later_pixels} "
            f"partial_top={_top_colors(partial)} complete_top={_top_colors(complete)}\n"
            f"host stderr:\n{peer.host_stderr()[-1500:]}"
        )
        assert abs(later_pixels - complete_pixels) < 0.35 * complete_pixels
        errors = [event for event in peer.drain() if event.get("event") == Event.ERROR]
        assert not errors, errors

    def test_filmstrip_serves_cached_scrubs(self, host_session: Any) -> None:
        """Once the crawl is done, scrubbing is served from the cache (no replay)."""
        peer = host_session[0]
        peer.drain()
        peer.send(Command.RELOAD)
        log = peer.wait_event(
            lambda event: event.get("event") == Event.LOG and event.get("trace_id") is not None
        )
        trace_id = log["trace_id"]
        peer.wait_event(
            lambda event: (
                event.get("event") == Event.FILMSTRIP
                and event.get("trace_id") == trace_id
                and event.get("done") is True
            ),
            timeout=180,
        )
        peer.drain()
        peer.send(Command.SCRUB, time=1.0)
        tick = peer.wait_event(
            lambda event: (
                event.get("event") == Event.TICK
                and event.get("scrubbing") is True
                and event.get("cached") is True
            ),
            timeout=30,
        )
        assert tick["time"] == pytest.approx(1.0)
        errors = [event for event in peer.drain() if event.get("event") == Event.ERROR]
        assert not errors, errors

    def test_load_switches_scene_name(self, host_session: Any, tmp_path: Path) -> None:
        """Renaming the scene class must not leave the host stuck on the old name."""
        peer = host_session[0]
        two_scenes = tmp_path / "two_scenes.py"
        two_scenes.write_text(
            "from manimlib import Scene\n\n\n"
            "class Alpha(Scene):\n"
            "    def construct(self):\n"
            "        self.wait(0.1)\n\n\n"
            "class Beta(Scene):\n"
            "    def construct(self):\n"
            "        self.wait(0.2)\n",
            encoding="utf-8",
        )
        peer.drain()
        peer.send(Command.LOAD, path=str(two_scenes), scene_name="Beta")
        log = peer.wait_event(
            lambda event: event.get("event") == Event.LOG and event.get("scene_name") == "Beta"
        )
        assert len(log["blocks"]) == 1

        # Leave the shared host back on the showcase scene.
        peer.send(Command.LOAD, path=str(SCENE_FILE), scene_name="FeatureShowcase")
        peer.wait_event(
            lambda event: (
                event.get("event") == Event.LOG and event.get("scene_name") == "FeatureShowcase"
            )
        )

    def test_set_timing_retimes_and_reposes(self, host_session: Any) -> None:
        """Retiming a block returns the shifted timeline and keeps the host usable."""
        peer = host_session[0]
        peer.drain()
        peer.send(Command.RELOAD)
        log = peer.wait_event(
            lambda event: event.get("event") == Event.LOG and event.get("trace_id") is not None
        )
        blocks = log["blocks"]
        target = blocks[0]["duration"] * 2.0
        try:
            peer.send(Command.SET_TIMING, timings=[{"index": 0, "duration": target}], time=0.0)
            timing = peer.wait_event(lambda event: event.get("event") == Event.TIMING, timeout=30)
            assert timing["trace_id"] == log["trace_id"]
            assert timing["blocks"][0]["duration"] == pytest.approx(target)
            assert timing["blocks"][1]["start"] == pytest.approx(blocks[0]["start"] + target)
            # The host must still pose and render after the retime.
            peer.send(Command.SCRUB, time=min(target / 2, timing["total_duration"]))
            peer.wait_event(
                lambda event: event.get("event") == Event.TICK and event.get("scrubbing") is True,
                timeout=30,
            )
            errors = [event for event in peer.drain() if event.get("event") == Event.ERROR]
            assert not errors, errors
        finally:
            # The host is shared for the module: always restore the base timing.
            peer.send(Command.SET_TIMING, timings=[], time=0.0)
            peer.wait_event(lambda event: event.get("event") == Event.TIMING, timeout=30)


@pytest.mark.gl
class TestHostLiveness:
    def test_host_exits_when_editor_disconnects(self) -> None:
        """The host must not outlive its editor, even after an abrupt disconnect."""
        peer = HostPeer()
        process = peer.start_host()
        try:
            peer.accept_control()
            peer.accept_frames()
            peer.wait_event(lambda event: event.get("event") == Event.LOG)
            assert peer.control is not None
            peer.control.shutdown(socket.SHUT_RDWR)  # simulate the editor process dying
            peer.control.close()
            process.wait(timeout=20)
            assert process.returncode == 0
        finally:
            if process.poll() is None:
                process.kill()
                process.wait()
            peer.close()


class _NullPump:
    """Frame pump double for in-process host tests."""

    def __init__(self) -> None:
        self.offered: list[bytes] = []
        self.frames_sent = 0
        self.frames_dropped = 0
        self.frames_deduped = 0

    def offer(self, rgba: bytes, width: int, height: int) -> None:
        from manim_studio.ipc import encode_frame

        self.offered.append(encode_frame(width, height, rgba))

    def offer_encoded(self, blob: bytes) -> None:
        self.offered.append(blob)

    def close(self) -> None:
        pass


def _frame_from_blob(blob: bytes) -> Frame:
    frames = FrameParser().feed(blob)
    assert len(frames) == 1
    return frames[0]


@pytest.mark.gl
class TestFilmstripCrawlerInProcess:
    """Deterministic crawler/invalidation tests without the socket harness."""

    BROKEN_SCENE = (
        "from manimlib import *\n\n\n"
        "class Broken(Scene):\n"
        "    def construct(self):\n"
        "        first = Circle()\n"
        "        self.play(Transform(first, second))\n"
    )

    def test_crawl_fills_cache_and_reload_invalidates(self) -> None:
        from manim_studio.preview_host import HostConfig, SceneHost

        host = SceneHost(HostConfig(scene_path=SCENE_FILE, embedded=True, size=(320, 180)))
        host._create_window()
        host.frame_pump = _NullPump()
        try:
            host.load()
            assert host._filmstrip is not None and host._filmstrip.is_empty
            host._ensure_crawler()
            assert host._crawler is not None
            # The default initial frame is the end, so the crawl starts nearest
            # the playhead (the last block) rather than from the beginning.
            assert host._crawler._order[0] == len(host.blocks) - 1
            for _ in range(5000):
                if host._crawler.done:
                    break
                host._crawler.step()
            assert host._crawler.done and not host._crawler.capped
            strip = host._filmstrip
            assert strip.frames >= 50

            # Block 0 is ShowCreation over 0.3s at 30fps: index 5 is mid-arc,
            # index 9 is the completed circle.
            partial = _frame_from_blob(strip.get(5) or strip.nearest(5, 3) or b"")
            complete = _frame_from_blob(strip.get(9) or strip.nearest(9, 3) or b"")
            assert 0 < _content_pixels(partial) < _content_pixels(complete)
            assert partial.width == 320 and partial.height == 180

            # Reloading replaces the cache immediately (freshness guarantee).
            host.load()
            assert host._filmstrip is not None and host._filmstrip.is_empty
            assert host._crawler is None
        finally:
            host._shutdown()

    def test_failed_load_keeps_last_good_state(self, tmp_path: Path) -> None:
        """A broken edit must not replace the working scene/timeline/filmstrip."""
        from manim_studio.preview_host import HostConfig, SceneHost

        broken = tmp_path / "broken.py"
        broken.write_text(self.BROKEN_SCENE, encoding="utf-8")
        host = SceneHost(HostConfig(scene_path=SCENE_FILE, embedded=True, size=(160, 90)))
        host._create_window()
        host.frame_pump = _NullPump()
        try:
            host.load()
            class_before = host.scene_class
            blocks_before = host.blocks
            strip_before = host._filmstrip
            assert class_before is not None and strip_before is not None

            with pytest.raises(NameError):
                host.load(broken)

            # Nothing was committed: class, blocks, cache and display scene intact.
            assert host.scene_class is class_before
            assert host.blocks == blocks_before
            assert host._filmstrip is strip_before
            assert host.scene is not None

            # And the host recovers on the next good load.
            host.load(SCENE_FILE)
            assert host.blocks
        finally:
            host._shutdown()


class _RaisingCrawler:
    """Stands in for a crawler whose scene fails mid-replay."""

    done = False

    def step(self) -> int:
        from manim_studio.scrub import ScrubError

        cause = ValueError("first")
        raise ScrubError("ValueError: first") from cause


class TestCrawlerGuard:
    """The idle loop must survive a scene error raised during a crawl."""

    def test_crawler_failure_disables_crawl_but_keeps_cache(self) -> None:
        from manim_studio.filmstrip import Filmstrip
        from manim_studio.preview_host import HostConfig, SceneHost

        host = SceneHost(HostConfig(scene_path=Path("unused.py")))
        filmstrip = Filmstrip()
        host._filmstrip = filmstrip
        host._crawler = _RaisingCrawler()  # type: ignore[assignment]

        host._step_crawler()  # must not raise

        assert host._crawler is None
        assert host._crawl_disabled is True
        assert host._filmstrip is filmstrip  # already-cached frames are kept
        # A disabled crawl is not restarted by the idle loop.
        host._ensure_crawler()
        assert host._crawler is None


class _CaptureControl:
    """Control-channel double that records emitted messages."""

    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []

    def send(self, message: dict[str, Any]) -> bool:
        self.sent.append(dict(message))
        return True


class TestHostStatsPayloads:
    """STATS/FILMSTRIP payloads carry RAM accounting without needing GL."""

    def _host(self) -> Any:
        from manim_studio.filmstrip import Filmstrip
        from manim_studio.preview_host import HostConfig, SceneHost

        host = SceneHost(HostConfig(scene_path=Path("unused.py")))
        strip = Filmstrip()
        strip.put(0, b"x" * 1024)
        strip.put(1, b"y" * 2048)
        host._filmstrip = strip
        pump = _NullPump()
        pump.frames_sent = 7
        pump.frames_dropped = 2
        host.frame_pump = pump  # type: ignore[assignment]
        host.control = _CaptureControl()  # type: ignore[assignment]
        return host

    def test_filmstrip_payload_reports_bytes_and_budgets(self) -> None:
        host = self._host()
        payload = host._filmstrip_payload(frames=2, done=False, capped=False)
        assert payload["frames"] == 2
        assert payload["bytes"] == 3072
        assert payload["max_bytes"] > 0
        assert payload["max_frames"] > 0
        assert payload["sent"] == 7
        assert payload["dropped"] == 2
        assert "host_rss" in payload

    def test_stats_command_emits_stats_event(self) -> None:
        from manim_studio.ipc import Command, Event

        host = self._host()
        host._handle({"cmd": Command.STATS})
        assert host.control is not None
        events = [m for m in host.control.sent if m.get("event") == Event.STATS]
        assert len(events) == 1
        payload = events[0]
        assert payload["frames"] == 2
        assert payload["bytes"] == 3072
        assert payload["sent"] == 7
        assert payload["dropped"] == 2
        assert payload["fps"] == 30
