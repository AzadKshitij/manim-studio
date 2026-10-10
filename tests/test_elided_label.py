"""ElidedLabel: long text must never become a layout minimum (it pushed the preview off screen)."""

from __future__ import annotations

from PySide6.QtWidgets import QHBoxLayout, QWidget

from manim_studio.editor.elided_label import ElidedLabel


def _host(qtbot, label: ElidedLabel, width: int = 300) -> QWidget:  # type: ignore[no-untyped-def]
    host = QWidget()
    layout = QHBoxLayout(host)
    layout.addWidget(label, 1)
    qtbot.addWidget(host)
    host.resize(width, 40)
    host.show()
    qtbot.wait(20)
    return host


def test_text_returns_the_full_string_even_when_elided(qtbot) -> None:  # type: ignore[no-untyped-def]
    label = ElidedLabel("a long message " * 200)
    host = _host(qtbot, label)
    assert label.text() == "a long message " * 200
    assert host.minimumSizeHint().width() < 400


def test_the_displayed_text_is_shortened_and_the_tooltip_has_everything(qtbot) -> None:  # type: ignore[no-untyped-def]
    long_text = "word " * 400
    label = ElidedLabel(long_text)
    host = _host(qtbot, label)
    assert host is not None  # keep the parent alive
    assert label.toolTip().endswith(long_text)
    assert label.sizeHint().width() < 400  # not the width of 2000 characters


def test_short_text_is_shown_whole_without_a_tooltip(qtbot) -> None:  # type: ignore[no-untyped-def]
    label = ElidedLabel("idle")
    host = _host(qtbot, label)
    assert host is not None  # keep the parent alive
    assert label.toolTip() == ""


def test_a_base_tooltip_is_kept_and_the_full_text_appended(qtbot) -> None:  # type: ignore[no-untyped-def]
    label = ElidedLabel()
    label.setToolTip("Animations of the block")
    label.setText("word " * 400)
    host = _host(qtbot, label)
    assert host is not None  # keep the parent alive
    assert label.toolTip().startswith("Animations of the block")
    label.setText("short")
    assert label.toolTip() == "Animations of the block"


def test_rich_text_is_not_interpreted(qtbot) -> None:  # type: ignore[no-untyped-def]
    label = ElidedLabel("<b>not bold</b> & <i>not italic</i>")
    host = _host(qtbot, label, width=600)
    assert host is not None
    assert label.textFormat().name == "PlainText"
