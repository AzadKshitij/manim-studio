"""App memory and cache stats helpers.

Pure stdlib (``psutil`` is used opportunistically when installed, but never
required) so the formatting and RSS probing can be unit-tested anywhere.
Both the preview host and the editor process call :func:`process_rss_bytes`
locally and exchange the numbers over the existing control channel.
"""

from __future__ import annotations

__all__ = ["cache_summary", "format_bytes", "process_rss_bytes", "usage_percent"]


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

        kernel32 = ctypes.windll.kernel32
        psapi = ctypes.windll.psapi
        process = kernel32.GetCurrentProcess()
        counters = _Counters()
        counters.cb = ctypes.sizeof(counters)
        ok = psapi.GetProcessMemoryInfo(process, ctypes.byref(counters), counters.cb)
        return int(counters.WorkingSetSize) if ok else None
    except Exception:
        return None
