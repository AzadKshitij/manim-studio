"""The one status the preview shows: what it is doing, in a word, and what to do next.

Pure (no Qt).  The window feeds facts in (host state, loading stage, error, unsaved
edits, whether a scene is open); :func:`summarize` decides what the status chip says.
Precedence runs from the most urgent fact to the least: no scene, a crashed host, a
load in progress, an error, a host that is not running, unsaved edits, then activity.
"""

from __future__ import annotations

from dataclasses import dataclass

from manim_studio import loading

__all__ = ["CRASHED", "OFF", "RUNNING", "PreviewStatus", "StatusSummary", "summarize"]

#: Host lifecycle, as the window tracks it.
OFF = "off"
RUNNING = "running"
CRASHED = "crashed"

_LOADING_WORDS = {
    loading.STARTING: "Starting",
    loading.IMPORTING: "Importing",
    loading.RELOADING: "Reloading",
    loading.RENDERING: "Rendering",
}


@dataclass(frozen=True)
class PreviewStatus:
    scene_open: bool = False
    host: str = OFF
    activity: str = "idle"  # idle | playing | seeking
    loading: str | None = None  # a loading.* stage while a load is in progress
    error: str | None = None  # the last error that no successful trace has cleared
    dirty: bool = False  # the buffer has edits the preview does not show yet


@dataclass(frozen=True)
class StatusSummary:
    text: str
    tone: str  # ok | busy | warn | err | accent | muted
    detail: str


def summarize(status: PreviewStatus) -> StatusSummary:
    """The chip's word, colour tone and tooltip for ``status``."""
    if not status.scene_open:
        return StatusSummary("No scene", "muted", "Open a scene to preview it.")
    if status.host == CRASHED:
        return StatusSummary(
            "Error", "err", "The preview host stopped. Press Reload to restart it."
        )
    if status.loading is not None:
        return StatusSummary(
            _LOADING_WORDS[status.loading], "busy", loading.caption(status.loading)
        )
    if status.error is not None:
        return StatusSummary("Error", "err", status.error)
    if status.host != RUNNING:
        return StatusSummary("Not running", "muted", "Press Reload to start the preview.")
    if status.dirty:
        return StatusSummary(
            "Stale", "warn", "The preview shows the last saved code. Save (Ctrl+S) to update it."
        )
    if status.activity == "playing":
        return StatusSummary("Playing", "accent", "The scene is playing.")
    if status.activity == "seeking":
        return StatusSummary("Seeking", "accent", "Showing the frame you asked for.")
    return StatusSummary("Ready", "ok", "The preview matches the saved code.")
