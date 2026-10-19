"""Sidecar version 2: per-scene render settings beside the timeline patches."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from manim_studio.scene_settings import SceneSettings
from manim_studio.sidecar import (
    SIDECAR_VERSION,
    SidecarError,
    load_scene_settings,
    load_sidecar,
    read_patch_file,
    read_settings_file,
    save_scene_settings,
    save_sidecar,
    sidecar_path,
    write_settings_file,
)

PORTRAIT = SceneSettings(width=1080, height=1920, palette="ocean", swatches=("#FFFFFF",))


@pytest.fixture
def scene(tmp_path: Path) -> Path:
    path = tmp_path / "intro.py"
    path.write_text("class Intro(Scene):\n    pass\n", encoding="utf-8")
    return path


def test_version_is_two() -> None:
    assert SIDECAR_VERSION == 2


def test_settings_round_trip_through_the_file(scene: Path) -> None:
    save_scene_settings(scene, {"Intro": PORTRAIT})
    assert load_scene_settings(scene) == {"Intro": PORTRAIT}
    assert json.loads(sidecar_path(scene).read_text(encoding="utf-8"))["version"] == 2


def test_settings_alone_keep_the_file(scene: Path) -> None:
    assert save_scene_settings(scene, {"Intro": PORTRAIT}) == sidecar_path(scene)
    assert sidecar_path(scene).exists()


def test_clearing_everything_removes_the_file(scene: Path) -> None:
    save_scene_settings(scene, {"Intro": PORTRAIT})
    assert save_scene_settings(scene, {}) is None
    assert not sidecar_path(scene).exists()


def test_saving_patches_keeps_existing_settings(scene: Path) -> None:
    save_scene_settings(scene, {"Intro": PORTRAIT})
    save_sidecar(scene, ())
    assert load_scene_settings(scene) == {"Intro": PORTRAIT}


def test_saving_settings_keeps_existing_patches(scene: Path) -> None:
    save_sidecar(scene, load_sidecar(scene), None)  # no patches yet: nothing written
    assert not sidecar_path(scene).exists()
    save_scene_settings(scene, {"Intro": PORTRAIT})
    assert load_sidecar(scene) == ()
    assert load_scene_settings(scene) == {"Intro": PORTRAIT}


def test_version_one_files_still_load(scene: Path) -> None:
    sidecar_path(scene).write_text(json.dumps({"version": 1, "patches": []}), encoding="utf-8")
    assert load_sidecar(scene) == ()
    assert load_scene_settings(scene) == {}


def test_read_patch_file_accepts_version_two(tmp_path: Path) -> None:
    path = tmp_path / "patches.json"
    path.write_text(json.dumps({"version": 2, "patches": []}), encoding="utf-8")
    assert read_patch_file(path) == ()


@pytest.mark.parametrize(
    "payload",
    [
        {"version": 2, "patches": [], "scenes": []},
        {"version": 2, "patches": [], "scenes": {"Intro": {"width": 3}}},
        {"version": 2, "patches": [], "scenes": {"Intro": {"swatches": "#FFF"}}},
    ],
)
def test_bad_scene_settings_are_a_sidecar_error(scene: Path, payload: dict) -> None:
    sidecar_path(scene).write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(SidecarError):
        load_scene_settings(scene)


def test_unreadable_file_is_not_silently_overwritten(scene: Path) -> None:
    sidecar_path(scene).write_text("{broken", encoding="utf-8")
    with pytest.raises(SidecarError):
        save_scene_settings(scene, {"Intro": PORTRAIT})
    assert sidecar_path(scene).read_text(encoding="utf-8") == "{broken"


def test_settings_temp_file_round_trips() -> None:
    path = write_settings_file(PORTRAIT)
    assert read_settings_file(path) == PORTRAIT
    assert write_settings_file(PORTRAIT) == path  # content-addressed: same file again
