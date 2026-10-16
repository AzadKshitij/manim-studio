"""Offscreen tests for the gear markers beside mobject lines (ADR 0012)."""

from __future__ import annotations

import importlib.util
import json
from collections.abc import Iterator

import pytest
from pytestqt.qtbot import QtBot

if importlib.util.find_spec("manimlib") is None:
    pytest.skip("manimlib is not installed", allow_module_level=True)

from manim_studio.editor.code_editor import CodeEditor
from manim_studio.editor.inspector_gear import InspectorGears
from manim_studio.mobject_schema import MobjectSchema, parse_schema
from manim_studio.mobject_sites import find_mobject_targets
from manim_studio.schema_probe import probe

pytestmark = pytest.mark.usefixtures("qtbot")

SOURCE = """from manimlib import *


class Demo(Scene):
    def construct(self):
        circle = Circle(radius=1.5, color=BLUE)
        square = Square(fill_color="#FF6B6B")
        helper = compute()
        circle.shift(UP)
"""


@pytest.fixture(scope="module")
def schema() -> MobjectSchema:
    return parse_schema(json.loads(json.dumps(probe())))


@pytest.fixture
def gears(qtbot: QtBot, schema: MobjectSchema) -> Iterator[InspectorGears]:
    editor = CodeEditor()
    qtbot.addWidget(editor)
    editor.set_text(SOURCE)
    editor.emptyUndoBuffer()
    controller = InspectorGears(editor)
    controller.set_schema(schema)
    yield controller


def test_one_gear_per_line_of_a_known_mobject(gears: InspectorGears) -> None:
    # circle is bound on line 6 and set on line 9; square on line 7; helper is not a mobject.
    assert sorted(gear.toolTip().split(" · ")[0] for gear in gears.buttons) == [
        "circle",
        "circle",
        "square",
    ]


def test_no_gears_without_a_schema(qtbot: QtBot) -> None:
    editor = CodeEditor()
    qtbot.addWidget(editor)
    editor.set_text(SOURCE)
    controller = InspectorGears(editor)
    assert controller.buttons == ()


def test_broken_source_shows_no_gears(gears: InspectorGears) -> None:
    gears.editor.set_text("def broken(:\n")
    gears.rescan()
    assert gears.buttons == ()


def test_clicking_a_gear_emits_the_fresh_target(gears: InspectorGears) -> None:
    received: list[object] = []
    gears.targetRequested.connect(received.append)
    gears.buttons[0].click()
    assert len(received) == 1
    target = received[0]
    assert target.var == "circle"
    assert target.cls == "Circle"


def test_a_stale_click_rescans_instead_of_opening_a_gone_target(gears: InspectorGears) -> None:
    received: list[object] = []
    gears.targetRequested.connect(received.append)
    old = next(iter(find_mobject_targets(SOURCE)))
    gears.editor.set_text("class Demo(Scene):\n    pass\n")
    gears._request(old)
    assert received == []
    assert gears.buttons == ()


def test_gears_follow_an_edit_after_the_rescan_delay(gears: InspectorGears) -> None:
    gears.editor.set_text(SOURCE.replace('square = Square(fill_color="#FF6B6B")', "pass"))
    gears.rescan()
    assert sorted(g.toolTip().split(" · ")[0] for g in gears.buttons) == ["circle", "circle"]
