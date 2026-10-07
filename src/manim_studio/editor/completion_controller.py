"""Jedi completion lifecycle and result routing for the code editor.

``code_editor`` emits completion/signature requests; this controller forwards
them to the GUI-thread-owned :class:`CompletionWorker` and pushes results back
into the editor.  The worker is created once and started lazily on the first
request (``MainWindow(..., completion=False)`` never creates one), and it is
stopped when the controller is destroyed so no thread outlives the window.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from PySide6.QtCore import QObject

from manim_studio.editor.completion_worker import CompletionWorker

if TYPE_CHECKING:
    from manim_studio.editor.code_editor import CodeEditor

__all__ = ["CompletionController"]


class CompletionController(QObject):
    """Owns the Jedi worker and routes its results to the editor."""

    def __init__(
        self,
        editor: CodeEditor,
        *,
        enabled: bool = True,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._editor = editor
        self._worker: CompletionWorker | None = None
        if enabled:
            self._worker = CompletionWorker(self)
            self._worker.completionsReady.connect(self._on_completions_ready)
            self._worker.signaturesReady.connect(self._on_signatures_ready)
            self.destroyed.connect(self._on_destroyed)

    @property
    def worker(self) -> CompletionWorker | None:
        """The underlying thread, or ``None`` when completion is disabled."""
        return self._worker

    # -- request routing ---------------------------------------------------

    def request_completions(self, source: str, offset: int, path: str | None) -> None:
        if self._worker is None:
            return
        self._worker.request_completions(source, offset, path)
        self._ensure_running()

    def request_signatures(self, source: str, offset: int, path: str | None) -> None:
        if self._worker is None:
            return
        self._worker.request_signatures(source, offset, path)
        self._ensure_running()

    def warm_up(self, source: str, path: str | None) -> None:
        """Prime Jedi's cache for freshly loaded buffer text, in the background."""
        if self._worker is None:
            return
        self._worker.request_warmup(source, path)
        self._ensure_running()

    def stop(self) -> None:
        """Stop the worker thread (safe to call repeatedly)."""
        if self._worker is not None:
            self._worker.stop()

    # -- internals ---------------------------------------------------------

    def _ensure_running(self) -> None:
        if self._worker is not None and not self._worker.isRunning():
            self._worker.start()

    def _on_destroyed(self, *_args: Any) -> None:
        self.stop()

    def _on_completions_ready(self, offset: int, items: Any) -> None:
        self._editor.show_completions(offset, [item.insert for item in items])

    def _on_signatures_ready(self, offset: int, items: Any) -> None:
        if items:
            self._editor.show_calltip(offset, "\n".join(item.label for item in items))
