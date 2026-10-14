"""Pure loading-stage rules (no Qt)."""

from __future__ import annotations

import pytest

from manim_studio import loading


@pytest.mark.parametrize(
    ("current", "event", "expected"),
    [
        (None, loading.BEGIN_START, loading.STARTING),
        (None, loading.BEGIN_RELOAD, loading.RELOADING),
        (loading.RENDERING, loading.BEGIN_RELOAD, loading.RELOADING),
        (loading.STARTING, loading.HELLO, loading.IMPORTING),
        (loading.RELOADING, loading.HELLO, loading.IMPORTING),
        (None, loading.HELLO, None),  # a hello outside a load changes nothing
        (loading.IMPORTING, loading.TRACED, loading.RENDERING),
        (loading.RELOADING, loading.TRACED, loading.RENDERING),
        (None, loading.TRACED, None),  # a patch-only trace shows no spinner
        (loading.RENDERING, loading.FRAME, None),
        (loading.STARTING, loading.FRAME, None),
        (loading.IMPORTING, loading.SETTLED, loading.IMPORTING),  # idle too early
        (loading.RENDERING, loading.SETTLED, None),  # idle after trace: no frame coming
        (loading.RELOADING, loading.FAILED, None),
        (None, loading.SETTLED, None),
    ],
)
def test_next_stage(current: str | None, event: str, expected: str | None) -> None:
    assert loading.next_stage(current, event) == expected


def test_every_stage_has_a_caption() -> None:
    for stage in (loading.STARTING, loading.IMPORTING, loading.RELOADING, loading.RENDERING):
        assert loading.caption(stage).endswith("…")


def test_remaining_visible_ms_holds_until_the_minimum_has_passed() -> None:
    assert loading.remaining_visible_ms(10.0, 10.1, 300) == 200
    assert loading.remaining_visible_ms(10.0, 10.3, 300) == 0
    assert loading.remaining_visible_ms(10.0, 60.0, 300) == 0
    assert loading.remaining_visible_ms(10.0, 10.0, 0) == 0
