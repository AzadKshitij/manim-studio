"""schema_store: the mobject schema without Qt (cache, default location, blocking load)."""

from __future__ import annotations

import ast
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from manim_studio import schema_store
from manim_studio.schema_probe import SCHEMA_VERSION
from manim_studio.schema_store import (
    SchemaUnavailable,
    default_cache_path,
    load_schema,
    read_cache,
    write_cache,
)


def _payload(version: str = "1.7.2") -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
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


def _completed(stdout: str = "", stderr: str = "", code: int = 0) -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(
        args=["probe"], returncode=code, stdout=stdout, stderr=stderr
    )


class TestCache:
    def test_round_trips_for_the_same_version(self, tmp_path: Path) -> None:
        path = tmp_path / "schema.json"
        write_cache(path, _payload())
        schema = read_cache(path, "1.7.2")
        assert schema is not None
        assert schema.exported["Circle"] == "m.Circle"

    def test_made_for_another_version_is_a_miss(self, tmp_path: Path) -> None:
        path = tmp_path / "schema.json"
        write_cache(path, _payload("1.6.0"))
        assert read_cache(path, "1.7.2") is None

    def test_missing_or_corrupt_is_a_miss(self, tmp_path: Path) -> None:
        assert read_cache(tmp_path / "absent.json", "1.7.2") is None
        corrupt = tmp_path / "corrupt.json"
        corrupt.write_text("{not json", encoding="utf-8")
        assert read_cache(corrupt, "1.7.2") is None


class TestDefaultCachePath:
    def test_windows_uses_localappdata(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(sys, "platform", "win32")
        monkeypatch.setenv("LOCALAPPDATA", str(Path("C:/Local")))
        assert (
            default_cache_path()
            == Path("C:/Local") / "manim-studio" / "cache" / "mobject_schema.json"
        )

    def test_linux_honours_xdg_cache_home(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(sys, "platform", "linux")
        monkeypatch.setenv("XDG_CACHE_HOME", str(Path("/xdg")))
        assert default_cache_path() == Path("/xdg") / "manim-studio" / "mobject_schema.json"

    def test_linux_falls_back_to_dot_cache(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(sys, "platform", "linux")
        monkeypatch.delenv("XDG_CACHE_HOME", raising=False)
        assert default_cache_path().parts[-3:] == (".cache", "manim-studio", "mobject_schema.json")

    def test_macos_uses_library_caches(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(sys, "platform", "darwin")
        assert default_cache_path().parts[-3:] == (
            "Caches",
            "manim-studio",
            "mobject_schema.json",
        )


class TestLoadSchema:
    def test_current_cache_is_used_without_probing(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        path = tmp_path / "schema.json"
        write_cache(path, _payload())
        monkeypatch.setattr(schema_store, "installed_manimlib_version", lambda: "1.7.2")

        def no_probe(*args: Any, **kwargs: Any) -> None:
            raise AssertionError("a current cache must not run the probe")

        monkeypatch.setattr(schema_store.subprocess, "run", no_probe)
        assert load_schema(path).manimlib_version == "1.7.2"

    def test_miss_runs_the_probe_and_caches_its_output(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        path = tmp_path / "nested" / "schema.json"
        monkeypatch.setattr(schema_store, "installed_manimlib_version", lambda: "1.7.2")
        calls: list[dict[str, Any]] = []

        def fake_run(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess:
            calls.append({"argv": argv, **kwargs})
            return _completed(stdout=json.dumps(_payload()))

        monkeypatch.setattr(schema_store.subprocess, "run", fake_run)
        schema = load_schema(path)
        assert schema.exported["Circle"] == "m.Circle"
        assert len(calls) == 1
        assert calls[0]["timeout"] == schema_store._DEFAULT_TIMEOUT
        assert calls[0]["stdin"] == subprocess.DEVNULL
        assert read_cache(path, "1.7.2") is not None

    def test_probe_failure_is_unavailable_with_its_last_line(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(schema_store, "installed_manimlib_version", lambda: "1.7.2")
        err = "Traceback (most recent call last):\nImportError: boom\n"
        monkeypatch.setattr(
            schema_store.subprocess, "run", lambda *a, **k: _completed(stderr=err, code=1)
        )
        with pytest.raises(SchemaUnavailable, match="ImportError: boom"):
            load_schema(tmp_path / "schema.json")

    def test_output_that_is_not_a_schema_is_unavailable(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(schema_store, "installed_manimlib_version", lambda: "1.7.2")
        monkeypatch.setattr(schema_store.subprocess, "run", lambda *a, **k: _completed("not json"))
        with pytest.raises(SchemaUnavailable, match="not usable"):
            load_schema(tmp_path / "schema.json")
        assert read_cache(tmp_path / "schema.json", "1.7.2") is None

    def test_timeout_is_unavailable(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(schema_store, "installed_manimlib_version", lambda: "1.7.2")

        def slow(*args: Any, **kwargs: Any) -> None:
            raise subprocess.TimeoutExpired(cmd="probe", timeout=kwargs["timeout"])

        monkeypatch.setattr(schema_store.subprocess, "run", slow)
        with pytest.raises(SchemaUnavailable, match="timed out"):
            load_schema(tmp_path / "schema.json", timeout=5)

    def test_probe_that_cannot_start_is_unavailable(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(schema_store, "installed_manimlib_version", lambda: "1.7.2")

        def missing(*args: Any, **kwargs: Any) -> None:
            raise FileNotFoundError("no python")

        monkeypatch.setattr(schema_store.subprocess, "run", missing)
        with pytest.raises(SchemaUnavailable, match="could not start"):
            load_schema(tmp_path / "schema.json")


class TestWithoutQt:
    def test_module_imports_neither_pyside6_nor_manimlib(self) -> None:
        tree = ast.parse(Path(schema_store.__file__).read_text(encoding="utf-8"))
        imported: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module)
        forbidden = {name for name in imported if name.split(".")[0] in {"PySide6", "manimlib"}}
        assert forbidden == set()
