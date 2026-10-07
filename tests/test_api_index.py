"""Tests for the offline manimlib API index."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from manim_studio.api_index import (
    ApiSymbol,
    build_index,
    find_manimlib_path,
    index_names,
    load_index,
    save_index,
)


class TestBuildIndex:
    def test_manimlib_is_installed(self) -> None:
        path = find_manimlib_path()
        assert path is not None and path.is_dir()

    def test_contains_core_api(self) -> None:
        names = index_names(build_index())
        for expected in (
            "Circle",
            "Square",
            "ShowCreation",
            "FadeIn",
            "Transform",
            "Scene",
            "Write",
        ):
            assert expected in names, f"missing {expected}"

    def test_kinds(self) -> None:
        symbols = {symbol.name: symbol for symbol in build_index()}
        assert symbols["Circle"].kind == "class"
        assert symbols["Scene"].kind == "class"
        assert symbols["always_redraw"].kind == "function"

    def test_no_private_symbols(self) -> None:
        assert not [symbol for symbol in build_index() if symbol.name.startswith("_")]

    def test_sorted_and_unique(self) -> None:
        names = index_names(build_index())
        assert list(names) == sorted(names)
        assert len(names) == len(set(names))

    def test_missing_path_returns_empty(self, tmp_path: Path) -> None:
        assert build_index(tmp_path / "nope") == ()


class TestPersistence:
    def test_round_trip(self, tmp_path: Path) -> None:
        symbols = (
            ApiSymbol(name="Circle", kind="class", module="mobject.geometry", lineno=1),
            ApiSymbol(name="always_redraw", kind="function", module="animation.updaters", lineno=9),
        )
        path = tmp_path / "api_index.json"
        save_index(path, symbols)
        assert load_index(path) == symbols

    def test_missing_file(self, tmp_path: Path) -> None:
        assert load_index(tmp_path / "absent.json") == ()

    def test_corrupt_file(self, tmp_path: Path) -> None:
        path = tmp_path / "api_index.json"
        path.write_text("{not json", encoding="utf-8")
        assert load_index(path) == ()

    def test_wrong_version(self, tmp_path: Path) -> None:
        path = tmp_path / "api_index.json"
        path.write_text('{"version": 99, "symbols": []}', encoding="utf-8")
        assert load_index(path) == ()


class TestFrozenLookup:
    def test_prefers_bundled_sources(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        bundle = tmp_path / "_internal"
        package = bundle / "manimlib"
        package.mkdir(parents=True)
        (package / "__init__.py").write_text("", encoding="utf-8")
        monkeypatch.setattr(sys, "_MEIPASS", str(bundle), raising=False)
        assert find_manimlib_path() == package

    def test_falls_back_to_installed_package(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path / "empty_bundle"), raising=False)
        path = find_manimlib_path()
        assert path is not None and (path / "__init__.py").is_file()
