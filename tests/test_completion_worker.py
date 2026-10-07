"""Tests for the off-thread completion worker (Jedi replaced by fakes)."""

from __future__ import annotations

import threading

import pytest

from manim_editor.editor.completion_worker import CompletionWorker
from manim_editor.python_completion import Completion, Signature

pytestmark = pytest.mark.usefixtures("qtbot")


class TestWorker:
    def test_completions_are_emitted(self, qtbot) -> None:
        def fake_complete(source: str, offset: int, path: str | None) -> tuple[Completion, ...]:
            return (Completion(name="append", insert="append", kind="function"),)

        worker = CompletionWorker(complete_fn=fake_complete)
        worker.start()
        try:
            with qtbot.waitSignal(worker.completionsReady, timeout=3000) as blocker:
                worker.request_completions("x", 3)
            offset, items = blocker.args
            assert offset == 3
            assert [item.insert for item in items] == ["append"]
        finally:
            worker.stop()

    def test_signatures_are_emitted(self, qtbot) -> None:
        def fake_signatures(source: str, offset: int, path: str | None) -> tuple[Signature, ...]:
            return (Signature(label="greet(name)", active_parameter=0),)

        worker = CompletionWorker(signature_fn=fake_signatures)
        worker.start()
        try:
            with qtbot.waitSignal(worker.signaturesReady, timeout=3000) as blocker:
                worker.request_signatures("x", 2)
            offset, items = blocker.args
            assert offset == 2
            assert items[0].label == "greet(name)"
        finally:
            worker.stop()

    def test_burst_coalesces_to_latest(self, qtbot) -> None:
        started = threading.Event()
        release = threading.Event()
        seen: list[int] = []

        def fake_complete(source: str, offset: int, path: str | None) -> tuple[Completion, ...]:
            seen.append(offset)
            started.set()
            release.wait(3)
            return ()

        worker = CompletionWorker(complete_fn=fake_complete)
        worker.start()
        try:
            worker.request_completions("s", 1)
            assert started.wait(3)
            # While the first lookup is blocked, later requests overwrite the
            # pending one; only the newest survives.
            worker.request_completions("s", 2)
            worker.request_completions("s", 3)
            release.set()
            qtbot.waitUntil(lambda: len(seen) >= 2, timeout=4000)
            assert seen == [1, 3]
        finally:
            release.set()
            worker.stop()

    def test_warmup_runs_without_emitting(self, qtbot) -> None:
        calls: list[tuple[str, int, str | None]] = []

        def fake_complete(source: str, offset: int, path: str | None) -> tuple[Completion, ...]:
            calls.append((source, offset, path))
            return ()

        worker = CompletionWorker(complete_fn=fake_complete)
        worker.start()
        try:
            worker.request_warmup("from manimlib import *\n", "scene.py")
            qtbot.waitUntil(lambda: bool(calls), timeout=3000)
            assert calls[0] == (
                "from manimlib import *\n",
                len("from manimlib import *\n"),
                "scene.py",
            )
        finally:
            worker.stop()
