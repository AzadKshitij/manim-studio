"""Wire protocol shared by the editor and the preview host.

Two channels are used, both plain loopback TCP:

* **control** — newline-delimited JSON objects, both directions.
* **frames** — one-directional host → editor stream of length-prefixed, zlib-compressed
  RGBA frames (:func:`encode_frame` / :class:`FrameParser`).

Every message on a freshly accepted connection starts with a ``hello`` event that
declares the channel, so a single listening socket can accept both connections.

This module is pure stdlib and fully unit-tested.
"""

from __future__ import annotations

import json
import struct
import threading
import zlib
from contextlib import suppress
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Final

if TYPE_CHECKING:
    from collections.abc import Iterator, Mapping
    from typing import BinaryIO

__all__ = [
    "MAX_FRAME_PAYLOAD",
    "PROTOCOL_VERSION",
    "Channel",
    "Command",
    "Event",
    "Frame",
    "FrameParser",
    "JsonLineReader",
    "JsonLineWriter",
    "ProtocolError",
    "as_bytes",
    "decode_message",
    "encode_frame",
    "encode_message",
    "hello_message",
]

PROTOCOL_VERSION: Final = 1

#: Refuse absurd frames (defensive upper bound: 4096x4096 RGBA).
MAX_FRAME_PAYLOAD: Final = 4096 * 4096 * 4

FRAME_MAGIC: Final = b"MFRM"
_FRAME_HEADER: Final = struct.Struct("<4sIIQ")


class ProtocolError(Exception):
    """Raised on malformed or out-of-order protocol data."""


class Channel:
    """Names of the two channels used by the protocol."""

    CONTROL: Final = "control"
    FRAMES: Final = "frames"


class Command:
    """Commands sent editor → host over the control channel."""

    LOAD: Final = "load"
    RELOAD: Final = "reload"
    PLAY: Final = "play"
    STOP: Final = "stop"
    SEEK: Final = "seek"
    RESIZE: Final = "resize"
    EXEC: Final = "exec"
    PING: Final = "ping"
    QUIT: Final = "quit"


class Event:
    """Events sent host → editor over the control channel."""

    HELLO: Final = "hello"
    LOG: Final = "log"
    STATUS: Final = "status"
    TICK: Final = "tick"
    STOPPED: Final = "stopped"
    ERROR: Final = "error"
    EXEC_RESULT: Final = "exec_result"
    PONG: Final = "pong"


# ---------------------------------------------------------------------------
# JSON line messages
# ---------------------------------------------------------------------------


def encode_message(message: Mapping[str, Any]) -> bytes:
    """Serialize one protocol message to a newline-terminated UTF-8 blob."""
    try:
        text = json.dumps(message, separators=(",", ":"), ensure_ascii=True)
    except (TypeError, ValueError) as exc:
        raise ProtocolError(f"message is not JSON-serializable: {exc}") from exc
    return text.encode("utf-8") + b"\n"


def as_bytes(value: Any) -> bytes:
    """Normalise Qt-style byte containers (``QByteArray``) and buffers to ``bytes``."""
    data = value.data() if hasattr(value, "data") else value
    if isinstance(data, memoryview):
        return data.tobytes()
    return bytes(data)


def decode_message(line: bytes | str) -> dict[str, Any]:
    """Parse one protocol line, enforcing a JSON object at the top level."""
    if isinstance(line, bytes):
        try:
            line = line.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ProtocolError("message is not valid UTF-8") from exc
    stripped = line.strip()
    if not stripped:
        raise ProtocolError("empty message")
    try:
        payload = json.loads(stripped)
    except json.JSONDecodeError as exc:
        raise ProtocolError(f"invalid JSON message: {exc}") from exc
    if not isinstance(payload, dict):
        raise ProtocolError(f"message must be a JSON object, got {type(payload).__name__}")
    return payload


def hello_message(channel: str, **extra: Any) -> dict[str, Any]:
    """Build the mandatory first message of a connection."""
    return {
        "event": Event.HELLO,
        "channel": channel,
        "protocol": PROTOCOL_VERSION,
        **extra,
    }


class JsonLineWriter:
    """Thread-safe JSON-lines writer over a binary stream (socket ``makefile``)."""

    def __init__(self, stream: BinaryIO) -> None:
        self._stream = stream
        self._lock = threading.Lock()
        self._closed = False

    @property
    def closed(self) -> bool:
        return self._closed

    def send(self, message: Mapping[str, Any]) -> bool:
        """Write one message; returns False if the stream is closed/broken."""
        data = encode_message(message)
        with self._lock:
            if self._closed:
                return False
            try:
                self._stream.write(data)
                self._stream.flush()
            except (OSError, ValueError):
                self._closed = True
                return False
        return True

    def close(self) -> None:
        with self._lock:
            self._closed = True
            with suppress(OSError):
                self._stream.close()


class JsonLineReader:
    """Blocking JSON-lines reader; iterate with :meth:`messages` in a worker thread."""

    def __init__(self, stream: BinaryIO) -> None:
        self._stream = stream

    def read_one(self) -> dict[str, Any] | None:
        """Read one message or return ``None`` at end of stream."""
        while True:
            line = self._stream.readline()
            if not line:
                return None
            if not line.strip():
                continue
            return decode_message(line)

    def messages(self) -> Iterator[dict[str, Any]]:
        """Yield messages until EOF or a broken stream; raises :class:`ProtocolError`."""
        while True:
            try:
                message = self.read_one()
            except (OSError, ValueError):
                return
            if message is None:
                return
            yield message


# ---------------------------------------------------------------------------
# Binary frame codec (host → editor)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Frame:
    """One decoded preview frame with raw RGBA pixels."""

    width: int
    height: int
    rgba: bytes

    @property
    def byte_count(self) -> int:
        return len(self.rgba)


def encode_frame(width: int, height: int, rgba: bytes, *, level: int = 1) -> bytes:
    """Length-prefix and zlib-compress one RGBA frame."""
    if width <= 0 or height <= 0:
        raise ProtocolError(f"invalid frame size {width}x{height}")
    expected = width * height * 4
    if len(rgba) != expected:
        raise ProtocolError(f"frame payload is {len(rgba)} bytes, expected {expected}")
    if expected > MAX_FRAME_PAYLOAD:
        raise ProtocolError(f"frame too large: {width}x{height}")
    payload = zlib.compress(rgba, level)
    return _FRAME_HEADER.pack(FRAME_MAGIC, width, height, len(payload)) + payload


class FrameParser:
    """Incremental parser: feed arbitrary chunks, receive whole frames.

    TCP delivers arbitrary segmentation, so the editor feeds every received byte
    burst here and only ever sees complete, decompressed frames.
    """

    def __init__(self) -> None:
        self._buffer = bytearray()

    @property
    def pending_bytes(self) -> int:
        return len(self._buffer)

    def reset(self) -> None:
        self._buffer.clear()

    def feed(self, data: bytes) -> list[Frame]:
        """Append ``data`` and return every complete frame it completed."""
        if data:
            self._buffer.extend(data)
        frames: list[Frame] = []
        while True:
            if len(self._buffer) < _FRAME_HEADER.size:
                break
            magic, width, height, payload_len = _FRAME_HEADER.unpack_from(self._buffer)
            if magic != FRAME_MAGIC:
                raise ProtocolError(f"bad frame magic {magic!r}")
            if width <= 0 or height <= 0 or width * height * 4 > MAX_FRAME_PAYLOAD:
                raise ProtocolError(f"invalid frame dimensions {width}x{height}")
            if payload_len > MAX_FRAME_PAYLOAD:
                raise ProtocolError(f"frame payload too large: {payload_len} bytes")
            end = _FRAME_HEADER.size + payload_len
            if len(self._buffer) < end:
                break
            payload = bytes(self._buffer[_FRAME_HEADER.size : end])
            del self._buffer[:end]
            try:
                rgba = zlib.decompress(payload)
            except zlib.error as exc:
                raise ProtocolError(f"corrupt frame payload: {exc}") from exc
            if len(rgba) != width * height * 4:
                raise ProtocolError(
                    f"decompressed frame is {len(rgba)} bytes, expected {width * height * 4}"
                )
            frames.append(Frame(width=width, height=height, rgba=rgba))
        return frames
