"""What happens when the scene file changes on disk (pure, stdlib only).

The policy is the user's choice (File > External changes).  :func:`decide` turns that choice
and whether the editor holds unsaved edits into one :class:`Decision`.  Nothing here reads
or writes the file or the editor; the window carries the decision out.
"""

from __future__ import annotations

from enum import Enum

__all__ = [
    "ALWAYS",
    "ASK",
    "AUTO",
    "DEFAULT_POLICY",
    "IGNORE",
    "POLICIES",
    "POLICY_LABELS",
    "Decision",
    "decide",
    "parse_policy",
]

#: Show the chooser for every change.
ASK = "ask"
#: Take a change silently when the editor has no unsaved edits; ask when it has some (default).
AUTO = "auto"
#: Take every change, even over unsaved edits; the edits stay on the undo stack.
ALWAYS = "always"
#: Never react to changes on disk.
IGNORE = "ignore"

POLICIES: tuple[str, ...] = (ASK, AUTO, ALWAYS, IGNORE)
DEFAULT_POLICY = AUTO

POLICY_LABELS: dict[str, str] = {
    ASK: "Ask",
    AUTO: "Auto-accept unless I have unsaved edits",
    ALWAYS: "Always auto-accept (my edits stay on the undo stack)",
    IGNORE: "Ignore changes on disk",
}


class Decision(Enum):
    """What the editor does about a change on disk."""

    RELOAD = "reload"  # take the file from disk (the editor has no unsaved edits)
    RELOAD_OVER_EDITS = "reload_over_edits"  # take it, keeping the old text on the undo stack
    ASK = "ask"  # show the chooser above the code
    LEAVE = "leave"  # do nothing; the status line says so


def parse_policy(value: object) -> str:
    """The policy named by ``value``, or the default when it is not a known policy."""
    return value if isinstance(value, str) and value in POLICIES else DEFAULT_POLICY


def decide(policy: str, buffer_dirty: bool) -> Decision:
    """The decision for a change on disk, given the policy and whether the editor is dirty."""
    policy = parse_policy(policy)
    if policy == IGNORE:
        return Decision.LEAVE
    if policy == ASK:
        return Decision.ASK
    if not buffer_dirty:
        return Decision.RELOAD
    if policy == ALWAYS:
        return Decision.RELOAD_OVER_EDITS
    return Decision.ASK  # AUTO with unsaved edits
