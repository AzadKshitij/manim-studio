"""The render settings of the open file's scenes, kept in its sidecar (no Qt).

The window asks this store for the settings of the scene it is showing, and asks it to
save changes.  Saving goes through :mod:`manim_studio.sidecar`, which keeps the timeline
patches already stored in the same file.
"""

from __future__ import annotations

from pathlib import Path

from manim_studio.scene_settings import SceneSettings
from manim_studio.sidecar import SidecarError, load_scene_settings, save_scene_settings

__all__ = ["SceneSettingsStore"]


class SceneSettingsStore:
    """Settings per scene class for one scene file."""

    def __init__(self) -> None:
        self._path: Path | None = None
        self._settings: dict[str, SceneSettings] = {}
        self._error: str | None = None

    @property
    def path(self) -> Path | None:
        return self._path

    @property
    def error(self) -> str | None:
        """Why the stored settings could not be read, if they could not."""
        return self._error

    def open(self, path: Path) -> None:
        """Load the settings stored beside ``path``; an unreadable file is reported, not lost."""
        self._path = path
        self._settings = {}
        self._error = None
        try:
            self._settings = load_scene_settings(path)
        except SidecarError as exc:
            self._error = str(exc)

    def close(self) -> None:
        self._path = None
        self._settings = {}
        self._error = None

    def settings_for(self, scene_name: str | None) -> SceneSettings | None:
        if not scene_name:
            return None
        return self._settings.get(scene_name)

    def set_settings(self, scene_name: str, settings: SceneSettings) -> None:
        """Store ``settings`` for ``scene_name`` and save the file.

        Raises :class:`SidecarError` (and leaves the stored value unchanged) when the
        sidecar cannot be written.
        """
        if self._path is None:
            raise SidecarError("no scene file is open")
        updated = {**self._settings, scene_name: settings}
        save_scene_settings(self._path, updated)
        self._settings = updated

    def clear_settings(self, scene_name: str) -> bool:
        """Forget the settings of ``scene_name``; False when there were none."""
        if self._path is None or scene_name not in self._settings:
            return False
        updated = {name: value for name, value in self._settings.items() if name != scene_name}
        save_scene_settings(self._path, updated)
        self._settings = updated
        return True
