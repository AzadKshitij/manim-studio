"""The manimlib mobject schema for the editor: a disk cache and a background probe (ADR 0012).

The probe imports manimlib, which takes a few seconds, so the editor never waits on it.  A cache
file keyed by the installed manimlib version answers at once.  On a miss, or when manimlib has
been upgraded, the probe runs in its own process (:func:`manim_studio.spawn.schema_probe_command`)
and its output is cached for the next launch.  Until a schema is ready, gears and the inspector
stay quiet.
"""

from __future__ import annotations

# Kept importable from here: the editor's tests patch the metadata lookup through this module.
import importlib.metadata  # noqa: F401
import json
from pathlib import Path

from PySide6.QtCore import QObject, QProcess, QStandardPaths, QTimer, Signal

from manim_studio.mobject_schema import MobjectSchema, SchemaError, parse_schema
from manim_studio.schema_store import (
    CACHE_NAME,
    installed_manimlib_version,
    read_cache,
    write_cache,
)
from manim_studio.spawn import schema_probe_command

__all__ = [
    "CACHE_NAME",
    "SchemaLoader",
    "installed_manimlib_version",
    "read_cache",
    "schema_cache_path",
    "write_cache",
]


def schema_cache_path() -> Path:
    """Where the schema is cached (home-dir fallback for odd platforms)."""
    try:
        location = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.CacheLocation)
        return Path(location) / CACHE_NAME
    except Exception:  # pragma: no cover - platform fallback
        return Path.home() / ".manim-studio" / CACHE_NAME


class SchemaLoader(QObject):
    """Loads the schema once: from the cache when it is current, else from a probe process."""

    ready = Signal(object)  #: a :class:`MobjectSchema`
    failed = Signal(str)  #: why no schema is available; the editor keeps working without one

    def __init__(self, parent: QObject | None = None, cache: Path | None = None) -> None:
        super().__init__(parent)
        self._cache = cache if cache is not None else schema_cache_path()
        self._process: QProcess | None = None
        self._schema: MobjectSchema | None = None

    @property
    def schema(self) -> MobjectSchema | None:
        return self._schema

    def start(self) -> None:
        cached = read_cache(self._cache, installed_manimlib_version())
        if cached is not None:
            self._schema = cached
            QTimer.singleShot(0, self._announce_cached)
            return
        self._probe()

    def _announce_cached(self) -> None:
        if self._schema is not None:
            self.ready.emit(self._schema)

    def _probe(self) -> None:
        executable, args = schema_probe_command()
        process = QProcess(self)
        process.setProcessChannelMode(QProcess.ProcessChannelMode.SeparateChannels)
        process.finished.connect(self._on_finished)
        process.errorOccurred.connect(self._on_error)
        self._process = process
        process.start(executable, list(args))

    def _on_error(self, error: QProcess.ProcessError) -> None:
        if error == QProcess.ProcessError.FailedToStart and self._process is not None:
            self._process = None
            self.failed.emit("could not start the manimlib probe")

    def _on_finished(self, exit_code: int, exit_status: QProcess.ExitStatus) -> None:
        process, self._process = self._process, None
        if process is None:
            return
        stderr = bytes(process.readAllStandardError().data()).decode("utf-8", "replace").strip()
        stdout = bytes(process.readAllStandardOutput().data()).decode("utf-8", "replace")
        process.deleteLater()
        if exit_status != QProcess.ExitStatus.NormalExit or exit_code != 0:
            detail = stderr.splitlines()[-1] if stderr else f"exit code {exit_code}"
            self.failed.emit(f"the manimlib probe failed: {detail}")
            return
        try:
            data = json.loads(stdout)
            schema = parse_schema(data)
        except (ValueError, SchemaError) as error:
            self.failed.emit(f"the manimlib probe output was not usable: {error}")
            return
        write_cache(self._cache, data)
        self._schema = schema
        self.ready.emit(schema)
