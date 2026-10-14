"""Keeps a recovery copy of unsaved edits beside the open scene file."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from PySide6.QtCore import QObject, QTimer

from manim_studio import autosave
from manim_studio.editor import scene_files
from manim_studio.editor.code_editor import CodeEditor

__all__ = ["AutosaveController"]


class AutosaveController(QObject):
    """Writes a snapshot once typing pauses, and removes it once the work is saved.

    While a recovery prompt is open, :meth:`pending_recovery` puts the controller on
    hold, so the nested event loop cannot rewrite or delete the snapshot being offered.
    """

    DELAY_MS = 2000

    def __init__(self, editor: CodeEditor, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._editor = editor
        self._path: Path | None = None
        self._hold = False
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(self.DELAY_MS)
        self._timer.timeout.connect(self.snapshot)
        editor.modified.connect(self._on_edit)

    def set_interval(self, ms: int) -> None:
        self._timer.setInterval(ms)

    def set_path(self, path: Path | None) -> None:
        self._timer.stop()
        self._hold = False
        self._path = path

    def pending_recovery(self) -> str | None:
        """Recovered code to offer on open, or ``None`` when there is nothing to recover.

        Offering a recovery holds the controller until :meth:`release` is called.
        """
        if self._path is None:
            return None
        snapshot = autosave.autosave_path(self._path)
        if not snapshot.is_file():
            return None
        text = snapshot.read_text(encoding="utf-8")
        disk_mtime = self._path.stat().st_mtime if self._path.exists() else None
        offer = autosave.should_offer_recovery(
            text,
            snapshot.stat().st_mtime,
            scene_files.read_disk_text(self._path),
            disk_mtime,
        )
        if not offer:
            return None
        self._hold = True
        return text

    def release(self) -> None:
        self._hold = False

    def snapshot(self) -> None:
        """Bring the snapshot in step with the buffer.

        Written while the buffer holds edits the disk does not.  Otherwise it is removed,
        including when the code was typed back to match the file while the editor still
        reports unsaved edits, so no reverted edit can be offered for recovery later.
        """
        if self._path is None or self._hold:
            return
        path = autosave.autosave_path(self._path)
        text = self._editor.text()
        if autosave.should_keep_snapshot(
            text, scene_files.read_disk_text(self._path), self._editor.is_modified()
        ):
            path.write_text(text, encoding="utf-8")
        else:
            path.unlink(missing_ok=True)

    def discard(self) -> None:
        """Remove the snapshot for the open scene (after a save, a discard or a clean close)."""
        self._timer.stop()
        if self._path is not None:
            autosave.autosave_path(self._path).unlink(missing_ok=True)

    def _on_edit(self, *_args: Any) -> None:
        if self._path is not None:
            self._timer.start()  # restarting debounces: one write per pause
