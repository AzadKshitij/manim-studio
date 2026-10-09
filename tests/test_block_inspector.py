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
        assert not inspector.delete_button.isEnabled()

    def test_reset_is_only_enabled_for_edited_blocks(self, inspector: BlockInspector) -> None:
        inspector.show_block(info(edited=False))
        assert not inspector.reset_button.isEnabled()
        inspector.show_block(info(edited=True))
        assert inspector.reset_button.isEnabled()
        assert "edited" in inspector.details.text()

    def test_jump_needs_a_known_line(self, inspector: BlockInspector) -> None:
        inspector.show_block(info(line=None))
        assert not inspector.jump_button.isEnabled()


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

    def test_buttons_emit_the_block_index(self, inspector: BlockInspector) -> None:
        jump = QSignalSpy(inspector.jumpRequested)
        reset = QSignalSpy(inspector.resetRequested)
        delete = QSignalSpy(inspector.deleteRequested)
        inspector.show_block(info(edited=True))
        inspector.jump_button.click()
        inspector.reset_button.click()
        inspector.delete_button.click()
        assert [jump.at(0)[0], reset.at(0)[0], delete.at(0)[0]] == [3, 3, 3]

    def test_an_identical_refresh_keeps_a_half_typed_value(self, inspector: BlockInspector) -> None:
        inspector.show_block(info())
        inspector.duration_spin.setValue(0.9)  # typed, not committed
        inspector.show_block(info())  # e.g. a scrub tick refreshes the inspector
        assert inspector.duration_spin.value() == pytest.approx(0.9)
