"""The inspector panel: every property of the selected mobject, edited as source (ADR 0012).

A dock panel whose rows come from the discovered schema: the constructor's parameters along the
MRO, the parameters of each chainable method the target already calls, and any method the user
adds with "Add call".  Nothing is written per class, so a new manimlib class or parameter shows up
without touching this file.  Each row has a check box (whether the source sets the argument) and a
widget holding its value.  Apply turns the rows into :mod:`manim_studio.call_edits` changes and
emits them; the window writes them into the buffer.  The panel never touches the file itself.
"""

from __future__ import annotations

import contextlib
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QKeySequence
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDockWidget,
    QDoubleSpinBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from manim_studio.call_edits import (
    AddCall,
    Change,
    EditError,
    RemoveArgument,
    SetArgument,
    format_colour,
    format_value,
)
from manim_studio.editor.color_field import ColorField, ColorListField
from manim_studio.editor.elided_label import ElidedLabel
from manim_studio.mobject_schema import (
    MethodSchema,
    MobjectSchema,
    ParamSchema,
    fields_for,
    lookup,
    methods_for,
    resolve_colour,
    ui_kind,
)
from manim_studio.mobject_sites import ArgRef, CallRef, KeywordRef, MobjectTarget
from manim_studio.theme import Theme, current_theme

__all__ = ["MobjectInspector", "order_changes"]

_LIMIT = 1_000_000.0
#: ``call`` of the rows of a call the user is adding; it is not in the source yet.
_PENDING = -1


def _check_style(theme: Theme) -> str:
    """Check boxes drawn with a visible border, filled with the accent when on.

    The app palette leaves unchecked boxes too faint to see on the panel, and a checked box is
    only a small tick.  Scoped to the inspector, so the rest of the app keeps its look.
    """
    colours = theme.colors
    return (
        "QCheckBox::indicator { width: 14px; height: 14px; border-radius: 3px; "
        f"border: 1px solid {colours.border_strong}; background: {colours.editor}; }}"
        "QCheckBox::indicator:checked { "
        f"background: {colours.accent}; border: 1px solid {colours.accent}; }}"
    )


def order_changes(changes: Iterable[Change]) -> list[Change]:
    """Sets before removes, so the indices a change names are the ones in the source it was made
    against.  Positional removes run from the last index down, since removing one shifts those
    after it.  Other changes (chained calls to add) come last."""
    pending = list(changes)
    sets = [c for c in pending if isinstance(c, SetArgument)]
    removes = [c for c in pending if isinstance(c, RemoveArgument)]
    others = [c for c in pending if not isinstance(c, SetArgument | RemoveArgument)]
    sets.sort(key=lambda c: (c.call, c.position is None, c.position or 0))
    removes.sort(key=lambda c: (c.call, c.position is not None, -(c.position or 0)))
    return [*sets, *removes, *others]


def _is_number(value: Any) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool)


def _matches(kind: str, value: Any) -> bool:
    if kind == "bool":
        return isinstance(value, bool)
    if kind == "int":
        return isinstance(value, int) and not isinstance(value, bool)
    if kind in ("float", "opacity"):
        return _is_number(value)
    if kind in ("text", "font", "colour"):
        return isinstance(value, str)
    return False


def _bound(
    call: CallRef, name: str, positional: list[str]
) -> tuple[ArgRef | KeywordRef | None, int | None]:
    """The argument of ``call`` that binds ``name``: a keyword, or a positional slot."""
    for keyword in call.keywords:
        if keyword.name == name:
            return keyword, None
    if name in positional:
        index = positional.index(name)
        if index < len(call.args):
            return call.args[index], index
    return None, None


def _effective_kind(
    kind: str,
    bound: ArgRef | KeywordRef | None,
    param: ParamSchema,
    colours: Mapping[str, str],
) -> str:
    """The widget a row really gets: the schema's kind when the source value fits it, else an
    expression field.  An expression field always round-trips the source text, so nothing the
    source says is ever lost."""
    if bound is None:
        if not (param.has_default and param.default_literal):
            return "expr"
        value = param.default
    elif not bound.literal:
        return "colour" if kind == "colour" and resolve_colour(bound.text, colours) else "expr"
    else:
        value = bound.value
    if kind == "colour":
        text = bound.text if bound is not None else str(value)
        return "colour" if resolve_colour(text, colours) else "expr"
    return kind if _matches(kind, value) else "expr"


@dataclass(eq=False)
class _Row:
    """One parameter of one call: what the source says, and the widget that edits it."""

    call: int
    param: ParamSchema
    kind: str
    bound: ArgRef | KeywordRef | None
    position: int | None
    check: QCheckBox
    editor: QWidget
    colours: Mapping[str, str]
    list_args: tuple[ArgRef, ...] = ()
    list_start: int = 0

    def changes(self) -> list[Change]:
        if self.kind == "colour_list":
            return self._list_changes()
        if not self.check.isChecked():
            return [self._remove()] if self.bound is not None else []
        if self._unchanged():
            return []
        text = self._source_text()
        if self.bound is None or isinstance(self.bound, KeywordRef):
            return [SetArgument(self.call, text, keyword=self.param.name)]
        return [SetArgument(self.call, text, position=self.position)]

    def _remove(self) -> RemoveArgument:
        if isinstance(self.bound, KeywordRef):
            return RemoveArgument(self.call, keyword=self.bound.name)
        return RemoveArgument(self.call, position=self.position)

    def _unchanged(self) -> bool:
        bound = self.bound
        if bound is None:
            return False
        if self.kind == "expr":
            return self._expr_text() == bound.text.strip()
        if self.kind == "colour":
            return resolve_colour(bound.text, self.colours) == self._hex()
        if not bound.literal:
            return False
        return bound.value == self._python_value()

    def _hex(self) -> str:
        assert isinstance(self.editor, ColorField)
        return self.editor.value()

    def _expr_text(self) -> str:
        assert isinstance(self.editor, QLineEdit)
        return self.editor.text().strip()

    def _python_value(self) -> Any:
        if self.kind == "bool":
            assert isinstance(self.editor, QCheckBox)
            return self.editor.isChecked()
        if self.kind == "int":
            assert isinstance(self.editor, QSpinBox)
            return self.editor.value()
        if self.kind in ("float", "opacity"):
            assert isinstance(self.editor, QDoubleSpinBox)
            return self.editor.value()
        assert isinstance(self.editor, QLineEdit)
        return self.editor.text()

    def source_text(self) -> str:
        """The Python source this row's widget holds, as it would be written."""
        return self._source_text()

    def _source_text(self) -> str:
        if self.kind == "colour":
            original = self.bound.text if self.bound is not None else '"'
            return format_colour(self._hex(), original)
        if self.kind == "expr":
            return self._expr_text()
        return format_value(self._python_value())

    def _list_changes(self) -> list[Change]:
        assert isinstance(self.editor, ColorListField)
        old = self.list_args
        if not self.check.isChecked():
            return [RemoveArgument(self.call, position=a.index) for a in reversed(old)]
        new = self.editor.values()
        changes: list[Change] = []
        for slot, value in enumerate(new[: len(old)]):
            if resolve_colour(old[slot].text, self.colours) != value:
                text = format_colour(value, old[slot].text)
                changes.append(SetArgument(self.call, text, position=old[slot].index))
        first_new = (old[-1].index + 1) if old else self.list_start
        for offset, value in enumerate(new[len(old) :]):
            changes.append(
                SetArgument(self.call, format_colour(value, '"'), position=first_new + offset)
            )
        for arg in reversed(old[len(new) :]):
            changes.append(RemoveArgument(self.call, position=arg.index))
        return changes


@dataclass(frozen=True, slots=True)
class _PendingCall:
    """A chained call the user is adding with "Add call"; its rows say what it passes."""

    method: str
    rows: tuple[_Row, ...]

    def change(self) -> AddCall:
        args: list[str] = []
        keywords: list[tuple[str, str]] = []
        for row in self.rows:
            if not row.check.isChecked():
                continue
            if row.kind == "colour_list":
                assert isinstance(row.editor, ColorListField)
                args.extend(format_colour(value, '"') for value in row.editor.values())
            else:
                keywords.append((row.param.name, row.source_text()))
        return AddCall(self.method, tuple(args), tuple(keywords))


class MobjectInspector(QDockWidget):
    """The dock panel.  ``applyRequested`` carries the target and its ordered changes."""

    applyRequested = Signal(object, object)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__("Inspector", parent)
        self.setObjectName("inspectorDock")
        self.apply_theme()
        self._schema: MobjectSchema | None = None
        self._target: MobjectTarget | None = None
        self._methods: dict[str, MethodSchema] = {}
        self._rows: list[_Row] = []
        self._pending: list[_PendingCall] = []
        self._filterable: list[tuple[str, QWidget, _Row | None]] = []
        self._dirty = False

        body = QWidget(self)
        column = QVBoxLayout(body)
        column.setContentsMargins(8, 8, 8, 8)
        column.setSpacing(6)
        self._title = ElidedLabel("", body)
        self._search = QLineEdit(body)
        self._search.setPlaceholderText("Filter properties")
        self._search.setClearButtonEnabled(True)
        self._search.setAccessibleName("Filter properties")
        self._search.textChanged.connect(self._apply_filter)
        self._set_only = QCheckBox("Set in code only", body)
        self._set_only.setToolTip("Show only the properties the code sets")
        self._set_only.toggled.connect(self._apply_filter)
        filter_row = QHBoxLayout()
        filter_row.addWidget(self._search, 1)
        filter_row.addWidget(self._set_only)
        self._method_box = QComboBox(body)
        self._method_box.setAccessibleName("Chained call to add")
        self._add_call = QPushButton("Add call", body)
        self._add_call.setToolTip("Add a chained call, such as .scale(...), to this mobject")
        call_row = QHBoxLayout()
        call_row.addWidget(self._method_box, 1)
        call_row.addWidget(self._add_call)
        self._scroll = QScrollArea(body)
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._form_host = QWidget()
        self._form = QVBoxLayout(self._form_host)
        self._form.setContentsMargins(0, 0, 0, 0)
        self._form.setSpacing(4)
        self._scroll.setWidget(self._form_host)
        self._status = QLabel("", body)
        self._status.setWordWrap(True)
        buttons = QHBoxLayout()
        self._revert = QPushButton("Revert", body)
        self._revert.setToolTip("Discard the edits and show the code's values again")
        self._apply = QPushButton("Apply", body)
        self._apply.setDefault(True)
        self._apply.setShortcut(QKeySequence("Ctrl+Return"))
        self._apply.setToolTip("Write these edits into the code and save (Ctrl+Enter)")
        buttons.addStretch(1)
        buttons.addWidget(self._revert)
        buttons.addWidget(self._apply)
        column.addWidget(self._title)
        column.addLayout(filter_row)
        column.addLayout(call_row)
        column.addWidget(self._scroll, 1)
        column.addWidget(self._status)
        column.addLayout(buttons)
        self.setWidget(body)
        self._apply.clicked.connect(self._on_apply)
        self._revert.clicked.connect(self._on_revert)
        self._add_call.clicked.connect(self._on_add_call)
        self._show_message("Click a gear beside a mobject in the code to inspect it.")

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @property
    def target(self) -> MobjectTarget | None:
        return self._target

    def apply_theme(self) -> None:
        """Recolour the check boxes from the current theme (the window calls this on change)."""
        self.setStyleSheet(_check_style(current_theme()))

    def has_pending_changes(self) -> bool:
        """True once the user has edited anything since the target was shown."""
        return self._dirty

    def set_schema(self, schema: MobjectSchema | None) -> None:
        self._schema = schema
        if schema is None:
            self._show_message("Loading the manimlib properties…")
        elif self._target is not None:
            self.show_target(self._target)
        else:
            self._show_message("Click a gear beside a mobject in the code to inspect it.")

    def set_status(self, text: str) -> None:
        self._status.setText(text)

    def clear(self, message: str) -> None:
        """Forget the target (it is gone from the source) and say why."""
        self._target = None
        self._show_message(message)

    def show_target(self, target: MobjectTarget) -> None:
        """Build the rows for ``target`` from the source as it reads now."""
        self._clear_form()
        self._target = target
        self._dirty = False
        self._status.setText("")
        schema = self._schema
        if schema is None:
            self._show_message("Loading the manimlib properties…")
            return
        cls = lookup(schema, target.cls)
        if cls is None:
            self._show_message(f"{target.cls} is not a ManimGL mobject, so it has no properties.")
            return
        self._title.setText(f"{target.var} = {target.cls}")
        self._methods = {method.name: method for method in methods_for(schema, cls)}
        self._method_box.clear()
        self._method_box.addItems(sorted(self._methods))
        self._add_call.setEnabled(bool(self._methods))
        for index, call in enumerate(target.calls):
            if call.constructor:
                heading = f"{target.cls}(…)  ·  line {call.line}"
                params = fields_for(schema, cls)
                positional = [p.name for p in cls.params if p.kind == "positional_or_keyword"]
            else:
                heading = f".{call.method}(…)  ·  line {call.line}"
                method = self._methods.get(call.method)
                if method is None:
                    self._add_note(f"{heading}: not a known ManimGL setter, so edit it in the code")
                    continue
                params = method.params
                positional = [p.name for p in method.params if p.kind == "positional_or_keyword"]
            self._add_heading(heading)
            self._add_params(index, call, params, positional, schema)
        self._form.addStretch(1)
        self._apply_filter()

    # ------------------------------------------------------------------
    # Building the form
    # ------------------------------------------------------------------

    def _add_params(
        self,
        call_index: int,
        call: CallRef,
        params: Iterable[ParamSchema],
        positional: list[str],
        schema: MobjectSchema,
    ) -> None:
        for param in params:
            if param.kind == "var_keyword":
                continue
            if param.kind == "var_positional":
                if ui_kind(param) == "colour_list":
                    self._add_list_row(call_index, call, param, len(positional), schema)
                continue
            self._add_row(call_index, call, param, positional, schema)

    def _add_heading(self, text: str) -> None:
        label = QLabel(text, self._form_host)
        label.setStyleSheet("font-weight: 600; margin-top: 6px;")
        self._form.addWidget(label)

    def _add_note(self, text: str) -> None:
        label = QLabel(text, self._form_host)
        label.setWordWrap(True)
        self._form.addWidget(label)

    def _add_row(
        self,
        call_index: int,
        call: CallRef,
        param: ParamSchema,
        positional: list[str],
        schema: MobjectSchema,
    ) -> None:
        bound, position = _bound(call, param.name, positional)
        kind = _effective_kind(ui_kind(param), bound, param, schema.colours)
        container, check = self._row_frame(param)
        check.setChecked(bound is not None)
        editor = self._make_editor(kind, bound, param, schema.colours)
        container.layout().addWidget(editor)  # type: ignore[union-attr]
        row = _Row(
            call=call_index,
            param=param,
            kind=kind,
            bound=bound,
            position=position,
            check=check,
            editor=editor,
            colours=schema.colours,
        )
        self._track(row, container)

    def _add_list_row(
        self,
        call_index: int,
        call: CallRef,
        param: ParamSchema,
        start: int,
        schema: MobjectSchema,
    ) -> None:
        args = tuple(arg for arg in call.args if arg.index >= start)
        container, check = self._row_frame(param)
        check.setChecked(bool(args))
        if any(resolve_colour(arg.text, schema.colours) is None for arg in args):
            note = QLabel("contains expressions: edit it in the code", container)
            container.layout().addWidget(note)  # type: ignore[union-attr]
            check.setEnabled(False)
            self._filterable.append((param.name, container, None))
            return
        values = [resolve_colour(arg.text, schema.colours) or "#FFFFFF" for arg in args]
        editor = ColorListField(values, container)
        container.layout().addWidget(editor)  # type: ignore[union-attr]
        row = _Row(
            call=call_index,
            param=param,
            kind="colour_list",
            bound=None,
            position=None,
            check=check,
            editor=editor,
            colours=schema.colours,
            list_args=args,
            list_start=start,
        )
        self._track(row, container)

    def _row_frame(self, param: ParamSchema) -> tuple[QWidget, QCheckBox]:
        container = QWidget(self._form_host)
        line = QHBoxLayout(container)
        line.setContentsMargins(0, 0, 0, 0)
        line.setSpacing(8)
        check = QCheckBox(param.name, container)
        check.setMinimumWidth(150)
        default = param.default_repr if param.has_default else "no default"
        check.setToolTip(f"{param.name}: checked = the code sets it (default {default})")
        check.setAccessibleName(f"Set {param.name}")
        line.addWidget(check, 0, Qt.AlignmentFlag.AlignTop)
        self._form.addWidget(container)
        return container, check

    def _track(self, row: _Row, container: QWidget) -> None:
        self._rows.append(row)
        self._filterable.append((row.param.name, container, row))
        row.check.toggled.connect(self._on_row_toggled)
        editor = row.editor
        if isinstance(editor, QCheckBox):
            editor.toggled.connect(self._mark_dirty)
        elif isinstance(editor, QSpinBox | QDoubleSpinBox):
            editor.valueChanged.connect(self._mark_dirty)
        elif isinstance(editor, QLineEdit):
            editor.textChanged.connect(self._mark_dirty)
        elif isinstance(editor, ColorField | ColorListField):
            editor.changed.connect(self._mark_dirty)

    def _make_editor(
        self,
        kind: str,
        bound: ArgRef | KeywordRef | None,
        param: ParamSchema,
        colours: Mapping[str, str],
    ) -> QWidget:
        if bound is not None and bound.literal:
            value: Any = bound.value
        elif bound is None and param.has_default and param.default_literal:
            value = param.default
        else:
            value = None
        if kind == "bool":
            box = QCheckBox(self._form_host)
            box.setChecked(value is True)
            return box
        if kind == "int":
            spin = QSpinBox(self._form_host)
            spin.setRange(-int(_LIMIT), int(_LIMIT))
            spin.setValue(int(value) if _is_number(value) else 0)
            return spin
        if kind in ("float", "opacity"):
            fspin = QDoubleSpinBox(self._form_host)
            fspin.setDecimals(4)
            if kind == "opacity":
                fspin.setRange(0.0, 1.0)
                fspin.setSingleStep(0.05)
            else:
                fspin.setRange(-_LIMIT, _LIMIT)
                fspin.setSingleStep(0.1)
            fspin.setValue(float(value) if _is_number(value) else 0.0)
            return fspin
        if kind in ("text", "font"):
            return QLineEdit(value if isinstance(value, str) else "", self._form_host)
        if kind == "colour":
            text = bound.text if bound is not None else str(value)
            return ColorField(resolve_colour(text, colours) or "#FFFFFF", self._form_host)
        line = QLineEdit(self._form_host)
        if bound is not None:
            line.setText(bound.text)
        elif value is not None:
            with contextlib.suppress(EditError):  # a value with no spelling stays blank
                line.setText(format_value(value))
        line.setPlaceholderText(param.default_repr or "")
        return line

    def _clear_form(self) -> None:
        self._rows = []
        self._pending = []
        self._filterable = []
        while self._form.count():
            item = self._form.takeAt(0)
            widget = item.widget() if item is not None else None
            if widget is not None:
                widget.deleteLater()

    def _show_message(self, text: str) -> None:
        self._clear_form()
        self._title.setText("")
        self._dirty = False
        self._methods = {}
        self._method_box.clear()
        self._add_call.setEnabled(False)
        self._add_note(text)
        self._form.addStretch(1)

    def _strip_trailing_stretch(self) -> None:
        """Remove the spacer that keeps the rows at the top, so new rows can follow them."""
        count = self._form.count()
        last = self._form.itemAt(count - 1) if count else None
        if last is not None and last.spacerItem() is not None:
            self._form.takeAt(count - 1)

    def _apply_filter(self, *_args: object) -> None:
        query = self._search.text().strip().lower()
        only_set = self._set_only.isChecked()
        for name, widget, row in self._filterable:
            matches = not query or query in name.lower()
            is_set = row is None or row.check.isChecked()
            widget.setVisible(matches and (is_set or not only_set))

    def _on_add_call(self) -> None:
        """Add the chosen method as a new call, with rows for its parameters."""
        schema, target = self._schema, self._target
        method = self._methods.get(self._method_box.currentText())
        if schema is None or target is None or method is None:
            return
        synthetic = CallRef(
            method=method.name,
            constructor=False,
            start=0,
            end=0,
            open_paren=0,
            args=(),
            keywords=(),
            line=0,
        )
        positional = [p.name for p in method.params if p.kind == "positional_or_keyword"]
        self._strip_trailing_stretch()
        first = len(self._rows)
        self._add_heading(f"+ .{method.name}(…)  ·  new call")
        self._add_params(_PENDING, synthetic, method.params, positional, schema)
        self._form.addStretch(1)
        self._pending.append(_PendingCall(method.name, tuple(self._rows[first:])))
        self._mark_dirty()
        self._apply_filter()

    # ------------------------------------------------------------------
    # Apply and revert
    # ------------------------------------------------------------------

    def _mark_dirty(self, *_args: object) -> None:
        # Connected to signals that pass the new value; the value itself is read at Apply.
        self._dirty = True

    def _on_row_toggled(self, *_args: object) -> None:
        self._mark_dirty()
        self._apply_filter()

    def _on_apply(self) -> None:
        if self._target is None:
            return
        try:
            changes = [
                change for row in self._rows if row.call != _PENDING for change in row.changes()
            ]
            changes += [pending.change() for pending in self._pending]
        except EditError as error:
            self._status.setText(str(error))
            return
        ordered = order_changes(changes)
        if not ordered:
            self._status.setText("Nothing to change.")
            return
        self.applyRequested.emit(self._target, ordered)

    def _on_revert(self) -> None:
        if self._target is not None:
            self.show_target(self._target)
