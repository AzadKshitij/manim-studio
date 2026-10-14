"""The status chip shows a summary as one word, a colour and a tooltip."""

from __future__ import annotations

from manim_studio.editor.status_chip import StatusChip
from manim_studio.preview_status import StatusSummary


def test_shows_the_word_with_a_dot_and_the_detail_as_tooltip(qtbot) -> None:  # type: ignore[no-untyped-def]
    chip = StatusChip()
    qtbot.addWidget(chip)
    chip.show_summary(StatusSummary("Stale", "warn", "Save to update the preview."))
    assert chip.text() == "● Stale"
    assert chip.toolTip() == "Save to update the preview."


def test_the_colour_follows_the_tone(qtbot) -> None:  # type: ignore[no-untyped-def]
    chip = StatusChip()
    qtbot.addWidget(chip)
    chip.show_summary(StatusSummary("Error", "err", "boom"))
    error_style = chip.styleSheet()
    chip.show_summary(StatusSummary("Ready", "ok", "fine"))
    assert chip.styleSheet() != error_style


def test_an_unknown_tone_falls_back_to_muted(qtbot) -> None:  # type: ignore[no-untyped-def]
    chip = StatusChip()
    qtbot.addWidget(chip)
    chip.show_summary(StatusSummary("Odd", "no-such-tone", ""))
    assert chip.text() == "● Odd"
    assert "color:" in chip.styleSheet()
