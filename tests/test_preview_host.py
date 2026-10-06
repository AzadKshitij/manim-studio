"""Tests for the preview host: pure components + real end-to-end GL integration."""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import queue
import socket
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

import pytest

from manim_editor.ipc import (
    Channel,
    Command,
    Event,
    Frame,
    FrameParser,
    JsonLineWriter,
    decode_message,
)
from manim_editor.preview_host import FramePump, TickThrottler, parse_size
from tests.helpers import FakeScene

SCENE_FILE = Path(__file__).parent / "scenes" / "feature_showcase.py"
REPO_ROOT = Path(__file__).parent.parent


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


class TestSceneShortcuts:
    """The live shell must expose the manimgl-style scene API, offline-testable."""

    def _host(self):
        from manim_editor.preview_host import HostConfig, SceneHost

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
        from manim_editor.preview_host import HostConfig, SceneHost

        host = SceneHost(HostConfig(scene_path=Path("unused.py")))
        assert host._scene_shortcuts() == {}
        host._clear_checkpoints()  # no-op without checkpoints

    def test_embed_is_neutralized(self) -> None:
        """scene.embed() would launch a blocking terminal inside the host."""
        from manim_editor.preview_host import HostConfig, SceneHost

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


class HostPeer:
    """A minimal editor-side peer: accepts control + frame channels and collects events."""

    def __init__(self) -> None:
        self.server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.server.bind(("127.0.0.1", 0))
        self.server.listen(4)
        self.port = int(self.server.getsockname()[1])
        self.control: socket.socket | None = None
        self.writer: JsonLineWriter | None = None
        self.events: queue.Queue[dict[str, Any]] = queue.Queue()
        self.frames: queue.Queue[Frame] = queue.Queue()
        self.seen: list[dict[str, Any]] = []
        self._threads: list[threading.Thread] = []

    def accept_control(self, timeout: float = 60.0) -> dict[str, Any]:
        self.server.settimeout(timeout)
        sock, _ = self.server.accept()
        sock.settimeout(None)
        self.control = sock
        reader = sock.makefile("rb")
        first = decode_message(reader.readline())
        assert first["channel"] == Channel.CONTROL
        self.writer = JsonLineWriter(sock.makefile("wb"))
        thread = threading.Thread(target=self._pump_control, args=(reader,), daemon=True)
        thread.start()
        self._threads.append(thread)
        return first

    def accept_frames(self, timeout: float = 60.0) -> dict[str, Any]:
        self.server.settimeout(timeout)
        sock, _ = self.server.accept()
        sock.settimeout(None)
        reader = sock.makefile("rb")
        first = decode_message(reader.readline())
        assert first["channel"] == Channel.FRAMES
        thread = threading.Thread(target=self._pump_frames, args=(sock,), daemon=True)
        thread.start()
        self._threads.append(thread)
        return first

    def _pump_control(self, reader: Any) -> None:
        try:
            for line in reader:
                if not line.strip():
                    continue
                self.events.put(json.loads(line.decode("utf-8")))
        except (OSError, ValueError):
            return

    def _pump_frames(self, sock: socket.socket) -> None:
        parser = FrameParser()
        try:
            while True:
                data = sock.recv(1 << 20)
                if not data:
                    return
                for frame in parser.feed(data):
                    self.frames.put(frame)
        except OSError:
            return

    def send(self, command: str, **payload: Any) -> None:
        assert self.writer is not None
        assert self.writer.send({"cmd": command, **payload})

    def wait_event(self, predicate: Any, timeout: float = 90.0) -> dict[str, Any]:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            remaining = deadline - time.monotonic()
            try:
                event = self.events.get(timeout=min(0.5, max(remaining, 0.01)))
            except queue.Empty:
                continue
            if predicate(event):
                return event
            self.seen.append(event)
        raise TimeoutError("expected event never arrived")

    def drain(self) -> list[dict[str, Any]]:
        """Move every queued event into ``seen`` and return them."""
        while True:
            try:
                self.seen.append(self.events.get_nowait())
            except queue.Empty:
                return self.seen

    def wait_frame(self, timeout: float = 60.0) -> Frame:
        try:
            return self.frames.get(timeout=timeout)
        except queue.Empty:
            raise TimeoutError("no frame received") from None

    def start_host(self, *extra: str) -> subprocess.Popen[bytes]:
        # Kept open for the process lifetime so host stderr can be inspected on
        # failure; a context manager would close it before the child starts.
        self.stderr_log = tempfile.TemporaryFile()  # noqa: SIM115
        process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "manim_editor.preview_host",
                str(SCENE_FILE),
                "--port",
                str(self.port),
                "--embedded",
                "--size",
                "320x180",
                "--scene-name",
                "FeatureShowcase",
                "--log-level",
                "INFO",
                *extra,
            ],
            cwd=REPO_ROOT,
            stdout=subprocess.DEVNULL,
            stderr=self.stderr_log,
            env={**os.environ, "PYTHONIOENCODING": "utf-8"},
        )
        return process

    def host_stderr(self) -> str:
        self.stderr_log.seek(0)
        return self.stderr_log.read().decode("utf-8", errors="replace")

    def close(self) -> None:
        if self.control is not None:
            with contextlib.suppress(OSError):
                self.control.shutdown(socket.SHUT_RDWR)
            with contextlib.suppress(OSError):
                self.control.close()
        self.server.close()


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
        _, _, control_hello, frames_hello, _ = host_session
        assert control_hello["role"] == "host"
        assert control_hello["protocol"] == 1
        assert control_hello["embedded"] is True
        assert control_hello["size"] == [320, 180]
        assert frames_hello["role"] == "host"

    def test_trace_event_has_blocks_and_stats(self, host_session: Any) -> None:
        _, _, _, _, log_event = host_session
        assert len(log_event["blocks"]) >= 12
        assert log_event["stats"]["play"] >= 10
        assert log_event["stats"]["undo"] == 1
        assert [b["index"] for b in log_event["blocks"]] == list(range(len(log_event["blocks"])))

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
