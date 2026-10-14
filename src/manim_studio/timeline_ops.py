"""Timeline operations: one table for their names, shortcuts and when they are available.

Pure (no Qt).  The editor turns the table into actions shared by the toolbar, the menus,
the shortcuts and the command palette, and feeds :func:`op_states` the facts that decide
what is available right now.
"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = [
    "OPS",
    "OPS_BY_KEY",
    "OpState",
    "TimelineFacts",
    "TimelineOp",
    "op_states",
    "tooltip_for",
]


@dataclass(frozen=True, slots=True)
class TimelineOp:
    """One timeline operation.

    ``scope`` says where the shortcut is bound: ``"window"`` (anywhere in the editor),
    ``"timeline"`` (only while the timeline has focus, so editing keys never reach the
    code editor), or ``"display"`` (the timeline handles the key itself; the shortcut is
    shown but not bound, because a second binding would make the key ambiguous).
    """

    key: str
    title: str
    glyph: str
    shortcut: str = ""
    scope: str = "window"
    label: str = ""  # short text beside the icon, when the toolbar has room
    group: str = "edit"  # toolbar group: edit or apply


OPS: tuple[TimelineOp, ...] = (
    TimelineOp("undo", "Undo", "undo", "Ctrl+Z", "display", "Undo", "edit"),
    TimelineOp("redo", "Redo", "redo", "Ctrl+Y", "display", "", "edit"),
    TimelineOp(
        "delete", "Delete selected block's call", "delete", "Del", "timeline", "Delete", "edit"
    ),
    TimelineOp(
        "reset", "Reset selected block's timing", "reset", "Ctrl+Backspace", "timeline", "", "edit"
    ),
    TimelineOp(
        "jump",
        "Jump to selected block's source",
        "jump_to_code",
        "Ctrl+Return",
        "timeline",
        "",
        "edit",
    ),
    TimelineOp("hold", "Insert hold at playhead", "hold", "Ctrl+Shift+H", "window", "Hold", "edit"),
    TimelineOp(
        "apply",
        "Apply timeline edits to source",
        "apply",
        "Ctrl+Shift+A",
        "window",
        "Apply",
        "apply",
    ),
    TimelineOp(
        "diff", "Show timeline edits as diff…", "diff", "Ctrl+Shift+D", "window", "", "apply"
    ),
    TimelineOp(
        "orphans", "Review orphaned edits…", "orphans", "Ctrl+Shift+O", "window", "", "apply"
    ),
    TimelineOp(
        "discard_orphans",
        "Discard orphaned edits",
        "discard_orphans",
        "Ctrl+Shift+Del",
        "timeline",
        "",
        "apply",
    ),
    TimelineOp(
        "discard_all",
        "Discard all timeline edits",
        "discard_all",
        "Ctrl+Shift+Backspace",
        "timeline",
        "",
        "apply",
    ),
)

OPS_BY_KEY: dict[str, TimelineOp] = {op.key: op for op in OPS}


@dataclass(frozen=True, slots=True)
class TimelineFacts:
    """What the editor knows right now, from which availability follows."""

    running: bool = False  # a scene is open and its preview host runs
    scene_open: bool = False  # a scene file is open, whether or not its preview runs
    has_log: bool = False  # the trace has blocks
    block: int | None = None  # the block the operations act on (clicked, caret or playhead)
    block_editable: bool = False  # the block's call exists in the source (so it can be deleted)
    block_has_line: bool = False  # the block's call has a source line (so jump works)
    block_edited: bool = False  # the block carries timeline edits
    can_undo: bool = False
    can_redo: bool = False
    edit_count: int = 0
    orphan_count: int = 0


@dataclass(frozen=True, slots=True)
class OpState:
    enabled: bool
    reason: str = ""  # why it is unavailable; empty when enabled


def op_states(facts: TimelineFacts) -> dict[str, OpState]:
    """Whether each operation is available, and the reason when it is not."""
    no_block = "select a block first"
    no_scene = "open a scene first"
    # With a scene open, the preview is what is missing; saying "open a scene" would mislead.
    not_running = "start the preview first" if facts.scene_open else no_scene
    no_edits = "no timeline edits"
    no_orphans = "no orphaned edits"
    states: dict[str, OpState] = {}

    def gate(key: str, ok: bool, reason: str) -> None:
        states[key] = OpState(ok, "" if ok else reason)

    gate("undo", facts.can_undo, "nothing to undo")
    gate("redo", facts.can_redo, "nothing to redo")
    if facts.block is None:
        for key in ("delete", "reset", "jump"):
            states[key] = OpState(False, no_block)
    else:
        gate(
            "delete",
            facts.running and facts.block_editable,
            "the block's call is not in the source",
        )
        gate("reset", facts.block_edited, "this block has no timeline edits")
        gate("jump", facts.block_has_line, "this block has no source line")
    if not facts.running:
        states["delete"] = OpState(False, not_running)
    gate(
        "hold",
        facts.running and facts.has_log,
        not_running if not facts.running else "the trace is empty",
    )
    gate("apply", facts.edit_count > 0, no_edits)
    gate("diff", facts.edit_count > 0, no_edits)
    gate("orphans", facts.orphan_count > 0, no_orphans)
    gate("discard_orphans", facts.orphan_count > 0, no_orphans)
    gate("discard_all", facts.edit_count > 0, no_edits)
    return states


def tooltip_for(op: TimelineOp, state: OpState | None = None) -> str:
    """The tooltip for one operation: its name, its shortcut, and why it is unavailable."""
    text = op.title
    if op.shortcut:
        text = f"{text}  ({op.shortcut})"
    if state is not None and not state.enabled and state.reason:
        text = f"{text} — {state.reason}"
    return text
