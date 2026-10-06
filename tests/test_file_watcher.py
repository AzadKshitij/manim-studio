"""Tests for the debounced file watcher."""

from __future__ import annotations

from pathlib import Path

from manim_editor.editor.file_watcher import DebounceGate, FileWatcher


class FakeClock:
    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class TestDebounceGate:
    def test_starts_idle(self) -> None:
        gate = DebounceGate(0.5, FakeClock())
        assert not gate.pending and not gate.due()

    def test_due_after_quiet_period(self) -> None:
        clock = FakeClock()
        gate = DebounceGate(0.5, clock)
        gate.note()
        assert gate.pending and not gate.due()
        clock.advance(0.3)
        gate.note()  # another event restarts the window
        clock.advance(0.3)
        assert not gate.due()
        clock.advance(0.2)
        assert gate.due()

    def test_reset(self) -> None:
        gate = DebounceGate(0.5, FakeClock())
        gate.note()
        gate.reset()
        assert not gate.pending and not gate.due()


class TestFileWatcher:
    def test_reports_external_change(self, qtbot, tmp_path: Path) -> None:
        target = tmp_path / "scene.py"
        target.write_text("x = 1\n", encoding="utf-8")
        watcher = FileWatcher(delay_ms=50, suppress_ms=50)
        watcher.watch(target)
        with qtbot.waitSignal(watcher.file_changed, timeout=8000) as blocker:
            target.write_text("x = 2\n", encoding="utf-8")
        assert blocker.args[0] == str(target.resolve())

    def test_suppresses_editor_saves(self, qtbot, tmp_path: Path) -> None:
        target = tmp_path / "scene.py"
        target.write_text("x = 1\n", encoding="utf-8")
        watcher = FileWatcher(delay_ms=50, suppress_ms=8000)
        watcher.watch(target)
        watcher.note_save()
        target.write_text("x = 2\n", encoding="utf-8")
        with qtbot.assertNotEmitted(watcher.file_changed, wait=1500):
            pass

    def test_missing_file_emits_and_keeps_watching(self, qtbot, tmp_path: Path) -> None:
        target = tmp_path / "scene.py"
        target.write_text("x = 1\n", encoding="utf-8")
        watcher = FileWatcher(delay_ms=50)
        watcher.watch(target)
        with qtbot.waitSignal(watcher.file_missing, timeout=8000):
            target.unlink()

    def test_watch_replaces_previous(self, qtbot, tmp_path: Path) -> None:
        first = tmp_path / "a.py"
        second = tmp_path / "b.py"
        first.write_text("a\n", encoding="utf-8")
        second.write_text("b\n", encoding="utf-8")
        watcher = FileWatcher(delay_ms=50)
        watcher.watch(first)
        watcher.watch(second)
        assert watcher.path == second.resolve()

    def test_stop_clears(self, qtbot, tmp_path: Path) -> None:
        target = tmp_path / "scene.py"
        target.write_text("x\n", encoding="utf-8")
        watcher = FileWatcher(delay_ms=50)
        watcher.watch(target)
        watcher.stop()
        assert watcher.path is None
