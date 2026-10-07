"""Unit tests for the frame payload codec."""

from __future__ import annotations

import random

import pytest

from manim_studio import frame_codec


def _random_bytes(size: int, seed: int = 0) -> bytes:
    return random.Random(seed).randbytes(size)


class TestSchemeNames:
    def test_known_schemes(self) -> None:
        assert frame_codec.scheme_name(frame_codec.SCHEME_ZLIB) == "zlib"
        assert frame_codec.scheme_name(frame_codec.SCHEME_ZSTD) == "zstd"
        assert frame_codec.scheme_name(frame_codec.SCHEME_RAW) == "raw"

    def test_unknown_scheme(self) -> None:
        assert frame_codec.scheme_name(99) == "unknown(99)"


class TestCompress:
    def test_compressible_uses_a_codec_and_round_trips(self) -> None:
        data = bytes(8192)
        scheme, payload = frame_codec.compress(data)
        assert scheme in (frame_codec.SCHEME_ZSTD, frame_codec.SCHEME_ZLIB)
        assert len(payload) < len(data)
        assert frame_codec.decompress(scheme, payload) == data

    def test_incompressible_falls_back_to_raw(self) -> None:
        data = _random_bytes(4096)
        scheme, payload = frame_codec.compress(data)
        assert scheme == frame_codec.SCHEME_RAW
        assert payload is data
        assert frame_codec.decompress(scheme, payload) == data

    @pytest.mark.skipif(not frame_codec.zstd_available(), reason="zstandard not installed")
    def test_zstd_is_preferred_when_available(self) -> None:
        scheme, payload = frame_codec.compress(bytes(8192))
        assert scheme == frame_codec.SCHEME_ZSTD
        assert frame_codec.decompress(frame_codec.SCHEME_ZSTD, payload) == bytes(8192)


class TestDecompress:
    def test_raw_is_identity(self) -> None:
        assert frame_codec.decompress(frame_codec.SCHEME_RAW, b"abc") == b"abc"

    def test_unknown_scheme_raises(self) -> None:
        with pytest.raises(frame_codec.CodecError, match="unknown"):
            frame_codec.decompress(7, b"whatever")

    def test_corrupt_payload_raises_codec_error(self) -> None:
        import zlib

        payload = bytearray(zlib.compress(bytes(8192)))
        payload[len(payload) // 2] ^= 0xFF  # zlib validates its adler32 trailer
        with pytest.raises(frame_codec.CodecError):
            frame_codec.decompress(frame_codec.SCHEME_ZLIB, bytes(payload))
