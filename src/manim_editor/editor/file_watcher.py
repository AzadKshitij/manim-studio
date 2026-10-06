"""File watching with debounce and self-save suppression.

The pure :class:`DebounceGate` holds the decision logic (injectable clock, unit
tested); :class:`FileWatcher` wires it to Qt's ``QFileSystemWatcher`` and keeps
watching the *parent directory* as well, because atomic saves replace the file
inode and would otherwise silently detach the watcher.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import QFileSystemWatcher, QObject, QTimer, Signal

__all__ = ["DebounceGate", "FileWatcher"]


class DebounceGate:
    """Decides when a burst of filesystem events has settled.

    ``note()`` records an event; ``due()`` reports whether the quiet period has
    elapsed since the last one.
    """

    def __init__(self, delay_seconds: float, clock: Callable[[], float] = time.monotonic) -> None:
        self.delay = delay_seconds
        self._clock = clock
        self._last: float | None = None

    def note(self) -> None:
        self._last = self._clock()

    def due(self) -> bool:
        if self._last is None:
            return False
        return self._clock() - self._last >= self.delay

    def reset(self) -> None:
        self._last = None

    @property
    def pending(self) -> bool:
        return self._last is not None


class FileWatcher(QObject):
    """Debounced, self-save-aware watcher for one scene file.

    ``QFileSystemWatcher`` is the fast path; a low-frequency signature poll
    (mtime + size) is the safety net.  Windows' ``ReadDirectoryChangesW`` can drop
    events (buffer overflow, network shares, editors that replace files), and a
    missed auto-reload is much worse than a 2-second worst-case latency.
    """

    file_changed = Signal(str)
    file_missing = Signal(str)

    def __init__(
        self,
        *,
        delay_ms: int = 500,
        suppress_ms: int = 750,
        poll_ms: int = 2000,
        parent: QObject | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        super().__init__(parent)
        self.delay_ms = delay_ms
        self.suppress_ms = suppress_ms
        self._clock = clock
        self._gate = DebounceGate(delay_ms / 1000.0, clock)
        self._watcher = QFileSystemWatcher(self)
        self._watcher.fileChanged.connect(self._on_fs_event)
        self._watcher.directoryChanged.connect(self._on_fs_event)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(delay_ms)
        self._timer.timeout.connect(self._fire)
        self._poll = QTimer(self)
        self._poll.setInterval(poll_ms)
        self._poll.timeout.connect(self._check_signature)
        self._path: Path | None = None
        self._signature: tuple[int, int] | None = None
        self._suppress_until = 0.0

    @property
    def path(self) -> Path | None:
        return self._path

    def watch(self, path: str | Path) -> None:
        """Start watching ``path`` (replacing any previous watch)."""
        self._detach()
        target = Path(path).resolve()
        self._path = target
        self._gate.reset()
        self._signature = self._file_signature(target)
        self._attach(target)
        self._poll.start()

    def stop(self) -> None:
        self._timer.stop()
        self._poll.stop()
        self._detach()
        self._path = None
        self._signature = None
        self._gate.reset()

    def note_save(self) -> None:
        """Call right after the editor writes the file to suppress the echo."""
        self._suppress_until = self._clock() + self.suppress_ms / 1000.0
        self._gate.reset()
        if self._path is not None:
            self._signature = self._file_signature(self._path)

    # -- internals ---------------------------------------------------------

    @staticmethod
    def _file_signature(target: Path) -> tuple[int, int] | None:
        try:
            stat = target.stat()
        except OSError:
            return None
        return (stat.st_mtime_ns, stat.st_size)

    def _check_signature(self) -> None:
        if self._path is None:
            return
        signature = self._file_signature(self._path)
        if signature == self._signature:
            return
        self._signature = signature
        if signature is None:
            # Deletion is reported by the filesystem watcher; the poll only needs to
            # notice when the file comes back or changes.
            return
        if self._clock() < self._suppress_until:
            return
        self._gate.note()
        self._timer.start(self.delay_ms)

    def _attach(self, target: Path) -> None:
        files = [str(target)] if target.exists() else []
        directories = [str(target.parent)] if target.parent.is_dir() else []
        watched = [*files, *directories]
        if watched:
            self._watcher.addPaths(watched)

    def _detach(self) -> None:
        existing = self._watcher.files() + self._watcher.directories()
        if existing:
            self._watcher.removePaths(existing)

    def _on_fs_event(self, _path: str) -> None:
        if self._path is None:
            return
        if self._clock() < self._suppress_until:
            return
        if not self._path.exists():
            self._signature = None
            # Atomic replace: keep watching the directory and re-attach the file.
            self._attach(self._path)
            if self._timer.isActive():
                self._timer.stop()
            self.file_missing.emit(str(self._path))
            return
        self._signature = self._file_signature(self._path)
        self._gate.note()
        self._timer.start(self.delay_ms)

    def _fire(self) -> None:
        if self._path is None or not self._gate.due():
            return
        self._gate.reset()
        self._attach(self._path)  # re-arm in case of atomic replace
        self._signature = self._file_signature(self._path)
        if self._path.exists():
            self.file_changed.emit(str(self._path))
