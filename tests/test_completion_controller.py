"""Tests for the completion controller: worker lifecycle and result routing.

The controller's real ``CompletionWorker`` is kept, but its Jedi lookups are
replaced with fakes so signals resolve instantly and deterministically.
"""

from __future__ import annotations

import pytest
from PySide6.QtCore import QObject

from manim_studio.editor import completion_controller as module
from manim_studio.editor.completion_controller import CompletionController
from manim_studio.editor.completion_worker import CompletionWorker
from manim_studio.python_completion import Completion, Signature


class _FakeEditor:
    def __init__(self) -> None:
        self.completions: list[tuple[int, list[str]]] = []
        self.calltips: list[tuple[int, str]] = []

    def show_completions(self, offset: int, inserts: list[str]) -> None:
        self.completions.append((offset, inserts))

    def show_calltip(self, offset: int, text: str) -> None:
        self.calltips.append((offset, text))


def _fake_complete(source: str, offset: int, path: str | None) -> tuple[Completion, ...]:
    return (Completion(name="append", insert="append", kind="function"),)


def _fake_signatures(source: str, offset: int, path: str | None) -> tuple[Signature, ...]:
    return (Signature(label="greet(name)", active_parameter=0),)


def _no_signatures(source: str, offset: int, path: str | None) -> tuple[Signature, ...]:
    return ()


def _patch_worker(monkeypatch: pytest.MonkeyPatch, *, signatures=_fake_signatures) -> None:
    """Make controller-created workers use fake Jedi functions."""
    real = CompletionWorker

    def factory(parent: QObject | None) -> CompletionWorker:
        return real(parent, complete_fn=_fake_complete, signature_fn=signatures)

    monkeypatch.setattr(module, "CompletionWorker", factory)


class TestDisabled:
    def test_inert_when_disabled(self) -> None:
        editor = _FakeEditor()
        controller = CompletionController(editor, enabled=False)  # type: ignore[arg-type]
        assert controller.worker is None
        controller.request_completions("src", 3, None)
        controller.request_signatures("src", 3, None)
        controller.warm_up("src", None)
        controller.stop()
        assert editor.completions == []
        assert editor.calltips == []


class TestRouting:
    def test_completions_reach_the_editor(
        self,
        qtbot,
        monkeypatch: pytest.MonkeyPatch,  # type: ignore[no-untyped-def]
    ) -> None:
        _patch_worker(monkeypatch)
        editor = _FakeEditor()
        controller = CompletionController(editor)  # type: ignore[arg-type]
        try:
            controller.request_completions("values.ap", 9, "scene.py")
            qtbot.waitUntil(lambda: bool(editor.completions), timeout=3000)
            assert editor.completions == [(9, ["append"])]
        finally:
            controller.stop()

    def test_signatures_reach_the_editor(
        self,
        qtbot,
        monkeypatch: pytest.MonkeyPatch,  # type: ignore[no-untyped-def]
    ) -> None:
        _patch_worker(monkeypatch)
        editor = _FakeEditor()
        controller = CompletionController(editor)  # type: ignore[arg-type]
        try:
            controller.request_signatures("greet(", 6, None)
            qtbot.waitUntil(lambda: bool(editor.calltips), timeout=3000)
            assert editor.calltips == [(6, "greet(name)")]
        finally:
            controller.stop()

    def test_empty_signatures_do_not_show_calltip(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _patch_worker(monkeypatch, signatures=_no_signatures)
        editor = _FakeEditor()
        controller = CompletionController(editor)  # type: ignore[arg-type]
        controller._on_signatures_ready(2, ())
        assert editor.calltips == []


class TestLifecycle:
    def test_first_request_starts_the_worker(
        self,
        qtbot,
        monkeypatch: pytest.MonkeyPatch,  # type: ignore[no-untyped-def]
    ) -> None:
        _patch_worker(monkeypatch)
        editor = _FakeEditor()
        controller = CompletionController(editor)  # type: ignore[arg-type]
        try:
            assert controller.worker is not None
            assert controller.worker.isRunning() is False
            controller.warm_up("from manimlib import *\n", "scene.py")
            qtbot.waitUntil(
                lambda: controller.worker is not None and controller.worker.isRunning(),
                timeout=3000,
            )
        finally:
            controller.stop()

    def test_stop_stops_the_worker(
        self,
        qtbot,
        monkeypatch: pytest.MonkeyPatch,  # type: ignore[no-untyped-def]
    ) -> None:
        _patch_worker(monkeypatch)
        editor = _FakeEditor()
        controller = CompletionController(editor)  # type: ignore[arg-type]
        controller.request_completions("x", 1, None)
        qtbot.waitUntil(lambda: controller.worker is not None and controller.worker.isRunning())
        controller.stop()
        assert controller.worker is not None
        assert controller.worker.isRunning() is False
