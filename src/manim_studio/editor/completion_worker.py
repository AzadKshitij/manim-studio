"""Off-thread Jedi completion and signature lookups for the code editor.

Jedi is fast for scene-sized files but not instant on first use, so the editor
hands requests to this worker and keeps rendering.  Requests coalesce: while a
lookup runs, newer requests overwrite the pending one, so a burst of keystrokes
produces a single call.  Results carry the buffer offset they were computed for;
the editor drops any that no longer match the caret.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable

from PySide6.QtCore import QObject, QThread, Signal

from manim_studio import python_completion
from manim_studio.python_completion import Completion, Signature

__all__ = ["CompletionWorker"]

log = logging.getLogger(__name__)

CompleteFn = Callable[[str, int, "str | None"], tuple[Completion, ...]]
SignatureFn = Callable[[str, int, "str | None"], tuple[Signature, ...]]

_Job = tuple[str, str, int, "str | None"]


class CompletionWorker(QThread):
    """Runs Jedi away from the GUI thread, newest request wins."""

    completionsReady = Signal(int, object)  # offset, tuple[Completion, ...]
    signaturesReady = Signal(int, object)  # offset, tuple[Signature, ...]

    def __init__(
        self,
        parent: QObject | None = None,
        complete_fn: CompleteFn = python_completion.complete,
        signature_fn: SignatureFn = python_completion.signatures,
    ) -> None:
        super().__init__(parent)
        self._complete_fn = complete_fn
        self._signature_fn = signature_fn
        self._condition = threading.Condition()
        self._pending_complete: tuple[str, int, str | None] | None = None
        self._pending_signature: tuple[str, int, str | None] | None = None
        self._pending_warmup: tuple[str, str | None] | None = None
        self._stopping = False

    # -- called from the GUI thread ------------------------------------

    def request_completions(self, source: str, offset: int, path: str | None = None) -> None:
        with self._condition:
            self._pending_complete = (source, offset, path)
            self._condition.notify()

    def request_signatures(self, source: str, offset: int, path: str | None = None) -> None:
        with self._condition:
            self._pending_signature = (source, offset, path)
            self._condition.notify()

    def request_warmup(self, source: str, path: str | None = None) -> None:
        """Parse ``source`` in the background so the next lookup is fast.

        The result is discarded; this only primes Jedi's internal caches after
        a file is opened (the first manimlib parse costs a few seconds).
        """
        with self._condition:
            self._pending_warmup = (source, path)
            self._condition.notify()

    def stop(self) -> None:
        """Ask the loop to exit and wait briefly for the current call."""
        with self._condition:
            self._stopping = True
            self._condition.notify()
        self.wait(3000)

    # -- worker thread -------------------------------------------------

    def run(self) -> None:
        while True:
            job = self._next_job()
            if job is None:
                return
            kind, source, offset, path = job
            try:
                if kind == "complete":
                    self.completionsReady.emit(offset, self._complete_fn(source, offset, path))
                elif kind == "signature":
                    self.signaturesReady.emit(offset, self._signature_fn(source, offset, path))
                else:  # warmup: parse to prime Jedi's caches, discard the result
                    self._complete_fn(source, offset, path)
            except Exception:  # pragma: no cover - defensive: never kill the thread
                log.debug("completion worker job failed", exc_info=True)

    def _next_job(self) -> _Job | None:
        with self._condition:
            while (
                self._pending_complete is None
                and self._pending_signature is None
                and self._pending_warmup is None
                and not self._stopping
            ):
                self._condition.wait()
            if self._stopping:
                return None
            if self._pending_complete is not None:
                source, offset, path = self._pending_complete
                self._pending_complete = None
                return ("complete", source, offset, path)
            if self._pending_signature is not None:
                source, offset, path = self._pending_signature
                self._pending_signature = None
                return ("signature", source, offset, path)
            assert self._pending_warmup is not None
            source, path = self._pending_warmup
            self._pending_warmup = None
            return ("warmup", source, len(source), path)
