"""Unit tests for the sidecar file and patch undo stack."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest

from manim_studio.patches import Anchor, Delete, SetDuration
from manim_studio.sidecar import (
    PatchStack,
    SidecarError,
    load_sidecar,
    save_sidecar,
    sidecar_path,
)

if TYPE_CHECKING:
    from pathlib import Path

A = SetDuration(Anchor("s:play:aa:0"), 2.0)
B = SetDuration(Anchor("s:wait:bb:0"), 1.0)


def test_sidecar_path_sits_next_to_the_scene() -> None:
    assert sidecar_path("a/b/foo.py").as_posix() == "a/b/foo.studio.json"


class TestFile:
    def test_missing_file_means_no_patches(self, tmp_path: Path) -> None:
        assert load_sidecar(tmp_path / "scene.py") == ()

    def test_round_trip(self, tmp_path: Path) -> None:
        scene = tmp_path / "scene.py"
        written = save_sidecar(scene, [A, Delete(Anchor("s:wait:bb:0", 1))])
        assert written == sidecar_path(scene)
        assert load_sidecar(scene) == (A, Delete(Anchor("s:wait:bb:0", 1)))
        assert not list(tmp_path.glob("*.tmp"))

    def test_saving_nothing_removes_the_file(self, tmp_path: Path) -> None:
        scene = tmp_path / "scene.py"
        save_sidecar(scene, [A])
        assert save_sidecar(scene, []) is None
        assert not sidecar_path(scene).exists()
        assert save_sidecar(scene, []) is None  # already gone: still fine

    @pytest.mark.parametrize(
        "content",
        [
            "not json",
            "[]",
            json.dumps({"version": 99, "patches": []}),
            json.dumps({"version": 1, "patches": "x"}),
            json.dumps({"version": 1, "patches": [{"op": "bogus", "site": "a"}]}),
        ],
    )
    def test_bad_files_raise_a_clear_error(self, tmp_path: Path, content: str) -> None:
        scene = tmp_path / "scene.py"
        sidecar_path(scene).write_text(content, encoding="utf-8")
        with pytest.raises(SidecarError):
            load_sidecar(scene)


class TestPatchStack:
    def test_add_undo_redo(self) -> None:
        stack = PatchStack()
        assert stack.add(A) is True
        assert stack.add(B) is True
        assert stack.patches == (A, B)
        assert stack.undo() and stack.patches == (A,)
        assert stack.redo() and stack.patches == (A, B)
        assert stack.can_redo is False

    def test_a_new_edit_clears_redo(self) -> None:
        stack = PatchStack()
        stack.add(A)
        stack.undo()
        stack.add(B)
        assert stack.can_redo is False

    def test_no_op_edits_do_not_touch_history(self) -> None:
        stack = PatchStack([A])
        revision = stack.revision
        assert stack.add(A) is False
        assert stack.discard(B) is False
        assert stack.discard_site("missing") is False
        assert stack.clear() is True and stack.clear() is False
        assert stack.revision == revision + 1

    def test_revision_never_repeats(self) -> None:
        stack = PatchStack()
        seen = {stack.revision}
        for step in (
            lambda: stack.add(A),
            stack.undo,
            stack.redo,
            lambda: stack.add(B),
        ):
            step()
            assert stack.revision not in seen
            seen.add(stack.revision)

    def test_discard_and_discard_site(self) -> None:
        iteration = SetDuration(Anchor("s:play:aa:0", 2), 5.0)
        stack = PatchStack([A, iteration, B])
        assert stack.discard(B) and stack.patches == (A, iteration)
        assert stack.discard_site("s:play:aa:0") and stack.is_empty

    def test_replace_and_undo_back(self) -> None:
        stack = PatchStack([A, B])
        assert stack.replace([A]) is True
        assert stack.patches == (A,)
        assert stack.undo() and stack.patches == (A, B)

    def test_undo_on_empty_history_is_false(self) -> None:
        stack = PatchStack()
        assert stack.undo() is False and stack.redo() is False
