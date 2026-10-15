"""The timeline sidecar file and its undo stack (pure, stdlib only).

Timeline edits live next to the scene as ``<scene>.studio.json`` until the user chooses
"Apply to source".  The file holds the anchored patches from :mod:`manim_studio.patches`
and, per scene class, the render settings from :mod:`manim_studio.scene_settings`; the
scene source stays untouched until the user applies them.

:class:`PatchStack` is the in-memory edit history (undo/redo of whole patch sets) and
replaces the old per-block ``TimingOverrides``.  Its :attr:`revision` increases on every
change so caches keyed on "what the scene currently looks like" can invalidate.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from manim_studio.patches import Patch, PatchError, parse_patch, with_patch
from manim_studio.scene_settings import SceneSettings, SettingsError

if TYPE_CHECKING:
    from collections.abc import Iterable

__all__ = [
    "SIDECAR_SUFFIX",
    "SIDECAR_VERSION",
    "PatchStack",
    "SidecarError",
    "load_scene_settings",
    "load_sidecar",
    "read_patch_file",
    "read_settings_file",
    "save_scene_settings",
    "save_sidecar",
    "sidecar_path",
    "write_patch_file",
    "write_settings_file",
]

SIDECAR_VERSION = 2
# Version 1 held patches only; it is still read and is rewritten as version 2 on save.
_READABLE_VERSIONS = (1, 2)
SIDECAR_SUFFIX = ".studio.json"


class SidecarError(ValueError):
    """Raised when a sidecar file exists but cannot be understood."""


def sidecar_path(scene_path: str | Path) -> Path:
    """``scenes/foo.py`` -> ``scenes/foo.studio.json``."""
    path = Path(scene_path)
    return path.with_name(path.stem + SIDECAR_SUFFIX)


def load_document(scene_path: str | Path) -> tuple[tuple[Patch, ...], dict[str, SceneSettings]]:
    """Patches and per-scene settings stored for ``scene_path``; absent means none."""
    path = sidecar_path(scene_path)
    if not path.exists():
        return (), {}
    return _read_document(path)


def load_sidecar(scene_path: str | Path) -> tuple[Patch, ...]:
    """Read the patches stored for ``scene_path``; an absent file means no patches."""
    return load_document(scene_path)[0]


def load_scene_settings(scene_path: str | Path) -> dict[str, SceneSettings]:
    """Settings per scene class name stored for ``scene_path``; absent means none."""
    return load_document(scene_path)[1]


def read_patch_file(path: str | Path) -> tuple[Patch, ...]:
    """Parse a sidecar-format JSON file; raises :class:`SidecarError` on any problem."""
    return _read_document(Path(path))[0]


def _read_document(path: Path) -> tuple[tuple[Patch, ...], dict[str, SceneSettings]]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SidecarError(f"cannot read {path.name}: {exc}") from exc
    if not isinstance(raw, dict):
        raise SidecarError(f"{path.name} must contain a JSON object")
    version = raw.get("version")
    if version not in _READABLE_VERSIONS:
        raise SidecarError(f"{path.name} has unsupported version {version!r}")
    items = raw.get("patches")
    if not isinstance(items, list):
        raise SidecarError(f"{path.name} 'patches' must be a list")
    try:
        patches = tuple(parse_patch(item) for item in items)
    except PatchError as exc:
        raise SidecarError(f"{path.name}: {exc}") from exc
    scenes_raw = raw.get("scenes", {})
    if not isinstance(scenes_raw, dict):
        raise SidecarError(f"{path.name} 'scenes' must be an object")
    try:
        scenes = {str(name): SceneSettings.from_dict(data) for name, data in scenes_raw.items()}
    except SettingsError as exc:
        raise SidecarError(f"{path.name}: {exc}") from exc
    return patches, scenes


def _payload(items: list[dict[str, Any]], scenes: Mapping[str, Any]) -> dict[str, Any]:
    payload: dict[str, Any] = {"version": SIDECAR_VERSION, "patches": items}
    if scenes:
        payload["scenes"] = dict(scenes)
    return payload


def save_sidecar(
    scene_path: str | Path,
    patches: Iterable[Patch],
    settings: Mapping[str, SceneSettings] | None = None,
) -> Path | None:
    """Write the sidecar atomically; with nothing left to store the file is removed.

    ``settings`` replaces the per-scene settings.  When it is ``None`` the settings already
    on disk are kept, so saving edits never drops render settings (and vice versa).

    Returns the path written, or ``None`` when the file was removed (or never existed).
    """
    path = sidecar_path(scene_path)
    kept = _existing_settings(path) if settings is None else dict(settings)
    items = [patch.to_dict() for patch in patches]
    scenes = {name: value.to_dict() for name, value in kept.items()}
    if not items and not scenes:
        with contextlib.suppress(FileNotFoundError):
            path.unlink()
        return None
    temp = path.with_name(path.name + ".tmp")
    temp.write_text(json.dumps(_payload(items, scenes), indent=2) + "\n", encoding="utf-8")
    os.replace(temp, path)
    return path


def save_scene_settings(
    scene_path: str | Path, settings: Mapping[str, SceneSettings]
) -> Path | None:
    """Replace the per-scene settings, keeping the stored patches."""
    return save_sidecar(scene_path, load_sidecar(scene_path), settings)


def _existing_settings(path: Path) -> dict[str, SceneSettings]:
    if not path.exists():
        return {}
    # An unreadable file is an error, not an empty one: overwriting it would lose data.
    return _read_document(path)[1]


def write_patch_file(patches: Iterable[Patch]) -> Path:
    """Write ``patches`` to a content-addressed temp file and return its path.

    Handed to processes (the preview host, the export launcher) that must run
    exactly the edits the editor is showing, independent of what is on disk.
    """
    content = json.dumps(_payload([patch.to_dict() for patch in patches], {}))
    return _write_temp("patches", content)


def write_settings_file(settings: SceneSettings) -> Path:
    """Write one scene's settings to a content-addressed temp file and return its path."""
    return _write_temp("settings", json.dumps(settings.to_dict()))


def read_settings_file(path: str | Path) -> SceneSettings:
    """Read a file written by :func:`write_settings_file`."""
    target = Path(path)
    try:
        raw = json.loads(target.read_text(encoding="utf-8"))
        return SceneSettings.from_dict(raw)
    except (OSError, json.JSONDecodeError, SettingsError) as exc:
        raise SidecarError(f"cannot read settings {target.name}: {exc}") from exc


def _write_temp(kind: str, content: str) -> Path:
    digest = hashlib.sha1(content.encode("utf-8")).hexdigest()[:16]
    config_dir = Path(tempfile.gettempdir()) / "manim-studio"
    config_dir.mkdir(parents=True, exist_ok=True)
    path = config_dir / f"{kind}_{digest}.json"
    if not path.exists() or path.read_text(encoding="utf-8") != content:
        path.write_text(content, encoding="utf-8")
    return path


@dataclass(frozen=True, slots=True)
class _Snapshot:
    patches: tuple[Patch, ...]


class PatchStack:
    """The current patch set with undo/redo."""

    def __init__(self, patches: Iterable[Patch] = ()) -> None:
        self._current: tuple[Patch, ...] = tuple(patches)
        self._undo: list[_Snapshot] = []
        self._redo: list[_Snapshot] = []
        self._revision = 0

    @property
    def patches(self) -> tuple[Patch, ...]:
        return self._current

    @property
    def revision(self) -> int:
        """Bumps on every change (including undo/redo); never reused."""
        return self._revision

    @property
    def is_empty(self) -> bool:
        return not self._current

    @property
    def can_undo(self) -> bool:
        return bool(self._undo)

    @property
    def can_redo(self) -> bool:
        return bool(self._redo)

    def add(self, patch: Patch) -> bool:
        """Add ``patch`` (superseding what it replaces); False when nothing changed."""
        return self._set(with_patch(self._current, patch))

    def discard(self, patch: Patch) -> bool:
        """Drop one patch; False when it was not present."""
        if patch not in self._current:
            return False
        return self._set(tuple(p for p in self._current if p != patch))

    def discard_site(self, site_id: str) -> bool:
        """Drop every patch anchored on ``site_id``."""
        kept = tuple(p for p in self._current if p.anchor.site_id != site_id)
        return self._set(kept)

    def replace(self, patches: Iterable[Patch]) -> bool:
        """Swap in a whole new patch set (for example with orphans removed)."""
        return self._set(tuple(patches))

    def clear(self) -> bool:
        return self._set(())

    def undo(self) -> bool:
        if not self._undo:
            return False
        self._redo.append(_Snapshot(self._current))
        self._current = self._undo.pop().patches
        self._revision += 1
        return True

    def redo(self) -> bool:
        if not self._redo:
            return False
        self._undo.append(_Snapshot(self._current))
        self._current = self._redo.pop().patches
        self._revision += 1
        return True

    def _set(self, patches: tuple[Patch, ...]) -> bool:
        if patches == self._current:
            return False
        self._undo.append(_Snapshot(self._current))
        self._redo.clear()
        self._current = patches
        self._revision += 1
        return True
