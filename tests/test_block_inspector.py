"""Tests for the editable block inspector."""

from __future__ import annotations

import pytest
from PySide6.QtTest import QSignalSpy

from manim_studio.editor.block_inspector import BlockInfo, BlockInspector


@pytest.fixture
def inspector(qtbot) -> BlockInspector:
    widget = BlockInspector()
    qtbot.addWidget(widget)
    widget.show()
    return widget


def info(**overrides) -> BlockInfo:  # type: ignore[no-untyped-def]
    base = {
        "index": 3,
        "name": "FadeIn",
        "duration": 0.5,
        "line": 71,
        "animations": "FadeIn(Square) 0.50s",
        "editable": True,
        "duration_kind": "literal",
    }
    base.update(overrides)
    return BlockInfo(**base)  # type: ignore[arg-type]


class TestDisplay:
    def test_empty_inspector_is_disabled(self, inspector: BlockInspector) -> None:
        assert inspector.info is None
        assert not inspector.duration_spin.isEnabled()
        assert inspector.title.text() == ""

    def test_shows_the_block(self, inspector: BlockInspector) -> None:
        inspector.show_block(info())
        assert inspector.title.text() == "#3  FadeIn"
        assert inspector.duration_spin.value() == pytest.approx(0.5)
        assert inspector.duration_spin.isEnabled()
        assert "line 71" in inspector.details.text()
        assert "FadeIn(Square)" in inspector.details.text()
        assert inspector.note.text() == ""

    def test_scope_selector_only_appears_for_loop_bodies(self, inspector: BlockInspector) -> None:
        inspector.show_block(info(loops=1))
        assert inspector.scope.isHidden()
        inspector.show_block(info(loops=3))
        assert not inspector.scope.isHidden()
        assert "loop" in inspector.details.text() and "3" in inspector.details.text()

    def test_notes_explain_how_the_duration_is_written(self, inspector: BlockInspector) -> None:
        inspector.show_block(info(duration_kind="expr"))
        assert "expression" in inspector.note.text()
        inspector.show_block(info(duration_kind="absent"))
        assert "adds one" in inspector.note.text()

    def test_blocks_without_a_call_site_are_read_only(self, inspector: BlockInspector) -> None:
        inspector.show_block(info(editable=False))
        assert not inspector.duration_spin.isEnabled()

    def test_edited_blocks_say_so(self, inspector: BlockInspector) -> None:
        inspector.show_block(info(edited=True))
        assert "edited" in inspector.details.text()

    def test_the_block_actions_live_in_the_timeline_toolbar_not_here(
        self, inspector: BlockInspector
    ) -> None:
        # delete / reset / jump-to-code are timeline-toolbar buttons (TimelineTransport).
        for name in ("jump_button", "reset_button", "delete_button"):
            assert not hasattr(inspector, name)


class TestEditing:
    def test_committing_a_new_duration_emits_it(self, inspector: BlockInspector) -> None:
        spy = QSignalSpy(inspector.durationCommitted)
        inspector.show_block(info())
        inspector.duration_spin.setValue(1.25)
        inspector.duration_spin.editingFinished.emit()
        assert spy.count() == 1
        assert spy.at(0) == [3, 1.25, False]

    def test_an_unchanged_value_does_not_emit(self, inspector: BlockInspector) -> None:
        spy = QSignalSpy(inspector.durationCommitted)
        inspector.show_block(info())
        inspector.duration_spin.editingFinished.emit()  # focus left, nothing typed
        assert spy.count() == 0

    def test_this_iteration_scope_is_reported(self, inspector: BlockInspector) -> None:
        spy = QSignalSpy(inspector.durationCommitted)
        inspector.show_block(info(loops=3))
        inspector.scope.setCurrentIndex(1)
        inspector.duration_spin.setValue(2.0)
        inspector.duration_spin.editingFinished.emit()
        assert spy.at(0) == [3, 2.0, True]

    def test_read_only_blocks_never_emit(self, inspector: BlockInspector) -> None:
        spy = QSignalSpy(inspector.durationCommitted)
        inspector.show_block(info(editable=False))
        inspector.duration_spin.setValue(2.0)
        inspector.duration_spin.editingFinished.emit()
        assert spy.count() == 0

    def test_an_identical_refresh_keeps_a_half_typed_value(self, inspector: BlockInspector) -> None:
        inspector.show_block(info())
        inspector.duration_spin.setValue(0.9)  # typed, not committed
        inspector.show_block(info())  # e.g. a scrub tick refreshes the inspector
        assert inspector.duration_spin.value() == pytest.approx(0.9)


class _Clip:
    def __init__(self, animation: str, target: str, duration: float) -> None:
        self.animation, self.target, self.duration = animation, target, duration


class TestLongTextNeverWidensTheWindow:
    """A block that animates 72 squares once pushed the window to ~13,700 px, off screen."""

    def test_identical_clips_are_grouped(self) -> None:
        from manim_studio.editor.block_inspector import describe_clips

        grid = [_Clip("Animate", "Square", 1.0) for _ in range(72)]
        assert describe_clips(grid) == "Animate(Square) \u00d772 1.00s"

    def test_distinct_clips_keep_their_order(self) -> None:
        from manim_studio.editor.block_inspector import describe_clips

        clips = [
            _Clip("Write", "Text", 2.767),
            _Clip("FadeIn", "Dot", 0.1),
            _Clip("FadeIn", "Dot", 0.1),
        ]
        assert describe_clips(clips) == "Write(Text) 2.77s, FadeIn(Dot) \u00d72 0.10s"

    def test_a_huge_details_text_does_not_raise_the_minimum_width(self, qtbot) -> None:  # type: ignore[no-untyped-def]
        inspector = BlockInspector()
        qtbot.addWidget(inspector)
        inspector.resize(900, 30)  # a realistic width: the details label gets real room
        inspector.show()
        baseline = inspector.minimumSizeHint().width()
        info = BlockInfo(
            index=15,
            name="Animate(Square)",
            duration=1.0,
            line=127,
            animations=", ".join("Animate(Square) 1.00s" for _ in range(100)),
            loops=1,
            edited=False,
            duration_kind=None,
            editable=True,
        )
        inspector.show_block(info)
        qtbot.wait(20)
        assert inspector.minimumSizeHint().width() <= baseline + 5
        assert inspector.details.text().startswith("line 127")
        assert "Animate(Square) 1.00s" in inspector.details.toolTip()  # the whole text is there

    def test_a_very_long_title_is_elided_too(self, qtbot) -> None:  # type: ignore[no-untyped-def]
        inspector = BlockInspector()
        qtbot.addWidget(inspector)
        inspector.show()
        baseline = inspector.minimumSizeHint().width()
        inspector.show_block(
            BlockInfo(
                index=1,
                name="X" * 3000,
                duration=1.0,
                line=None,
                animations="",
                loops=1,
                edited=False,
                duration_kind=None,
                editable=True,
            )
        )
        qtbot.wait(20)
        assert inspector.minimumSizeHint().width() <= baseline + 5
