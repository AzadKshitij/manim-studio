"""Autosave snapshots and crash recovery: the pure rules, the writer and the window."""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest

from manim_studio import autosave
from manim_studio.editor import main_window as main_window_module
from manim_studio.editor.main_window import MainWindow
from manim_studio.editor.unsaved import DISCARD_RECOVERY, RECOVER

SCENE_FILE = Path(__file__).parent / "scenes" / "feature_showcase.py"


class TestRules:
    def test_snapshot_is_a_hidden_sibling_of_the_scene(self, tmp_path: Path) -> None:
        assert autosave.autosave_path(tmp_path / "demo.py") == tmp_path / ".demo.py.autosave"

    def test_snapshot_is_kept_only_while_edited_and_different(self) -> None:
        assert autosave.should_keep_snapshot("x", "y", True)
        assert autosave.should_keep_snapshot("x", None, True)  # the scene is missing on disk
        assert not autosave.should_keep_snapshot("x", "x", True)
        assert not autosave.should_keep_snapshot("x", "y", False)

    def test_recovery_is_offered_only_for_a_newer_snapshot_with_other_code(self) -> None:
        assert not autosave.should_offer_recovery(None, None, "a", 1.0)
        assert not autosave.should_offer_recovery("b", 0.5, "a", 1.0)  # older than the file
        assert not autosave.should_offer_recovery("a", 2.0, "a", 1.0)  # the same code
        assert autosave.should_offer_recovery("b", 2.0, "a", 1.0)
        assert autosave.should_offer_recovery("b", 2.0, None, None)  # no file on disk


@pytest.fixture
def scene(tmp_path: Path) -> Path:
    path = tmp_path / "demo.py"
    shutil.copy(SCENE_FILE, path)
    return path


def _window(qtbot, scene: Path) -> MainWindow:  # type: ignore[no-untyped-def]
    win = MainWindow(restore_settings=False, auto_preview=False, completion=False)
    qtbot.addWidget(win)
    assert win.open_path(scene)
    win._autosave.set_interval(0)
    return win


class TestWriter:
    def test_edits_are_snapshotted_after_a_pause(self, qtbot, scene: Path) -> None:  # type: ignore[no-untyped-def]
        window = _window(qtbot, scene)
        window.editor.insert_line_at(1, "# edited")
        snapshot = autosave.autosave_path(scene)
        qtbot.waitUntil(snapshot.exists)
        assert snapshot.read_text(encoding="utf-8") == window.editor.text()

    def test_clean_buffer_has_no_snapshot(self, qtbot, scene: Path) -> None:  # type: ignore[no-untyped-def]
        window = _window(qtbot, scene)
        window._autosave.snapshot()
        assert not autosave.autosave_path(scene).exists()

    def test_undo_back_to_the_saved_code_removes_the_snapshot(self, qtbot, scene: Path) -> None:  # type: ignore[no-untyped-def]
        window = _window(qtbot, scene)
        window.editor.insert_line_at(1, "# edited")
        snapshot = autosave.autosave_path(scene)
        qtbot.waitUntil(snapshot.exists)
        window.editor.undo()
        window._autosave.snapshot()
        assert not snapshot.exists()

    def test_retyping_the_saved_code_removes_the_snapshot(self, qtbot, scene: Path) -> None:  # type: ignore[no-untyped-def]
        window = _window(qtbot, scene)
        window.editor.insert_line_at(1, "# edited")
        snapshot = autosave.autosave_path(scene)
        qtbot.waitUntil(snapshot.exists)
        # The same code typed back: the editor still reports unsaved edits, but nothing differs.
        window.editor.replace_text(scene.read_text(encoding="utf-8"))
        assert window.editor.is_modified()
        window._autosave.snapshot()
        assert not snapshot.exists()

    def test_saving_removes_the_snapshot(self, qtbot, scene: Path) -> None:  # type: ignore[no-untyped-def]
        window = _window(qtbot, scene)
        window.editor.insert_line_at(1, "# edited")
        snapshot = autosave.autosave_path(scene)
        qtbot.waitUntil(snapshot.exists)
        window.save_buffer()
        assert not snapshot.exists()
        assert "# edited" in scene.read_text(encoding="utf-8")


def _crash_with_edit(qtbot, scene: Path) -> Path:  # type: ignore[no-untyped-def]
    """Leave a snapshot behind, as a crash would: the session never closes cleanly."""
    first = _window(qtbot, scene)
    first.editor.insert_line_at(1, "# edited")
    snapshot = autosave.autosave_path(scene)
    qtbot.waitUntil(snapshot.exists)
    first._autosave.set_path(None)  # stop its timer; the snapshot file stays behind
    first.editor.load_file(scene)  # and nothing unsaved is left in that window
    return snapshot


class TestRecovery:
    def test_recover_restores_the_unsaved_code(
        self, qtbot, scene: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:  # type: ignore[no-untyped-def]
        snapshot = _crash_with_edit(qtbot, scene)
        monkeypatch.setattr(main_window_module, "ask_recovery", lambda *a, **k: RECOVER)
        second = _window(qtbot, scene)
        assert "# edited" in second.editor.text()
        assert second.editor.is_modified()  # recovered edits stay unsaved until saved
        assert snapshot.exists()

    def test_discard_recovery_removes_the_snapshot(
        self, qtbot, scene: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:  # type: ignore[no-untyped-def]
        snapshot = _crash_with_edit(qtbot, scene)
        monkeypatch.setattr(main_window_module, "ask_recovery", lambda *a, **k: DISCARD_RECOVERY)
        second = _window(qtbot, scene)
        assert "# edited" not in second.editor.text()
        assert not snapshot.exists()

    def test_the_timer_cannot_remove_the_snapshot_during_the_prompt(
        self, qtbot, scene: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:  # type: ignore[no-untyped-def]
        snapshot = _crash_with_edit(qtbot, scene)
        seen: list[bool] = []
        windows: list[MainWindow] = []

        def prompt_while_the_timer_fires(parent: object, name: str) -> str:
            # What a debounced snapshot does inside the prompt's nested event loop.
            windows[0]._autosave.snapshot()
            seen.append(snapshot.exists())
            return RECOVER

        monkeypatch.setattr(main_window_module, "ask_recovery", prompt_while_the_timer_fires)
        second = MainWindow(restore_settings=False, auto_preview=False, completion=False)
        qtbot.addWidget(second)
        windows.append(second)
        assert second.open_path(scene)
        assert seen == [True]

    def test_no_prompt_without_a_snapshot(
        self, qtbot, scene: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:  # type: ignore[no-untyped-def]
        def fail(*_args: object) -> str:
            raise AssertionError("asked to recover with nothing to recover")

        monkeypatch.setattr(main_window_module, "ask_recovery", fail)
        _window(qtbot, scene)

    def test_no_prompt_when_the_scene_was_saved_after_the_snapshot(
        self, qtbot, scene: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:  # type: ignore[no-untyped-def]
        snapshot = _crash_with_edit(qtbot, scene)
        later = snapshot.stat().st_mtime + 60
        os.utime(scene, (later, later))  # saved after the snapshot was written

        def fail(*_args: object) -> str:
            raise AssertionError("asked to recover a snapshot older than the scene")

        monkeypatch.setattr(main_window_module, "ask_recovery", fail)
        _window(qtbot, scene)
