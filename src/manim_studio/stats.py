"""App memory and cache stats helpers.

Pure stdlib (``psutil`` is used opportunistically when installed, but never
required) so the formatting and RSS probing can be unit-tested anywhere.
Both the preview host and the editor process call :func:`process_rss_bytes`
locally and exchange the numbers over the existing control channel.
"""

from __future__ import annotations

import contextlib
import math
import threading
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterator, Mapping

__all__ = [
    "TimingStats",
    "cache_summary",
    "format_bytes",
    "format_timings",
    "process_rss_bytes",
    "usage_percent",
]


def format_bytes(value: int | float | None) -> str:
    """Format a byte count as ``12.3 MiB``; ``None``/negative renders as ``—``."""
    if value is None:
        return "—"
    try:
        count = float(value)
    except (TypeError, ValueError):
        return "—"
    if count < 0:
        return "—"
    if count < 1024:
        return f"{count:.0f} B"
    units = ("KiB", "MiB", "GiB", "TiB")
    scaled = count / 1024.0
    for unit in units:
        if scaled < 1024 or unit == units[-1]:
            return f"{scaled:.1f} {unit}"
        scaled /= 1024.0
    return f"{scaled:.1f} TiB"  # pragma: no cover - unreachable


def usage_percent(used: int | float | None, total: int | float | None) -> float | None:
    """Share of ``total`` consumed by ``used`` in [0, 100], or ``None``."""
    if used is None or total is None:
        return None
    try:
        total_value = float(total)
        used_value = float(used)
    except (TypeError, ValueError):
        return None
    if total_value <= 0 or used_value < 0:
        return None
    return min(max(used_value / total_value * 100.0, 0.0), 100.0)


def cache_summary(
    frames: int | None,
    byte_size: int | None,
    max_bytes: int | None,
    max_frames: int | None = None,
) -> str:
    """One-line filmstrip summary, e.g. ``312 frames · 12.3 MiB / 256.0 MiB (5%)``."""
    frame_text = str(int(frames)) if frames is not None and frames >= 0 else "—"
    used_text = format_bytes(byte_size)
    if max_bytes and max_bytes > 0:
        percent = usage_percent(byte_size, max_bytes)
        percent_text = f" ({percent:.0f}%)" if percent is not None else ""
        summary = f"{frame_text} frames · {used_text} / {format_bytes(max_bytes)}{percent_text}"
    else:
        summary = f"{frame_text} frames · {used_text}"
    if max_frames and max_frames > 0 and frames is not None and frames >= 0:
        summary += f" · cap {max_frames} frames"
    return summary


class TimingStats:
    """Thread-safe EWMA + max aggregator for named phase timings.

    Phases are recorded as wall-clock seconds (``record``) or timed with the
    :meth:`phase` context manager.  :meth:`snapshot` reports millisecond averages
    so the editor can show where a frame's budget goes.  The preview host records
    from its GL thread and its pump thread, so every mutation is lock-protected.
    """

    def __init__(self, *, alpha: float = 0.2) -> None:
        if not 0.0 < alpha <= 1.0:
            raise ValueError("alpha must be in (0, 1]")
        self._alpha = float(alpha)
        self._lock = threading.Lock()
        self._entries: dict[str, list[float]] = {}

    def record(self, name: str, seconds: float) -> None:
        """Fold one duration into ``name``; non-finite/negative values are ignored."""
        if not name or not math.isfinite(seconds) or seconds < 0:
            return
        millis = seconds * 1000.0
        with self._lock:
            entry = self._entries.get(name)
            if entry is None:
                self._entries[name] = [millis, millis, 1.0]
                return
            entry[0] += (millis - entry[0]) * self._alpha
            entry[1] = max(entry[1], millis)
            entry[2] += 1.0

    @contextlib.contextmanager
    def phase(self, name: str) -> Iterator[None]:
        """Time the enclosed block and record it under ``name``."""
        started = time.perf_counter()
        try:
            yield
        finally:
            self.record(name, time.perf_counter() - started)

    def snapshot(self) -> dict[str, dict[str, float]]:
        """Millisecond avg/max/count per phase, ordered by name."""
        with self._lock:
            return {
                name: {
                    "avg_ms": round(entry[0], 3),
                    "max_ms": round(entry[1], 3),
                    "count": int(entry[2]),
                }
                for name, entry in sorted(self._entries.items())
            }

    def reset(self) -> None:
        with self._lock:
            self._entries.clear()


def format_timings(snapshot: Mapping[str, Mapping[str, float]]) -> str:
    """One line per phase, e.g. ``render: 3.1 ms (max 7.2, n=120)``."""
    lines: list[str] = []
    for name, entry in snapshot.items():
        average = float(entry.get("avg_ms", 0.0))
        peak = float(entry.get("max_ms", 0.0))
        count = int(entry.get("count", 0))
        lines.append(f"{name}: {average:.1f} ms (max {peak:.1f}, n={count})")
    return "\n".join(lines)


def process_rss_bytes() -> int | None:
    """Current process RSS in bytes, or ``None`` when it cannot be determined.

    Prefers ``psutil`` when installed; otherwise reads ``/proc/self/status``
    on Linux, ``GetProcessMemoryInfo`` on Windows, and finally falls back to
    ``resource.getrusage`` peak RSS (better than nothing for a stats readout).
    Never raises.
    """

    import os
    import sys
    from typing import Any

    try:
        import importlib

        psutil: Any = importlib.import_module("psutil")
        return int(psutil.Process().memory_info().rss)
    except Exception:
        pass
    if sys.platform.startswith("linux"):
        try:
            with open("/proc/self/status", encoding="utf-8") as handle:
                for line in handle:
                    if line.startswith("VmRSS:"):
                        parts = line.split()
                        return int(parts[1]) * 1024
        except Exception:
            pass
    if os.name == "nt":
        value = _windows_rss()
        if value is not None:
            return value
    try:
        import importlib

        resource: Any = importlib.import_module("resource")
        peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        return int(peak * 1024) if sys.platform != "darwin" else int(peak)
    except Exception:
        return None


def _windows_rss() -> int | None:
    """RSS via Win32 ``GetProcessMemoryInfo``; ``None`` on any failure."""
    try:
        import ctypes

        class _Counters(ctypes.Structure):
            _fields_ = [
                ("cb", ctypes.c_ulong),
                ("PageFaultCount", ctypes.c_ulong),
                ("PeakWorkingSetSize", ctypes.c_size_t),
                ("WorkingSetSize", ctypes.c_size_t),
                ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                ("PagefileUsage", ctypes.c_size_t),
                ("PeakPagefileUsage", ctypes.c_size_t),
            ]

        windll = getattr(ctypes, "windll", None)  # Windows-only; absent elsewhere
        if windll is None:
            return None
        kernel32 = windll.kernel32
        psapi = windll.psapi
        process = kernel32.GetCurrentProcess()
        counters = _Counters()
        counters.cb = ctypes.sizeof(counters)
        ok = psapi.GetProcessMemoryInfo(process, ctypes.byref(counters), counters.cb)
        return int(counters.WorkingSetSize) if ok else None
    except Exception:
        return None
