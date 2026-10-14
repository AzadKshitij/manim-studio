"""The timeline operation table and its availability rules (pure, no Qt)."""

from __future__ import annotations

import pytest

from manim_studio.timeline_ops import (
    OPS,
    OPS_BY_KEY,
    OpState,
    TimelineFacts,
    op_states,
    tooltip_for,
)

EDITING = TimelineFacts(
    running=True,
    has_log=True,
    block=2,
    block_editable=True,
    block_has_line=True,
    block_edited=True,
    can_undo=True,
    can_redo=True,
    edit_count=3,
    orphan_count=1,
)


def test_every_operation_has_a_unique_key_and_a_known_scope() -> None:
    keys = [op.key for op in OPS]
    assert len(keys) == len(set(keys))
    assert {op.scope for op in OPS} <= {"window", "timeline", "display"}


def test_shortcuts_are_unique_among_the_bound_ones() -> None:
    bound = [op.shortcut for op in OPS if op.scope in ("window", "timeline") and op.shortcut]
    assert len(bound) == len(set(bound))


def test_editing_keys_are_never_bound_window_wide() -> None:
    # A window-wide Delete or Ctrl+Backspace would steal the code editor's own keys.
    for key in ("delete", "reset", "jump", "discard_orphans", "discard_all"):
        assert OPS_BY_KEY[key].scope == "timeline", key


def test_everything_is_available_when_the_block_and_edits_allow_it() -> None:
    states = op_states(EDITING)
    assert all(state.enabled for state in states.values()), states


@pytest.mark.parametrize(
    ("facts", "key", "reason"),
    [
        (TimelineFacts(), "delete", "open a scene first"),
        (TimelineFacts(running=True), "delete", "select a block first"),
        (TimelineFacts(running=True, block=0), "delete", "the block's call is not in the source"),
        (TimelineFacts(running=True, block=0), "reset", "this block has no timeline edits"),
        (TimelineFacts(running=True, block=0), "jump", "this block has no source line"),
        (TimelineFacts(), "hold", "open a scene first"),
        (TimelineFacts(scene_open=True), "delete", "start the preview first"),
        (TimelineFacts(scene_open=True), "hold", "start the preview first"),
        (TimelineFacts(running=True), "hold", "the trace is empty"),
        (TimelineFacts(), "apply", "no timeline edits"),
        (TimelineFacts(), "orphans", "no orphaned edits"),
        (TimelineFacts(), "undo", "nothing to undo"),
        (TimelineFacts(), "redo", "nothing to redo"),
    ],
)
def test_an_unavailable_operation_says_why(facts: TimelineFacts, key: str, reason: str) -> None:
    state = op_states(facts)[key]
    assert state == OpState(False, reason)


def test_an_open_scene_disables_delete_with_its_reason_rather_than_the_block_reason() -> None:
    state = op_states(TimelineFacts(block=None))["delete"]
    assert state == OpState(False, "open a scene first")


def test_every_operation_gets_a_state() -> None:
    assert set(op_states(TimelineFacts())) == set(OPS_BY_KEY)


def test_the_tooltip_names_the_operation_its_shortcut_and_the_reason() -> None:
    hold = OPS_BY_KEY["hold"]
    assert tooltip_for(hold) == "Insert hold at playhead  (Ctrl+Shift+H)"
    assert tooltip_for(hold, OpState(False, "open a scene first")) == (
        "Insert hold at playhead  (Ctrl+Shift+H) — open a scene first"
    )
    assert tooltip_for(hold, OpState(True)) == tooltip_for(hold)
