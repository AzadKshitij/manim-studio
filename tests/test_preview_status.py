"""The preview status rules: what the chip says, and which fact wins (pure, no Qt)."""

from __future__ import annotations

import pytest

from manim_studio import loading
from manim_studio.preview_status import CRASHED, OFF, RUNNING, PreviewStatus, summarize

RUNNING_SCENE = PreviewStatus(scene_open=True, host=RUNNING)


def test_no_scene_is_muted_even_if_the_host_crashed() -> None:
    summary = summarize(PreviewStatus(scene_open=False, host=CRASHED))
    assert summary.text == "No scene"
    assert summary.tone == "muted"


def test_a_crashed_host_is_an_error_that_says_how_to_recover() -> None:
    summary = summarize(PreviewStatus(scene_open=True, host=CRASHED, loading=loading.RENDERING))
    assert summary.text == "Error"
    assert summary.tone == "err"
    assert "Reload" in summary.detail


@pytest.mark.parametrize(
    ("stage", "word"),
    [
        (loading.STARTING, "Starting"),
        (loading.IMPORTING, "Importing"),
        (loading.RELOADING, "Reloading"),
        (loading.RENDERING, "Rendering"),
    ],
)
def test_each_load_stage_has_a_word_and_its_caption(stage: str, word: str) -> None:
    summary = summarize(PreviewStatus(scene_open=True, host=RUNNING, loading=stage))
    assert summary.text == word
    assert summary.tone == "busy"
    assert summary.detail == loading.caption(stage)


def test_loading_wins_over_a_stale_error_and_unsaved_edits() -> None:
    summary = summarize(
        PreviewStatus(
            scene_open=True,
            host=RUNNING,
            loading=loading.RELOADING,
            error="boom",
            dirty=True,
        )
    )
    assert summary.text == "Reloading"


def test_an_error_is_named_with_its_message() -> None:
    summary = summarize(PreviewStatus(scene_open=True, host=RUNNING, error="NameError: x"))
    assert summary.text == "Error"
    assert summary.tone == "err"
    assert summary.detail == "NameError: x"


def test_a_host_that_is_not_running_says_so() -> None:
    summary = summarize(PreviewStatus(scene_open=True, host=OFF))
    assert summary.text == "Not running"
    assert summary.tone == "muted"


def test_unsaved_edits_are_stale_until_saved() -> None:
    summary = summarize(PreviewStatus(scene_open=True, host=RUNNING, dirty=True))
    assert summary.text == "Stale"
    assert summary.tone == "warn"
    assert "Save" in summary.detail


def test_activity_shows_while_the_preview_is_playing_or_seeking() -> None:
    assert summarize(PreviewStatus(True, RUNNING, activity="playing")).text == "Playing"
    assert summarize(PreviewStatus(True, RUNNING, activity="seeking")).text == "Seeking"


def test_a_running_preview_that_matches_the_code_is_ready() -> None:
    summary = summarize(RUNNING_SCENE)
    assert summary.text == "Ready"
    assert summary.tone == "ok"
