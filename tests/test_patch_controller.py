"""Unit tests for the editor-side timeline edit controller (no host process)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest

from manim_studio.editor.patch_controller import BlockRef, PatchController
from manim_studio.patches import Anchor, Delete, SetDuration
from manim_studio.sidecar import load_sidecar, save_sidecar, sidecar_path
from manim_studio.site_index import index_source

if TYPE_CHECKING:
    from pathlib import Path

SOURCE = """\
class Demo(Scene):
    def construct(self):
        self.play(FadeIn(a))
        for i in range(3):
            self.play(FadeIn(b), run_time=0.5)
        self.wait(1)
"""


@pytest.fixture
def scene(tmp_path: Path) -> Path:
    path = tmp_path / "scene.py"
    path.write_text(SOURCE, encoding="utf-8")
    return path


@pytest.fixture
def sites() -> list[str]:
    return [site.site_id for site in index_source(SOURCE).sites]


def host_log(sites: list[str], *, orphaned: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """The log event the host would send for SOURCE (play, 3 loop iterations, wait)."""
    return {
        "sites": [
            {"id": sites[0], "occ": 0},
            {"id": sites[1], "occ": 0},
            {"id": sites[1], "occ": 1},
            {"id": sites[1], "occ": 2},
            {"id": sites[2], "occ": 0},
        ],
        "patches": {"applied": 0, "orphaned": orphaned or []},
    }


@pytest.fixture
def controller(scene: Path, sites: list[str]) -> PatchController:
    controller = PatchController()
    controller.open_scene(scene)
    controller.on_log(host_log(sites))
    return controller


class Recorder:
    def __init__(self, controller: PatchController) -> None:
        self.payloads: list[list[dict[str, Any]]] = []
        self.messages: list[str] = []
        controller.patchesChanged.connect(self.payloads.append)
        controller.message.connect(self.messages.append)


class TestSceneLifecycle:
    def test_loads_existing_edits_from_the_sidecar(self, scene: Path, sites: list[str]) -> None:
        patch = SetDuration(Anchor(sites[2]), 4.0)
        save_sidecar(scene, [patch])
        controller = PatchController()
        payload = controller.open_scene(scene)
        assert payload == [patch.to_dict()]
        assert controller.edit_count == 1

    def test_a_broken_sidecar_is_reported_and_ignored(self, scene: Path) -> None:
        sidecar_path(scene).write_text("not json", encoding="utf-8")
        controller = PatchController()
        recorder = Recorder(controller)
        assert controller.open_scene(scene) == []
        assert "ignoring timeline edits" in recorder.messages[0]

    def test_open_scene_starts_a_fresh_history(
        self, controller: PatchController, scene: Path
    ) -> None:
        controller.set_block_duration(4, 3.0)
        assert controller.can_undo
        controller.open_scene(scene)
        assert controller.can_undo is False


class TestBlockMapping:
    def test_block_refs_come_from_the_log(
        self, controller: PatchController, sites: list[str]
    ) -> None:
        assert controller.block_ref(2) == BlockRef(sites[1], 1)
        assert controller.block_ref(99) is None

    def test_iteration_count_is_the_loop_fan_out(self, controller: PatchController) -> None:
        assert [controller.iteration_count(i) for i in range(5)] == [1, 3, 3, 3, 1]

    def test_blocks_without_a_known_site(self, controller: PatchController) -> None:
        controller.on_log({"sites": [None, {"id": "x", "occ": 0}, "junk"]})
        assert controller.block_ref(0) is None
        assert controller.block_ref(1) == BlockRef("x", 0)
        assert controller.block_ref(2) is None


class TestEditing:
    def test_setting_a_duration_writes_a_patch_persists_and_notifies(
        self, controller: PatchController, scene: Path, sites: list[str]
    ) -> None:
        recorder = Recorder(controller)
        assert controller.set_block_duration(4, 2.5) is True
        patch = SetDuration(Anchor(sites[2]), 2.5)
        assert controller.patches == (patch,)
        assert load_sidecar(scene) == (patch,)
        assert recorder.payloads == [[patch.to_dict()]]
        assert controller.block_is_edited(4) and not controller.block_is_edited(0)

    def test_editing_a_loop_block_edits_the_call_site_for_all_iterations(
        self, controller: PatchController, sites: list[str]
    ) -> None:
        controller.set_block_duration(2, 2.0)
        assert controller.patches == (SetDuration(Anchor(sites[1]), 2.0),)

    def test_iteration_only_keeps_the_other_iterations_value(
        self, controller: PatchController, sites: list[str]
    ) -> None:
        controller.set_block_duration(2, 3.0, iteration_only=True)
        # The loop body already has run_time=0.5 in the source, so no default patch.
        assert controller.patches == (SetDuration(Anchor(sites[1], 1), 3.0),)

    def test_iteration_only_on_an_absent_duration_first_writes_the_default(
        self, scene: Path, sites: list[str]
    ) -> None:
        source = SOURCE.replace(", run_time=0.5", "")
        scene.write_text(source, encoding="utf-8")
        controller = PatchController()
        controller.open_scene(scene)
        controller.on_log(host_log([s.site_id for s in index_source(source).sites]))
        assert controller.set_block_duration(2, 3.0, iteration_only=True) is False
        assert controller.set_block_duration(2, 3.0, iteration_only=True, sibling_duration=1.0)
        new_sites = [s.site_id for s in index_source(source).sites]
        assert controller.patches == (
            SetDuration(Anchor(new_sites[1]), 1.0),
            SetDuration(Anchor(new_sites[1], 1), 3.0),
        )

    def test_iteration_only_outside_a_loop_is_a_plain_edit(
        self, controller: PatchController, sites: list[str]
    ) -> None:
        controller.set_block_duration(0, 2.0, iteration_only=True)
        assert controller.patches == (SetDuration(Anchor(sites[0]), 2.0),)

    def test_unknown_blocks_and_bad_durations_are_rejected_with_a_message(
        self, controller: PatchController
    ) -> None:
        recorder = Recorder(controller)
        controller.on_log({"sites": [None]})
        assert controller.set_block_duration(0, 2.0) is False
        controller.on_log({"sites": [{"id": "x", "occ": 0}]})
        assert controller.set_block_duration(0, 0.0) is False
        assert controller.set_block_duration(0, float("nan")) is False
        assert len(recorder.messages) == 3 and not recorder.payloads
        assert controller.edit_count == 0

    def test_same_value_is_a_no_op(self, controller: PatchController) -> None:
        controller.set_block_duration(4, 2.0)
        recorder = Recorder(controller)
        assert controller.set_block_duration(4, 2.0) is False
        assert not recorder.payloads

    def test_delete_and_reset(self, controller: PatchController, sites: list[str]) -> None:
        controller.set_block_duration(4, 2.0)
        controller.delete_block(4)
        assert controller.patches == (Delete(Anchor(sites[2])),)
        assert controller.reset_block(4) is True
        assert controller.patches == ()
        assert controller.reset_block(4) is False

    def test_undo_redo_persist_and_notify(self, controller: PatchController, scene: Path) -> None:
        controller.set_block_duration(4, 2.0)
        recorder = Recorder(controller)
        assert controller.undo() is True
        assert load_sidecar(scene) == ()
        assert controller.redo() is True
        assert len(load_sidecar(scene)) == 1
        assert len(recorder.payloads) == 2

    def test_clearing_removes_the_sidecar_file(
        self, controller: PatchController, scene: Path
    ) -> None:
        controller.set_block_duration(4, 2.0)
        assert sidecar_path(scene).exists()
        controller.clear()
        assert not sidecar_path(scene).exists()


class TestOrphans:
    def test_orphans_come_from_the_host_report(
        self, controller: PatchController, sites: list[str]
    ) -> None:
        patch = SetDuration(Anchor("Gone:wait:dead:0"), 9.0)
        controller.on_log(
            host_log(sites, orphaned=[{"patch": patch.to_dict(), "reason": "it is gone"}])
        )
        assert controller.orphan_count == 1
        assert controller.orphans[0].reason == "it is gone"

    def test_malformed_orphan_entries_are_skipped(
        self, controller: PatchController, sites: list[str]
    ) -> None:
        controller.on_log(host_log(sites, orphaned=[{"patch": {"op": "bogus"}}, "junk"]))
        assert controller.orphan_count == 0

    def test_discarding_orphans_keeps_the_live_edits(
        self, controller: PatchController, sites: list[str]
    ) -> None:
        live = SetDuration(Anchor(sites[2]), 2.0)
        orphan = SetDuration(Anchor("Gone:wait:dead:0"), 9.0)
        controller.set_block_duration(4, 2.0)
        controller._stack.add(orphan)
        controller.on_log(host_log(sites, orphaned=[{"patch": orphan.to_dict(), "reason": "x"}]))
        assert controller.discard_orphans() is True
        assert controller.patches == (live,)
        assert controller.discard_orphans() is False

    def test_summary(self, controller: PatchController, sites: list[str]) -> None:
        assert controller.summary() == ""
        controller.set_block_duration(4, 2.0)
        assert controller.summary() == "1 timeline edit"
        orphan = SetDuration(Anchor("Gone:wait:dead:0"), 9.0)
        controller._stack.add(orphan)
        controller.on_log(host_log(sites, orphaned=[{"patch": orphan.to_dict(), "reason": "x"}]))
        assert controller.summary() == "2 timeline edits (1 orphaned)"


class TestApplyToSource:
    def test_plan_bakes_whole_site_edits(self, controller: PatchController) -> None:
        controller.set_block_duration(4, 2.0)
        plan = controller.plan_apply(SOURCE)
        assert plan is not None and "self.wait(2.0)" in plan.text

    def test_nothing_to_apply(self, controller: PatchController) -> None:
        assert controller.plan_apply(SOURCE) is None

    def test_finish_keeps_edits_that_could_not_be_baked(
        self, controller: PatchController, sites: list[str]
    ) -> None:
        controller.set_block_duration(4, 2.0)
        controller.set_block_duration(2, 3.0, iteration_only=True)
        plan = controller.plan_apply(SOURCE)
        assert plan is not None
        assert "self.wait(2.0)" in plan.text and "__studio__" not in plan.text
        assert controller.finish_apply(plan) is True
        assert controller.patches == (SetDuration(Anchor(sites[1], 1), 3.0),)

    def test_a_scene_that_no_longer_parses_cannot_be_planned(
        self, controller: PatchController
    ) -> None:
        controller.set_block_duration(4, 2.0)
        assert controller.plan_apply("def broken(:\n") is None


class TestDurationKind:
    def test_reports_how_each_call_spells_its_duration(self, controller: PatchController) -> None:
        # SOURCE: play without run_time, play with literal run_time (loop), wait(1)
        assert controller.duration_kind(0) == "absent"
        assert controller.duration_kind(1) == "literal"
        assert controller.duration_kind(4) == "literal"
        assert controller.duration_kind(99) is None

    def test_the_source_index_is_cached_until_the_file_changes(
        self, controller: PatchController, scene: Path, sites: list[str]
    ) -> None:
        first = controller._source_index()
        assert controller._source_index() is first  # no re-parse per call
        scene.write_text(SOURCE.replace("self.wait(1)", "self.wait(t * 2)"), encoding="utf-8")
        import os

        stat = scene.stat()
        os.utime(scene, ns=(stat.st_atime_ns, stat.st_mtime_ns + 2_000_000_000))
        assert controller._source_index() is not first
        assert controller.duration_kind(4) == "expr"
