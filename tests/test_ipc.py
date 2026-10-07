"""Unit tests for the IPC protocol and frame codec."""

from __future__ import annotations

import io
import socket
import threading

import pytest

from manim_studio.ipc import (
    PROTOCOL_VERSION,
    Channel,
    Command,
    Event,
    Frame,
    FrameParser,
    JsonLineReader,
    JsonLineWriter,
    ProtocolError,
    decode_message,
    encode_frame,
    encode_message,
    hello_message,
)


class TestJsonMessages:
    def test_round_trip(self) -> None:
        message = {"cmd": Command.SEEK, "index": 3, "note": "héllo"}
        assert decode_message(encode_message(message)) == message

    def test_stats_command_and_event_exist(self) -> None:
        assert Command.STATS == "stats"
        assert Event.STATS == "stats"
        message = {"cmd": Command.STATS}
        assert decode_message(encode_message(message)) == message

    def test_encode_rejects_non_serializable(self) -> None:
        with pytest.raises(ProtocolError):
            encode_message({"bad": object()})

    @pytest.mark.parametrize(
        "line",
        [b"", b"   \n", b"{nope", b"[1,2,3]", b'"string"', b"\xff\xfe"],
    )
    def test_decode_rejects_bad_lines(self, line: bytes) -> None:
        with pytest.raises(ProtocolError):
            decode_message(line)

    def test_hello_message_shape(self) -> None:
        hello = hello_message(Channel.CONTROL, role="host")
        assert hello["event"] == Event.HELLO
        assert hello["channel"] == Channel.CONTROL
        assert hello["protocol"] == PROTOCOL_VERSION
        assert hello["role"] == "host"


class TestJsonLineStreams:
    def test_writer_reader_round_trip(self) -> None:
        buffer = io.BytesIO()
        writer = JsonLineWriter(buffer)
        for i in range(5):
            assert writer.send({"n": i})
        buffer.seek(0)
        reader = JsonLineReader(buffer)
        assert list(reader.messages()) == [{"n": i} for i in range(5)]

    def test_reader_skips_blank_lines(self) -> None:
        buffer = io.BytesIO(b'\n\n{"a":1}\n\n')
        assert list(JsonLineReader(buffer).messages()) == [{"a": 1}]

    def test_reader_ends_on_eof(self) -> None:
        assert list(JsonLineReader(io.BytesIO(b"")).messages()) == []

    def test_reader_raises_protocol_error_on_garbage(self) -> None:
        reader = JsonLineReader(io.BytesIO(b"{bad}\n"))
        with pytest.raises(ProtocolError):
            reader.read_one()

    def test_writer_returns_false_after_close(self) -> None:
        writer = JsonLineWriter(io.BytesIO())
        writer.close()
        assert writer.send({"a": 1}) is False

    def test_writer_returns_false_on_broken_stream(self) -> None:
        class Broken(io.RawIOBase):
            def write(self, data: bytes) -> int:
                raise OSError("pipe closed")

            def writable(self) -> bool:
                return True

            def flush(self) -> None:
                pass

        assert JsonLineWriter(Broken()).send({"a": 1}) is False


class TestSocketRoundTrip:
    def test_concurrent_writer_and_reader_threads(self) -> None:
        server, client = socket.socketpair()
        try:
            writer = JsonLineWriter(client.makefile("wb"))
            reader = JsonLineReader(server.makefile("rb"))
            received: list[dict] = []
            done = threading.Event()

            def pump() -> None:
                for message in reader.messages():
                    received.append(message)
                done.set()

            thread = threading.Thread(target=pump, daemon=True)
            thread.start()
            for i in range(200):
                writer.send({"n": i})
            writer.close()
            client.close()  # closing the socket ends the peer's readline
            assert done.wait(timeout=10)
            assert received == [{"n": i} for i in range(200)]
        finally:
            server.close()
            client.close()


class TestFrameCodec:
    def _rgba(self, width: int, height: int) -> bytes:
        return bytes((i * 7) % 256 for i in range(width * height * 4))

    def test_encode_and_parse_one_frame(self) -> None:
        rgba = self._rgba(16, 8)
        blob = encode_frame(16, 8, rgba)
        frames = FrameParser().feed(blob)
        assert frames == [Frame(width=16, height=8, rgba=rgba)]

    def test_byte_by_byte_feed(self) -> None:
        rgba = self._rgba(4, 4)
        parser = FrameParser()
        collected: list[Frame] = []
        for byte in encode_frame(4, 4, rgba):
            collected.extend(parser.feed(bytes([byte])))
        assert collected == [Frame(width=4, height=4, rgba=rgba)]
        assert parser.pending_bytes == 0

    def test_two_frames_in_one_burst(self) -> None:
        first, second = self._rgba(2, 2), self._rgba(3, 1)
        blob = encode_frame(2, 2, first) + encode_frame(3, 1, second)
        frames = FrameParser().feed(blob)
        assert [f.rgba for f in frames] == [first, second]

    def test_partial_payload_waits_without_error(self) -> None:
        rgba = self._rgba(8, 8)
        blob = encode_frame(8, 8, rgba)
        parser = FrameParser()
        assert parser.feed(blob[:10]) == []
        assert parser.pending_bytes == 10
        frames = parser.feed(blob[10:])
        assert frames == [Frame(width=8, height=8, rgba=rgba)]

    def test_reset_drops_pending(self) -> None:
        parser = FrameParser()
        parser.feed(encode_frame(2, 2, self._rgba(2, 2))[:12])
        parser.reset()
        assert parser.pending_bytes == 0

    def test_encode_rejects_bad_sizes(self) -> None:
        with pytest.raises(ProtocolError):
            encode_frame(0, 4, b"")
        with pytest.raises(ProtocolError):
            encode_frame(4, 4, b"too short")

    def test_parser_rejects_bad_magic(self) -> None:
        parser = FrameParser()
        with pytest.raises(ProtocolError, match="magic"):
            parser.feed(b"NOPE" + b"\x00" * 20)

    def test_parser_rejects_absurd_dimensions(self) -> None:
        import struct

        header = struct.pack("<4sIIQ", b"MFRM", 99999, 99999, 0)
        with pytest.raises(ProtocolError, match="dimensions"):
            FrameParser().feed(header)

    def test_parser_rejects_corrupt_payload(self) -> None:
        blob = bytearray(encode_frame(2, 2, self._rgba(2, 2)))
        blob[-1] ^= 0xFF
        with pytest.raises(ProtocolError, match="corrupt"):
            FrameParser().feed(bytes(blob))
