"""The editor's schema cache (ADR 0012): valid only for the installed manimlib, never blocking."""

from __future__ import annotations

import importlib.metadata
import json
from pathlib import Path
from typing import Any

import pytest
from PySide6.QtCore import QProcess
from pytestqt.qtbot import QtBot

from manim_studio.editor import schema_cache
from manim_studio.editor.schema_cache import SchemaLoader, read_cache, write_cache


def _payload(version: str = "1.7.2") -> dict[str, Any]:
    return {
        "schema_version": 1,
        "manimlib_version": version,
        "exported": {"Circle": "m.Circle"},
        "colours": {"BLUE": "#58C4DD"},
        "classes": {
            "m.Circle": {
                "name": "Circle",
                "module": "m",
                "mro": ["m.Circle"],
                "params": [],
                "methods": [],
            }
        },
    }


def test_cache_round_trips_for_the_same_version(tmp_path: Path) -> None:
    path = tmp_path / "schema.json"
    write_cache(path, _payload())
    schema = read_cache(path, "1.7.2")
    assert schema is not None
    assert schema.exported["Circle"] == "m.Circle"


def test_cache_made_for_another_manimlib_is_a_miss(tmp_path: Path) -> None:
    path = tmp_path / "schema.json"
    write_cache(path, _payload("1.6.0"))
    assert read_cache(path, "1.7.2") is None


def test_missing_or_corrupt_cache_is_a_miss(tmp_path: Path) -> None:
    assert read_cache(tmp_path / "absent.json", "1.7.2") is None
    corrupt = tmp_path / "corrupt.json"
    corrupt.write_text("{not json", encoding="utf-8")
    assert read_cache(corrupt, "1.7.2") is None


def test_a_wrong_schema_version_is_a_miss(tmp_path: Path) -> None:
    path = tmp_path / "schema.json"
    payload = _payload()
    payload["schema_version"] = 99
    path.write_text(json.dumps(payload), encoding="utf-8")
    assert read_cache(path, "1.7.2") is None


def test_loader_announces_a_current_cache_without_probing(
    qtbot: QtBot, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "schema.json"
    write_cache(path, _payload())
    monkeypatch.setattr(schema_cache, "installed_manimlib_version", lambda: "1.7.2")

    def no_probe() -> None:
        raise AssertionError("a current cache must not start the probe")

    loader = SchemaLoader(cache=path)
    monkeypatch.setattr(loader, "_probe", no_probe)
    with qtbot.waitSignal(loader.ready, timeout=2000) as blocker:
        loader.start()
    assert blocker.args[0].manimlib_version == "1.7.2"
    assert loader.schema is not None


def test_missing_distribution_metadata_reads_as_unknown(monkeypatch: pytest.MonkeyPatch) -> None:
    # A frozen build may drop the metadata; the probe then decides, so this must not fail.
    def missing(_name: str) -> str:
        raise importlib.metadata.PackageNotFoundError(_name)

    monkeypatch.setattr(schema_cache.importlib.metadata, "version", missing)
    assert schema_cache.installed_manimlib_version() == "unknown"


class _Output:
    """What ``QProcess.readAll...`` returns: a QByteArray, read through ``data()``."""

    def __init__(self, raw: bytes) -> None:
        self._raw = raw

    def data(self) -> bytes:
        return self._raw


class _FakeProcess:
    """Stands in for the probe's QProcess: only what the finished handler reads."""

    def __init__(self, out: bytes, err: bytes = b"") -> None:
        self._out = _Output(out)
        self._err = _Output(err)

    def readAllStandardOutput(self) -> _Output:
        return self._out

    def readAllStandardError(self) -> _Output:
        return self._err

    def deleteLater(self) -> None:
        pass


def test_probe_output_is_cached_and_announced(
    qtbot: QtBot, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "schema.json"
    monkeypatch.setattr(schema_cache, "installed_manimlib_version", lambda: "1.7.2")
    loader = SchemaLoader(cache=path)
    loader._process = _FakeProcess(json.dumps(_payload()).encode("utf-8") + b"\n")  # type: ignore[assignment]
    with qtbot.waitSignal(loader.ready, timeout=2000):
        loader._on_finished(0, QProcess.ExitStatus.NormalExit)
    assert read_cache(path, "1.7.2") is not None


def test_a_failed_probe_reports_its_last_error_line(
    qtbot: QtBot, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(schema_cache, "installed_manimlib_version", lambda: "1.7.2")
    loader = SchemaLoader(cache=tmp_path / "schema.json")
    err = b"Traceback (most recent call last):\nImportError: boom\n"
    loader._process = _FakeProcess(b"", err)  # type: ignore[assignment]
    with qtbot.waitSignal(loader.failed, timeout=2000) as blocker:
        loader._on_finished(1, QProcess.ExitStatus.NormalExit)
    assert blocker.args[0].endswith("ImportError: boom")


def test_probe_output_that_is_not_a_schema_is_reported(
    qtbot: QtBot, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(schema_cache, "installed_manimlib_version", lambda: "1.7.2")
    loader = SchemaLoader(cache=tmp_path / "schema.json")
    loader._process = _FakeProcess(b"not json")  # type: ignore[assignment]
    with qtbot.waitSignal(loader.failed, timeout=2000) as blocker:
        loader._on_finished(0, QProcess.ExitStatus.NormalExit)
    assert "not usable" in blocker.args[0]
    assert read_cache(tmp_path / "schema.json", "1.7.2") is None
