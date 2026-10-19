"""The editor's per-scene settings store: saving, reloading and what it refuses (no Qt)."""

from __future__ import annotations

from pathlib import Path

import pytest

from manim_studio.editor.scene_settings_store import SceneSettingsStore
from manim_studio.patches import Anchor, SetDuration
from manim_studio.scene_settings import SceneSettings
from manim_studio.sidecar import SidecarError, load_sidecar, save_sidecar, sidecar_path


@pytest.fixture
def scene(tmp_path: Path) -> Path:
    path = tmp_path / "intro.py"
    path.write_text("class Intro(Scene):\n    pass\n", encoding="utf-8")
    return path


def test_settings_are_saved_and_reloaded(scene: Path) -> None:
    store = SceneSettingsStore()
    store.open(scene)
    assert store.settings_for("Intro") is None
    store.set_settings("Intro", SceneSettings(width=1080, height=1920))

    reopened = SceneSettingsStore()
    reopened.open(scene)
    assert reopened.settings_for("Intro") == SceneSettings(width=1080, height=1920)


def test_no_settings_without_a_scene_name(scene: Path) -> None:
    store = SceneSettingsStore()
    store.open(scene)
    store.set_settings("Intro", SceneSettings())
    assert store.settings_for(None) is None
    assert store.settings_for("") is None


def test_saving_without_an_open_file_is_refused() -> None:
    with pytest.raises(SidecarError):
        SceneSettingsStore().set_settings("Intro", SceneSettings())


def test_unreadable_sidecar_is_reported_and_left_alone(scene: Path) -> None:
    sidecar_path(scene).write_text("{broken", encoding="utf-8")
    store = SceneSettingsStore()
    store.open(scene)
    assert store.error is not None
    assert store.settings_for("Intro") is None
    with pytest.raises(SidecarError):
        store.set_settings("Intro", SceneSettings())
    assert sidecar_path(scene).read_text(encoding="utf-8") == "{broken"


def test_clearing_the_last_settings_removes_the_file(scene: Path) -> None:
    store = SceneSettingsStore()
    store.open(scene)
    store.set_settings("Intro", SceneSettings())
    assert store.clear_settings("Intro") is True
    assert not sidecar_path(scene).exists()
    assert store.clear_settings("Intro") is False


def test_timeline_patches_survive_settings_changes(scene: Path) -> None:
    patch = SetDuration(Anchor("Intro.construct:wait:abc:0"), 2.0)
    save_sidecar(scene, [patch])
    store = SceneSettingsStore()
    store.open(scene)
    store.set_settings("Intro", SceneSettings(width=720, height=1280))
    assert load_sidecar(scene) == (patch,)
