"""RGBA frame payload codec used by the preview protocol.

The host compresses every streamed frame and the editor decompresses it.  zstd
level 1 encodes ~6x faster than zlib-1 at a ~3x better ratio (see ``docs/perf``),
so it is the default; zlib is the fallback when the optional ``zstandard``
extension is missing, and a payload compression cannot shrink is sent raw.

``zstandard`` is imported lazily inside the codec functions, so this module (and
``ipc``, which imports it) pulls in no third-party code until a frame is coded.
"""

from __future__ import annotations

import threading
import zlib

__all__ = [
    "SCHEME_RAW",
    "SCHEME_ZLIB",
    "SCHEME_ZSTD",
    "CodecError",
    "compress",
    "decompress",
    "scheme_name",
    "zstd_available",
]

SCHEME_ZLIB = 0
SCHEME_ZSTD = 1
SCHEME_RAW = 2

_NAMES = {SCHEME_ZLIB: "zlib", SCHEME_ZSTD: "zstd", SCHEME_RAW: "raw"}
# zstd compression contexts are not safe to share between threads, and the host
# encodes on both the pump thread and the idle/crawl thread, so cache per thread.
_local = threading.local()


class CodecError(ValueError):
    """A frame payload could not be decoded."""


def scheme_name(scheme: int) -> str:
    """Human-readable name for a scheme byte (for diagnostics)."""
    return _NAMES.get(scheme, f"unknown({scheme})")


def zstd_available() -> bool:
    """True when the optional ``zstandard`` extension can be imported."""
    try:
        import zstandard  # noqa: F401
    except ImportError:
        return False
    return True


def _zstd_compress(data: bytes, level: int) -> bytes:
    import zstandard

    cache = getattr(_local, "compressors", None)
    if cache is None:
        cache = {}
        _local.compressors = cache
    compressor = cache.get(level)
    if compressor is None:
        compressor = zstandard.ZstdCompressor(level=level)
        cache[level] = compressor
    return compressor.compress(data)


def compress(data: bytes, *, level: int = 1) -> tuple[int, bytes]:
    """Return ``(scheme, payload)`` for one frame, preferring zstd, then zlib."""
    try:
        payload = _zstd_compress(data, level)
    except ImportError:
        payload = zlib.compress(data, level)
        scheme = SCHEME_ZLIB
    else:
        scheme = SCHEME_ZSTD
    if len(payload) >= len(data):
        return SCHEME_RAW, data
    return scheme, payload


def decompress(scheme: int, payload: bytes) -> bytes:
    """Decode a payload produced by :func:`compress`; raises :class:`CodecError`."""
    if scheme == SCHEME_RAW:
        return payload
    try:
        if scheme == SCHEME_ZLIB:
            return zlib.decompress(payload)
        if scheme == SCHEME_ZSTD:
            import zstandard

            return zstandard.ZstdDecompressor().decompress(payload)
    except Exception as exc:  # zlib.error / zstandard.ZstdError
        raise CodecError(f"{scheme_name(scheme)} decode failed: {exc}") from exc
    raise CodecError(f"unknown frame codec scheme {scheme}")
