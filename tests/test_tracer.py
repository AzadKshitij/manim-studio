"""Unit tests for the runtime tracer."""

from __future__ import annotations

import pytest

from manim_editor.tracer import (
    TraceRecorder,
    describe_play_arguments,
)
from tests.helpers import FakeBuilder, FakeMobject, FakeScene, make_animation


class TestDescribePlayArguments:
    def test_empty(self) -> None:
        assert describe_play_arguments(()) == "Play"

    def test_single_animation_uses_class_name(self) -> None:
        assert describe_play_arguments((make_animation("ShowCreation"),)) == "ShowCreation"

    def test_multiple_animations_joined(self) -> None:
        args = (make_animation("ShowCreation"), make_animation("Transform"))
        assert describe_play_arguments(args) == "ShowCreation+Transform"

    def test_duplicates_collapsed(self) -> None:
        args = (make_animation("FadeIn"), make_animation("FadeIn"))
        assert describe_play_arguments(args) == "FadeIn"

    def test_long_lists_truncated(self) -> None:
        args = tuple(make_animation(name) for name in "ABCDE")
        assert describe_play_arguments(args) == "A+B+3"

    def test_builder_named_after_mobject(self) -> None:
        builder = FakeBuilder(FakeMobject("Circle"))
        assert describe_play_arguments((builder,)) == "Animate(FakeMobject)"

    def test_plain_mobject_falls_back_to_class_name(self) -> None:
        assert describe_play_arguments((FakeMobject(),)) == "FakeMobject"


class TestPlayAndWaitBlocks:
    def test_blocks_recorded_in_order_with_real_durations(self) -> None:
        scene = FakeScene()
        recorder = TraceRecorder()
        recorder.attach(scene)
        scene.play(make_animation("ShowCreation"), run_time=1.0)
        scene.wait(2.5)
        scene.play(make_animation("FadeOut"), run_time=0.5)
        snapshot = recorder.snapshot()

        assert [b.index for b in snapshot.blocks] == [0, 1, 2]
        assert [b.name for b in snapshot.blocks] == ["ShowCreation", "Wait", "FadeOut"]
        assert [b.start for b in snapshot.blocks] == [0.0, 1.0, 3.5]
        assert [b.duration for b in snapshot.blocks] == [1.0, 2.5, 0.5]
        assert snapshot.total_duration == pytest.approx(4.0)
        assert snapshot.stats == {"play": 2, "wait": 1}

    def test_loop_produces_one_block_per_iteration(self) -> None:
        scene = FakeScene()
        recorder = TraceRecorder()
        recorder.attach(scene)
        for _ in range(5):
            scene.play(make_animation("Transform"), run_time=0.2)
        snapshot = recorder.snapshot()
        assert len(snapshot.blocks) == 5
        assert [b.start for b in snapshot.blocks] == pytest.approx([0.0, 0.2, 0.4, 0.6, 0.8])
        assert all(b.duration == pytest.approx(0.2) for b in snapshot.blocks)

    def test_run_time_kwarg_overrides_animation_default(self) -> None:
        scene = FakeScene()
        recorder = TraceRecorder()
        recorder.attach(scene)
        scene.play(make_animation("Wait", run_time=3.0), run_time=0.1)
        assert recorder.snapshot().blocks[0].duration == pytest.approx(0.1)

    def test_play_without_animations_is_not_a_block(self) -> None:
        scene = FakeScene()
        recorder = TraceRecorder()
        recorder.attach(scene)
        scene.play()
        snapshot = recorder.snapshot()
        assert snapshot.blocks == ()
        assert snapshot.stats["play"] == 1

    def test_exception_still_records_block_and_propagates(self) -> None:
        class ExplodingScene(FakeScene):
            def play(self, *animations, run_time=None, **kwargs):
                self.time += 0.25
                raise RuntimeError("boom")

        exploding = ExplodingScene()
        recorder = TraceRecorder()
        recorder.attach(exploding)
        with pytest.raises(RuntimeError, match="boom"):
            exploding.play(make_animation("Transform"), run_time=0.25)
        snapshot = recorder.snapshot()
        assert len(snapshot.blocks) == 1
        assert snapshot.blocks[0].duration == pytest.approx(0.25)

    def test_zero_duration_wait_is_allowed(self) -> None:
        scene = FakeScene()
        recorder = TraceRecorder()
        recorder.attach(scene)
        scene.wait(0.0)
        snapshot = recorder.snapshot()
        assert snapshot.blocks[0].duration == pytest.approx(0.0)

    def test_nested_play_calls_do_not_duplicate_index(self) -> None:
        scene = FakeScene()
        recorder = TraceRecorder()
        recorder.attach(scene)

        original_play = scene.play

        def outer(*args, **kwargs):
            original_play(*args, **kwargs)
            return "outer"

        scene.play = outer  # type: ignore[method-assign]
        # Attach again on a new recorder over the already wrapped method.
        recorder2 = TraceRecorder()
        recorder2.attach(scene)
        scene.play(make_animation("Transform"), run_time=0.5)
        snapshot = recorder2.snapshot()
        assert len(snapshot.blocks) == 1


class TestEventsAndStats:
    def test_api_calls_are_counted(self) -> None:
        scene = FakeScene()
        recorder = TraceRecorder()
        recorder.attach(scene)
        mob = FakeMobject()
        scene.add(mob)
        scene.remove(mob)
        scene.clear()
        scene.save_state()
        scene.undo()
        scene.redo()
        stats = recorder.snapshot().stats
        assert stats == {"add": 1, "remove": 1, "clear": 1, "save_state": 1, "undo": 1, "redo": 1}

    def test_events_carry_time_and_detail(self) -> None:
        scene = FakeScene()
        recorder = TraceRecorder()
        recorder.attach(scene)
        scene.play(make_animation("Transform"), run_time=1.0)
        scene.add(FakeMobject("Circle"))
        events = recorder.snapshot().events
        assert [e.kind for e in events] == ["add"]
        assert events[0].time == pytest.approx(1.0)
        assert events[0].detail == "FakeMobject"
        assert events[0].during_play is False

    def test_internal_adds_during_play_flagged(self) -> None:
        scene = FakeScene(internal_adds=True)
        recorder = TraceRecorder()
        recorder.attach(scene)
        scene.play(make_animation("ShowCreation"), run_time=1.0)
        events = recorder.snapshot().events
        assert len(events) == 1
        assert events[0].kind == "add"
        assert events[0].during_play is True

    def test_events_can_be_disabled(self) -> None:
        scene = FakeScene()
        recorder = TraceRecorder(record_events=False)
        recorder.attach(scene)
        scene.add(FakeMobject())
        snapshot = recorder.snapshot()
        assert snapshot.events == ()
        assert snapshot.stats["add"] == 1

    def test_snapshot_to_dict_shape(self) -> None:
        scene = FakeScene()
        recorder = TraceRecorder()
        recorder.attach(scene)
        scene.play(make_animation("ShowCreation"), run_time=0.5)
        payload = recorder.snapshot().to_dict()
        assert payload["version"] == 1
        assert payload["blocks"][0]["name"] == "ShowCreation"
        assert payload["total_duration"] == pytest.approx(0.5)
        assert "stats" in payload and "events" in payload


class TestLifecycle:
    def test_detach_restores_originals(self) -> None:
        scene = FakeScene()
        recorder = TraceRecorder()
        recorder.attach(scene)
        recorder.detach()
        assert not recorder.attached
        scene.play(make_animation("Transform"), run_time=1.0)
        assert recorder.snapshot().blocks == ()

    def test_attach_twice_raises(self) -> None:
        scene = FakeScene()
        recorder = TraceRecorder()
        recorder.attach(scene)
        with pytest.raises(RuntimeError, match="already attached"):
            recorder.attach(scene)

    def test_context_manager_detaches(self) -> None:
        scene = FakeScene()
        with TraceRecorder() as recorder:
            recorder.attach(scene)
        assert not recorder.attached

    def test_reset_clears_recordings(self) -> None:
        scene = FakeScene()
        recorder = TraceRecorder()
        recorder.attach(scene)
        scene.play(make_animation("Transform"), run_time=1.0)
        recorder.reset()
        snapshot = recorder.snapshot()
        assert snapshot.blocks == () and snapshot.stats == {}
