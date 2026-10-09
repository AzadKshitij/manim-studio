"""Spawned preview-host peer used by the integration and performance tests.

``HostPeer`` speaks just enough of the protocol to act as the editor side:
it accepts the control and frame connections, parses JSON-line events and
whole frames, and offers blocking ``wait_event`` / ``wait_frame`` helpers.
"""

from __future__ import annotations

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

from manim_studio.ipc import (
    Channel,
    Frame,
    FrameParser,
    JsonLineWriter,
    decode_message,
)

SCENE_FILE = Path(__file__).parent / "scenes" / "feature_showcase.py"
REPO_ROOT = Path(__file__).parent.parent

__all__ = ["REPO_ROOT", "SCENE_FILE", "HostPeer"]


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

    def start_host(self, *extra: str, scene_file: Path | None = None) -> subprocess.Popen[bytes]:
        # Kept open for the process lifetime so host stderr can be inspected on
        # failure; a context manager would close it before the child starts.
        self.stderr_log = tempfile.TemporaryFile()  # noqa: SIM115
        process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "manim_studio.preview_host",
                str(scene_file or SCENE_FILE),
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
