"""Tests for the runtime application icon asset and loader."""

from __future__ import annotations

import ctypes
import sys
from pathlib import Path

from manim_editor.editor import app as app_module
from manim_editor.editor.icon import ICON_FILENAME, _pick_icon, icon_path, load_icon


def test_icon_asset_is_packaged() -> None:
    path = icon_path()
    assert path is not None
    assert path.suffix == ".ico"
    assert path.parent.name == "assets"
    assert path.is_file()
    # ICO files start with the 00 00 01 00 reserved+type header.
    assert path.read_bytes()[:4] == b"\x00\x00\x01\x00"


def test_single_icon_is_used(tmp_path: Path) -> None:
    icon = tmp_path / ICON_FILENAME
    icon.write_bytes(b"x")
    assert _pick_icon(tmp_path) == icon


def test_dropped_icon_overrides_the_default(tmp_path: Path) -> None:
    (tmp_path / ICON_FILENAME).write_bytes(b"default")
    brand = tmp_path / "brand.ico"
    brand.write_bytes(b"custom")
    assert _pick_icon(tmp_path) == brand


def test_multiple_custom_icons_resolve_alphabetically(tmp_path: Path) -> None:
    (tmp_path / "zeta.ico").write_bytes(b"z")
    alpha = tmp_path / "Alpha.ico"
    alpha.write_bytes(b"a")
    assert _pick_icon(tmp_path) == alpha


def test_no_icons_returns_none(tmp_path: Path) -> None:
    assert _pick_icon(tmp_path) is None


def test_load_icon_returns_real_icon(qapp) -> None:
    assert not load_icon().isNull()


def test_missing_asset_returns_empty_icon(qapp, monkeypatch) -> None:
    monkeypatch.setattr("manim_editor.editor.icon.icon_path", lambda: None)
    assert load_icon().isNull()


class _FakeShell32:
    def __init__(self) -> None:
        self.ids: list[str] = []

    def SetCurrentProcessExplicitAppUserModelID(self, app_id: str) -> int:
        self.ids.append(app_id)
        return 0


class _FakeWindll:
    def __init__(self) -> None:
        self.shell32 = _FakeShell32()


def test_windows_app_id_is_set(monkeypatch) -> None:
    fake = _FakeWindll()
    monkeypatch.setattr(ctypes, "windll", fake, raising=False)
    monkeypatch.setattr(sys, "platform", "win32")
    app_module._set_windows_app_id("Example.Id")
    assert fake.shell32.ids == ["Example.Id"]


def test_windows_app_id_skipped_off_windows(monkeypatch) -> None:
    fake = _FakeWindll()
    monkeypatch.setattr(ctypes, "windll", fake, raising=False)
    monkeypatch.setattr(sys, "platform", "linux")
    app_module._set_windows_app_id("Example.Id")
    assert fake.shell32.ids == []
