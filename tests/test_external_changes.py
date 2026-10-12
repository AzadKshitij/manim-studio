"""The external-change policy: what happens to a change on disk, for each policy and buffer."""

from __future__ import annotations

import pytest

from manim_studio.external_changes import (
    ALWAYS,
    ASK,
    AUTO,
    DEFAULT_POLICY,
    IGNORE,
    POLICIES,
    POLICY_LABELS,
    Decision,
    decide,
    parse_policy,
)


@pytest.mark.parametrize(
    ("policy", "dirty", "expected"),
    [
        (ASK, False, Decision.ASK),
        (ASK, True, Decision.ASK),
        (AUTO, False, Decision.RELOAD),
        (AUTO, True, Decision.ASK),
        (ALWAYS, False, Decision.RELOAD),
        (ALWAYS, True, Decision.RELOAD_OVER_EDITS),
        (IGNORE, False, Decision.LEAVE),
        (IGNORE, True, Decision.LEAVE),
    ],
)
def test_each_policy_decides_as_documented(policy: str, dirty: bool, expected: Decision) -> None:
    assert decide(policy, dirty) is expected


def test_an_unknown_policy_falls_back_to_the_default() -> None:
    assert parse_policy("bogus") == DEFAULT_POLICY == AUTO
    assert parse_policy(None) == AUTO
    assert decide("bogus", True) is Decision.ASK


def test_every_policy_has_a_label() -> None:
    assert set(POLICY_LABELS) == set(POLICIES)
    assert all(POLICY_LABELS.values())
