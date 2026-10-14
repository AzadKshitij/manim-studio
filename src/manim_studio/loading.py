"""Preview loading stages: which host event moves the spinner, and what it says.

Pure (no Qt): the editor feeds host events in and shows whatever stage comes out.
The host only reports lifecycle ``status`` plus the ``log``/``frame`` events of a
load, so the stages are the ones those events can prove: the host process starting,
the scene importing (the host's hello), the trace finishing and the first frame.
"""

from __future__ import annotations

__all__ = [
    "BEGIN_RELOAD",
    "BEGIN_START",
    "FAILED",
    "FRAME",
    "HELLO",
    "IMPORTING",
    "MIN_VISIBLE_MS",
    "RELOADING",
    "RENDERING",
    "SETTLED",
    "SHOW_DELAY_MS",
    "STARTING",
    "TRACED",
    "caption",
    "next_stage",
    "remaining_visible_ms",
]

# Stages (what the spinner is doing).
STARTING = "starting"
IMPORTING = "importing"
RELOADING = "reloading"
RENDERING = "rendering"

# Events (what just happened).
BEGIN_START = "begin_start"  # the host process was launched
BEGIN_RELOAD = "begin_reload"  # a load or reload command was sent
HELLO = "hello"  # the host connected a channel
TRACED = "traced"  # the host finished a trace (``log``)
FRAME = "frame"  # the host streamed a frame
SETTLED = "settled"  # the host reported idle, playing or seeking
FAILED = "failed"  # an error, crash, stop or close

#: A spinner appears only if loading is still going on after this long, so fast
#: reloads never flash.
SHOW_DELAY_MS = 150
#: Once shown, the spinner stays at least this long so it can be read.
MIN_VISIBLE_MS = 300

_CAPTIONS: dict[str, str] = {
    STARTING: "Starting preview…",
    IMPORTING: "Importing scene…",
    RELOADING: "Reloading scene…",
    RENDERING: "Rendering first frame…",
}


def next_stage(current: str | None, event: str) -> str | None:
    """The loading stage after ``event``; ``None`` means nothing is loading."""
    if event == BEGIN_START:
        return STARTING
    if event == BEGIN_RELOAD:
        return RELOADING
    if event == HELLO:
        return IMPORTING if current in (STARTING, RELOADING) else current
    if event == TRACED:
        return RENDERING if current in (STARTING, RELOADING, IMPORTING) else current
    if event == SETTLED:
        # Idle right after a trace means no frame is coming (external window mode).
        # Idle at any other time is not a load finishing, so it changes nothing.
        return None if current == RENDERING else current
    if event in (FRAME, FAILED):
        return None
    return current


def caption(stage: str) -> str:
    """The text shown under the spinner for ``stage``."""
    return _CAPTIONS[stage]


def remaining_visible_ms(shown_at: float, now: float, min_visible_ms: int = MIN_VISIBLE_MS) -> int:
    """How much longer a shown spinner must stay up; ``0`` once it has been long enough."""
    elapsed_ms = (now - shown_at) * 1000.0
    return max(0, round(min_visible_ms - elapsed_ms))
