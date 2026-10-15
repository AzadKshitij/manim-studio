"""What the editor remembers between sessions: layout, the scene chosen per file, the last
file opened, and the preview mode.

Everything lives in QSettings.  When persistence is off (tests, scripted windows) nothing
is written and every read returns the default, so a run never depends on a user's
saved settings.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from PySide6.QtCore import QByteArray, QSettings
from PySide6.QtWidgets import QSplitter

from manim_studio import external_changes

__all__ = ["SessionState", "scene_key"]

_HORIZONTAL = "layout/horizontal"
_VERTICAL = "layout/vertical"
_SHELL_VISIBLE = "layout/shell_visible"
_LAYOUT_MODE = "layout/mode"
_SCENE_DEFAULTS = "scene/defaults"
_EMBEDDED = "window/embedded"
_LAST_FILE = "session/last_file"
_REOPEN_LAST = "session/reopen_last_file"
_EXTERNAL = "file/external_changes"


def scene_key(path: Path) -> str:
    """A settings key for ``path`` that does not depend on how the path is spelled."""
    digest = hashlib.sha256(str(path.resolve()).encode("utf-8")).hexdigest()
    return f"scene_selection/{digest[:20]}"


def _layout_keys(preset: str) -> tuple[str, str]:
    """Landscape keeps the original keys, so layouts saved before presets still restore."""
    if preset == "landscape":
        return _HORIZONTAL, _VERTICAL
    return f"layout/{preset}/horizontal", f"layout/{preset}/vertical"


def _as_bool(value: Any, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in ("true", "1", "yes")


class SessionState:
    """Reads and writes what the window remembers; inert when ``enabled`` is false."""

    def __init__(self, settings: QSettings, *, enabled: bool) -> None:
        self._settings = settings
        self._enabled = enabled

    @property
    def enabled(self) -> bool:
        return self._enabled

    # ------------------------------------------------------------------
    # Layout
    # ------------------------------------------------------------------

    def remember_layout(
        self,
        *,
        horizontal: QSplitter,
        vertical: QSplitter,
        shell_visible: bool,
        preset: str = "landscape",
    ) -> None:
        if not self._enabled:
            return
        horizontal_key, vertical_key = _layout_keys(preset)
        self._settings.setValue(horizontal_key, horizontal.saveState())
        self._settings.setValue(vertical_key, vertical.saveState())
        self._settings.setValue(_SHELL_VISIBLE, shell_visible)

    def restore_layout(
        self, *, horizontal: QSplitter, vertical: QSplitter, preset: str = "landscape"
    ) -> bool | None:
        """Restore the splitter sizes; returns the remembered Live shell visibility, if any."""
        if not self._enabled:
            return None
        horizontal_key, vertical_key = _layout_keys(preset)
        for key, splitter in ((horizontal_key, horizontal), (vertical_key, vertical)):
            state = self._settings.value(key)
            if isinstance(state, bytes):
                state = QByteArray(state)
            if isinstance(state, QByteArray) and not state.isEmpty():
                splitter.restoreState(state)
        shell = self._settings.value(_SHELL_VISIBLE)
        return None if shell is None else _as_bool(shell, default=False)

    def layout_mode(self) -> str | None:
        """The remembered layout choice: ``auto``, ``landscape`` or ``portrait``."""
        if not self._enabled:
            return None
        value = self._settings.value(_LAYOUT_MODE)
        return str(value) if value else None

    def remember_layout_mode(self, mode: str) -> None:
        if self._enabled:
            self._settings.setValue(_LAYOUT_MODE, mode)

    # ------------------------------------------------------------------
    # Defaults for new scenes
    # ------------------------------------------------------------------

    def scene_defaults_json(self) -> str | None:
        """The app-wide scene settings new scenes start with, as stored JSON."""
        if not self._enabled:
            return None
        value = self._settings.value(_SCENE_DEFAULTS)
        return str(value) if value else None

    def remember_scene_defaults_json(self, text: str | None) -> None:
        if not self._enabled:
            return
        if text is None:
            self._settings.remove(_SCENE_DEFAULTS)
        else:
            self._settings.setValue(_SCENE_DEFAULTS, text)

    # ------------------------------------------------------------------
    # Preview mode
    # ------------------------------------------------------------------

    def embedded(self) -> bool | None:
        """The remembered preview mode, or ``None`` when nothing was remembered."""
        if not self._enabled:
            return None
        value = self._settings.value(_EMBEDDED)
        return None if value is None else _as_bool(value, default=True)

    def remember_embedded(self, embedded: bool) -> None:
        if self._enabled:
            self._settings.setValue(_EMBEDDED, bool(embedded))

    # ------------------------------------------------------------------
    # Files and scenes
    # ------------------------------------------------------------------

    def last_file(self) -> Path | None:
        if not self._enabled:
            return None
        value = self._settings.value(_LAST_FILE)
        return Path(str(value)) if value else None

    def remember_last_file(self, path: Path) -> None:
        if self._enabled:
            self._settings.setValue(_LAST_FILE, str(path.resolve()))

    def external_policy(self) -> str:
        """How changes on disk are handled (see :mod:`manim_studio.external_changes`)."""
        if not self._enabled:
            return external_changes.DEFAULT_POLICY
        return external_changes.parse_policy(self._settings.value(_EXTERNAL))

    def remember_external_policy(self, policy: str) -> None:
        if self._enabled:
            self._settings.setValue(_EXTERNAL, external_changes.parse_policy(policy))

    @property
    def reopen_last_file(self) -> bool:
        """Whether a launch without a file reopens the last one (on by default)."""
        if not self._enabled:
            return False
        return _as_bool(self._settings.value(_REOPEN_LAST), default=True)

    def set_reopen_last_file(self, on: bool) -> None:
        if self._enabled:
            self._settings.setValue(_REOPEN_LAST, bool(on))

    def scene_for(self, path: Path) -> str | None:
        """The scene class last chosen for ``path``, or ``None``."""
        if not self._enabled:
            return None
        value = self._settings.value(scene_key(path))
        return str(value) if value else None

    def remember_scene(self, path: Path, name: str) -> None:
        if self._enabled and name:
            self._settings.setValue(scene_key(path), name)
