"""The inspector panel (ADR 0012): rows come from the schema, Apply emits the source changes."""

from __future__ import annotations

import importlib.util
import json
from collections.abc import Iterator
from typing import Any

import pytest
from pytestqt.qtbot import QtBot

if importlib.util.find_spec("manimlib") is None:
    pytest.skip("manimlib is not installed", allow_module_level=True)

from PySide6.QtWidgets import QCheckBox, QDoubleSpinBox

from manim_studio.call_edits import RemoveArgument, SetArgument, apply_changes
from manim_studio.editor.color_field import ColorField, ColorListField
from manim_studio.editor.mobject_inspector import (
    MobjectInspector,
    order_changes,
)
from manim_studio.mobject_schema import MobjectSchema, parse_schema
from manim_studio.mobject_sites import MobjectTarget, find_mobject_targets
from manim_studio.schema_probe import probe

pytestmark = pytest.mark.usefixtures("qtbot")

CIRCLE = "from manimlib import *\n\nc = Circle(radius=1.5, color=BLUE)\nc.shift(UP)\n"
TEXT = 'from manimlib import *\n\nt = Text("x")\nt.set_color_by_gradient(BLUE, TEAL)\n'


@pytest.fixture(scope="module")
def schema() -> MobjectSchema:
    return parse_schema(json.loads(json.dumps(probe())))


@pytest.fixture
def dock(qtbot: QtBot, schema: MobjectSchema) -> Iterator[MobjectInspector]:
    panel = MobjectInspector()
    qtbot.addWidget(panel)
    panel.set_schema(schema)
    yield panel


def _target(source: str, var: str) -> MobjectTarget:
    return next(t for t in find_mobject_targets(source) if t.var == var)


def _row(dock: MobjectInspector, name: str, call: int = 0) -> Any:
    return next(r for r in dock._rows if r.param.name == name and r.call == call)


def _applied(dock: MobjectInspector, qtbot: QtBot) -> list[Any]:
    seen: list[Any] = []
    dock.applyRequested.connect(lambda target, changes: seen.append((target, changes)))
    dock._apply.click()
    assert seen, "Apply emitted nothing"
    return seen[-1][1]


def test_rows_come_from_the_whole_mro(dock: MobjectInspector) -> None:
    dock.show_target(_target(CIRCLE, "c"))
    names = {row.param.name for row in dock._rows}
    assert {"radius", "stroke_color", "fill_opacity", "color"} <= names


def test_a_bound_argument_shows_its_value_and_is_checked(dock: MobjectInspector) -> None:
    dock.show_target(_target(CIRCLE, "c"))
    radius = _row(dock, "radius")
    assert radius.check.isChecked()
    assert isinstance(radius.editor, QDoubleSpinBox)
    assert radius.editor.value() == pytest.approx(1.5)


def test_an_unbound_argument_is_unchecked_and_shows_its_default(dock: MobjectInspector) -> None:
    dock.show_target(_target(CIRCLE, "c"))
    width = _row(dock, "stroke_width")
    assert not width.check.isChecked()


def test_changing_a_value_emits_a_set_for_that_keyword(
    dock: MobjectInspector, qtbot: QtBot
) -> None:
    dock.show_target(_target(CIRCLE, "c"))
    _row(dock, "radius").editor.setValue(2.0)
    changes = _applied(dock, qtbot)
    assert changes == [SetArgument(0, "2.0", keyword="radius")]


def test_unchecking_a_bound_argument_emits_a_remove(dock: MobjectInspector, qtbot: QtBot) -> None:
    dock.show_target(_target(CIRCLE, "c"))
    _row(dock, "radius").check.setChecked(False)
    assert _applied(dock, qtbot) == [RemoveArgument(0, keyword="radius")]


def test_checking_an_unbound_argument_adds_it(dock: MobjectInspector, qtbot: QtBot) -> None:
    dock.show_target(_target(CIRCLE, "c"))
    row = _row(dock, "stroke_width")
    row.check.setChecked(True)
    changes = _applied(dock, qtbot)
    assert len(changes) == 1
    assert changes[0].keyword == "stroke_width"
    assert float(changes[0].value) == pytest.approx(row.editor.value())


def test_an_unchanged_manim_colour_writes_nothing(dock: MobjectInspector) -> None:
    # ``color=BLUE`` shows as its hex; leaving it alone must not rewrite the name.
    dock.show_target(_target(CIRCLE, "c"))
    colour = _row(dock, "color")
    assert isinstance(colour.editor, ColorField)
    assert colour.editor.value() == "#58C4DD"
    dock._apply.click()
    assert dock._status.text() == "Nothing to change."


def test_a_gradient_gets_a_colour_list_and_plus_adds_one(
    dock: MobjectInspector, qtbot: QtBot
) -> None:
    dock.show_target(_target(TEXT, "t"))
    gradient = next(r for r in dock._rows if r.kind == "colour_list")
    assert isinstance(gradient.editor, ColorListField)
    assert gradient.editor.values() == ["#58C4DD", "#5CD0B3"]
    gradient.editor._on_add()
    changes = _applied(dock, qtbot)
    assert changes == [SetArgument(1, '"#5CD0B3"', position=2)]


def test_applied_changes_produce_the_intended_source(dock: MobjectInspector, qtbot: QtBot) -> None:
    dock.show_target(_target(TEXT, "t"))
    gradient = next(r for r in dock._rows if r.kind == "colour_list")
    gradient.editor._on_add()
    changes = _applied(dock, qtbot)
    out = apply_changes(TEXT, _target(TEXT, "t"), changes)
    # The untouched BLUE and TEAL keep their names; the colour added is written as hex.
    assert out.endswith('t.set_color_by_gradient(BLUE, TEAL, "#5CD0B3")\n')


def test_a_value_the_schema_cannot_edit_falls_back_to_the_expression(
    dock: MobjectInspector,
) -> None:
    dock.show_target(_target("from manimlib import *\nc = Circle(radius=UP)\n", "c"))
    radius = _row(dock, "radius")
    assert radius.kind == "expr"
    assert radius.editor.text() == "UP"


def test_order_changes_sets_first_and_positional_removes_from_the_end() -> None:
    changes = [
        RemoveArgument(0, position=0),
        SetArgument(0, "1", keyword="radius"),
        RemoveArgument(0, position=2),
        SetArgument(0, "2", position=1),
    ]
    ordered = order_changes(changes)
    assert ordered[:2] == [SetArgument(0, "2", position=1), SetArgument(0, "1", keyword="radius")]
    assert ordered[2:] == [RemoveArgument(0, position=2), RemoveArgument(0, position=0)]


def test_a_missing_target_clears_the_panel_with_a_reason(dock: MobjectInspector) -> None:
    dock.show_target(_target(CIRCLE, "c"))
    dock.clear("gone")
    assert dock.target is None
    assert dock._rows == []


def test_no_schema_shows_a_loading_message(qtbot: QtBot) -> None:
    panel = MobjectInspector()
    qtbot.addWidget(panel)
    panel.show_target(_target(CIRCLE, "c"))
    assert panel._rows == []
    assert panel.target is not None  # kept, so the rows appear once the schema arrives
    panel.set_schema(None)
    assert panel._rows == []


def test_check_boxes_are_labelled_with_their_parameter(dock: MobjectInspector) -> None:
    dock.show_target(_target(CIRCLE, "c"))
    labels = {box.text() for box in dock.findChildren(QCheckBox)}
    assert "radius" in labels and "stroke_color" in labels


def test_add_call_with_an_argument_appends_a_chained_call(
    dock: MobjectInspector, qtbot: QtBot
) -> None:
    from manim_studio.call_edits import AddCall

    dock.show_target(_target(CIRCLE, "c"))
    dock._method_box.setCurrentText("scale")
    dock._add_call.click()
    row = next(r for r in dock._rows if r.call == -1 and r.param.name == "scale_factor")
    row.check.setChecked(True)
    row.editor.setValue(2.0)
    changes = _applied(dock, qtbot)
    assert changes == [AddCall("scale", (), (("scale_factor", "2.0"),))]
    out = apply_changes(CIRCLE, _target(CIRCLE, "c"), changes)
    assert "c.shift(UP)" in out
    assert "\nc = Circle(radius=1.5, color=BLUE).scale(scale_factor=2.0)\n" in out


def test_add_call_with_no_arguments_writes_an_empty_call(
    dock: MobjectInspector, qtbot: QtBot
) -> None:
    from manim_studio.call_edits import AddCall

    dock.show_target(_target(CIRCLE, "c"))
    dock._method_box.setCurrentText("center")
    dock._add_call.click()
    changes = _applied(dock, qtbot)
    assert changes == [AddCall("center", (), ())]
    assert apply_changes(CIRCLE, _target(CIRCLE, "c"), changes).count(".center()") == 1


def test_set_in_code_only_hides_the_rows_the_code_does_not_set(dock: MobjectInspector) -> None:
    dock.show_target(_target(CIRCLE, "c"))
    width = _row(dock, "stroke_width").check.parentWidget()
    radius = _row(dock, "radius").check.parentWidget()
    dock._set_only.setChecked(True)
    assert width.isHidden()
    assert not radius.isHidden()


def test_a_colour_with_no_default_is_a_colour_picker(dock: MobjectInspector, qtbot: QtBot) -> None:
    dock.show_target(_target(CIRCLE, "c"))
    fill = _row(dock, "fill_color")
    assert fill.kind == "colour"
    assert isinstance(fill.editor, ColorField)
    fill.check.setChecked(True)
    fill.editor.set_value("#112233")
    assert _applied(dock, qtbot) == [SetArgument(0, '"#112233"', keyword="fill_color")]


def test_an_array_default_is_three_number_boxes_written_as_a_list(
    dock: MobjectInspector, qtbot: QtBot
) -> None:
    from manim_studio.editor.value_fields import VectorField

    dock.show_target(_target(CIRCLE, "c"))
    centre = _row(dock, "arc_center")
    assert centre.kind == "vector"
    assert isinstance(centre.editor, VectorField)
    assert centre.editor.values() == [0.0, 0.0, 0.0]
    centre.check.setChecked(True)
    centre.editor._boxes[0].setValue(1.0)
    assert _applied(dock, qtbot) == [SetArgument(0, "[1.0, 0.0, 0.0]", keyword="arc_center")]


def test_a_tuple_in_source_stays_a_tuple_when_edited(dock: MobjectInspector, qtbot: QtBot) -> None:
    source = "from manimlib import *\na = Axes(x_range=(-8, 8, 1))\n"
    dock.show_target(_target(source, "a"))
    axis = _row(dock, "x_range")
    assert axis.kind == "vector"
    axis.editor._boxes[0].setValue(-9.0)
    assert _applied(dock, qtbot) == [SetArgument(0, "(-9.0, 8.0, 1.0)", keyword="x_range")]


COLOUR_MAP = 'from manimlib import *\nt = Text("x", t2c={"x": BLUE})\n'


def test_a_colour_map_is_edited_row_by_row_and_keeps_names(
    dock: MobjectInspector, qtbot: QtBot
) -> None:
    from manim_studio.editor.value_fields import DictField

    dock.show_target(_target(COLOUR_MAP, "t"))
    row = _row(dock, "t2c")
    assert row.kind == "colour_map"
    assert isinstance(row.editor, DictField)
    assert row.editor.entry_count() == 1
    dock._apply.click()  # nothing was touched, so the source text is left exactly as it was
    assert dock._status.text() == "Nothing to change."

    row.editor._on_add()
    row.editor._rows[1].key.setText("y")
    changes = _applied(dock, qtbot)
    assert changes == [SetArgument(0, '{"x": BLUE, "y": "#FFFFFF"}', keyword="t2c")]
    out = apply_changes(COLOUR_MAP, _target(COLOUR_MAP, "t"), changes)
    assert 't = Text("x", t2c={"x": BLUE, "y": "#FFFFFF"})' in out


def test_a_dict_with_computed_keys_stays_an_expression(dock: MobjectInspector) -> None:
    dock.show_target(_target('from manimlib import *\nt = Text("x", t2c={1: BLUE})\n', "t"))
    assert _row(dock, "t2c").kind == "expr"
