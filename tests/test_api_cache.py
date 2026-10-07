"""Tests for the editor-side API index cache."""

from __future__ import annotations

from pathlib import Path

import pytest

from manim_editor.api_index import ApiSymbol, load_index, save_index
from manim_editor.editor import api_cache


class TestApiCachePath:
    def test_cache_file_name(self) -> None:
        assert api_cache.api_cache_path().name == "api_index.json"


class TestLoadApiNames:
    def test_valid_cache_is_used_without_rebuild(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        cache = tmp_path / "api_index.json"
        save_index(cache, (ApiSymbol(name="Circle", kind="class", module="m", lineno=1),))
        monkeypatch.setattr(api_cache, "api_cache_path", lambda: cache)

        def _fail() -> None:
            raise AssertionError("build_index must not run when the cache is valid")

        monkeypatch.setattr(api_cache, "build_index", _fail)
        assert api_cache.load_api_names() == ("Circle",)

    def test_cache_miss_rebuilds_and_persists(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        cache = tmp_path / "api_index.json"
        monkeypatch.setattr(api_cache, "api_cache_path", lambda: cache)
        symbols = (ApiSymbol(name="Square", kind="class", module="m", lineno=2),)
        monkeypatch.setattr(api_cache, "build_index", lambda: symbols)
        assert api_cache.load_api_names() == ("Square",)
        assert load_index(cache) == symbols

    def test_empty_rebuild_is_not_persisted(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        cache = tmp_path / "api_index.json"
        monkeypatch.setattr(api_cache, "api_cache_path", lambda: cache)
        monkeypatch.setattr(api_cache, "build_index", lambda: ())
        assert api_cache.load_api_names() == ()
        assert not cache.exists()
