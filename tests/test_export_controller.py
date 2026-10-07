"""Tests for the export child-process controller.

The process plumbing is exercised with the test interpreter itself as the child:
a one-liner ``python -c`` exits instantly, so the tests stay deterministic and
never invoke manimgl.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from PySide6.QtCore import QProcess

from manim_studio.editor import export_controller as module
from manim_studio.editor.export_controller import ExportController
from manim_studio.export import ExportPlan


def _plan(code: str, output: Path) -> ExportPlan:
    argv = (sys.executable, "-c", code)
    return ExportPlan(argv=argv, output_path=output, scene_name=None, quality="preview")


class _RunningProcess:
    def state(self) -> QProcess.ProcessState:
        return QProcess.ProcessState.Running


@pytest.fixture
def controller(qtbot) -> ExportController:  # type: ignore[no-untyped-def]
    return ExportController()


class TestPreviewRender:
    def test_success_emits_output_path(
        self,
        controller: ExportController,
        qtbot,  # type: ignore[no-untyped-def]
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        output = tmp_path / "demo_preview.mp4"
        output.write_bytes(b"fake")
        monkeypatch.setattr(module, "build_export_plan", lambda *a, **k: _plan("pass", output))
        statuses: list[str] = []
        controller.statusChanged.connect(statuses.append)
        with qtbot.waitSignal(controller.previewVideoReady, timeout=10_000) as blocker:
            assert controller.render_preview_video(tmp_path / "scene.py", None, output) is True
        assert blocker.args == [output]
        assert any("rendering preview video" in text for text in statuses)

    def test_failure_emits_exit_code(
        self,
        controller: ExportController,
        qtbot,  # type: ignore[no-untyped-def]
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        output = tmp_path / "demo_preview.mp4"  # never created
        monkeypatch.setattr(
            module, "build_export_plan", lambda *a, **k: _plan("import sys; sys.exit(3)", output)
        )
        with qtbot.waitSignal(controller.previewVideoFailed, timeout=10_000) as blocker:
            assert controller.render_preview_video(tmp_path / "scene.py", None, output) is True
        assert blocker.args == [3]


class TestExport:
    def test_finished_emits_exit_code(
        self,
        controller: ExportController,
        qtbot,  # type: ignore[no-untyped-def]
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        output = tmp_path / "out.mp4"
        monkeypatch.setattr(module, "build_export_plan", lambda *a, **k: _plan("pass", output))
        with qtbot.waitSignal(controller.exportFinished, timeout=10_000) as blocker:
            assert controller.export_video(tmp_path / "scene.py", "Demo", output) is True
        assert blocker.args == [0]

    def test_child_output_is_forwarded(
        self,
        controller: ExportController,
        qtbot,  # type: ignore[no-untyped-def]
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        output = tmp_path / "out.png"
        monkeypatch.setattr(
            module, "build_export_plan", lambda *a, **k: _plan("print('hello render')", output)
        )
        lines: list[str] = []
        controller.outputLine.connect(lines.append)
        with qtbot.waitSignals([controller.outputLine, controller.exportFinished], timeout=10_000):
            assert controller.export_still(tmp_path / "scene.py", None, output) is True
        assert "hello render" in lines


class TestBusyGuards:
    def test_preview_guard(self, controller: ExportController, tmp_path: Path) -> None:
        controller._preview_process = _RunningProcess()  # type: ignore[assignment]
        statuses: list[str] = []
        controller.statusChanged.connect(statuses.append)
        assert controller.render_preview_video(tmp_path / "s.py", None, tmp_path / "o.mp4") is False
        assert statuses == ["preview render already running"]

    def test_export_guard(self, controller: ExportController, tmp_path: Path) -> None:
        controller._export_process = _RunningProcess()  # type: ignore[assignment]
        statuses: list[str] = []
        controller.statusChanged.connect(statuses.append)
        assert controller.export_video(tmp_path / "s.py", None, tmp_path / "o.mp4") is False
        assert controller.export_still(tmp_path / "s.py", None, tmp_path / "o.png") is False
        assert statuses == ["export already running", "export already running"]

    def test_running_helpers(self, controller: ExportController) -> None:
        assert controller.preview_render_running() is False
        assert controller.export_running() is False
        controller._preview_process = _RunningProcess()  # type: ignore[assignment]
        assert controller.preview_render_running() is True


class TestShutdown:
    def test_shutdown_kills_child(
        self,
        controller: ExportController,
        qtbot,  # type: ignore[no-untyped-def]
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        output = tmp_path / "out.mp4"
        monkeypatch.setattr(
            module,
            "build_export_plan",
            lambda *a, **k: _plan("import time; time.sleep(30)", output),
        )
        assert controller.export_video(tmp_path / "scene.py", None, output) is True
        qtbot.waitUntil(controller.export_running, timeout=5000)
        controller.shutdown()
        assert controller.export_running() is False
