"""The timeline sidecar file and its undo stack (pure, stdlib only).

Timeline edits live next to the scene as ``<scene>.studio.json`` until the user chooses
"Apply to source".  The file holds only the anchored patches from
:mod:`manim_studio.patches`; the scene source stays untouched.

:class:`PatchStack` is the in-memory edit history (undo/redo of whole patch sets) and
replaces the old per-block ``TimingOverrides``.  Its :attr:`revision` increases on every
change so caches keyed on "what the scene currently looks like" can invalidate.
"""

from __future__ import annotations

import contextlib
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from manim_studio.patches import Patch, PatchError, parse_patch, with_patch

if TYPE_CHECKING:
    from collections.abc import Iterable

__all__ = [
    "SIDECAR_SUFFIX",
    "SIDECAR_VERSION",
    "PatchStack",
    "SidecarError",
    "load_sidecar",
    "save_sidecar",
    "sidecar_path",
]

SIDECAR_VERSION = 1
SIDECAR_SUFFIX = ".studio.json"


class SidecarError(ValueError):
    """Raised when a sidecar file exists but cannot be understood."""


def sidecar_path(scene_path: str | Path) -> Path:
    """``scenes/foo.py`` -> ``scenes/foo.studio.json``."""
    path = Path(scene_path)
    return path.with_name(path.stem + SIDECAR_SUFFIX)


def load_sidecar(scene_path: str | Path) -> tuple[Patch, ...]:
    """Read the patches stored for ``scene_path``; an absent file means no patches."""
    path = sidecar_path(scene_path)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return ()
    except (OSError, json.JSONDecodeError) as exc:
        raise SidecarError(f"cannot read {path.name}: {exc}") from exc
    if not isinstance(raw, dict):
        raise SidecarError(f"{path.name} must contain a JSON object")
    version = raw.get("version")
    if version != SIDECAR_VERSION:
        raise SidecarError(f"{path.name} has unsupported version {version!r}")
    items = raw.get("patches")
    if not isinstance(items, list):
        raise SidecarError(f"{path.name} 'patches' must be a list")
    try:
        return tuple(parse_patch(item) for item in items)
    except PatchError as exc:
        raise SidecarError(f"{path.name}: {exc}") from exc


def save_sidecar(scene_path: str | Path, patches: Iterable[Patch]) -> Path | None:
    """Write the patches atomically; with no patches the sidecar file is removed.

    Returns the path written, or ``None`` when the file was removed (or never existed).
    """
    path = sidecar_path(scene_path)
    items = [patch.to_dict() for patch in patches]
    if not items:
        with contextlib.suppress(FileNotFoundError):
            path.unlink()
        return None
    payload = {"version": SIDECAR_VERSION, "patches": items}
    temp = path.with_name(path.name + ".tmp")
    temp.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    os.replace(temp, path)
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
