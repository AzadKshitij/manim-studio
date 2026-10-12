"""The timeline operations as shared Qt actions: one set for menus, shortcuts and buttons."""

from __future__ import annotations

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QSignalSpy
from PySide6.QtWidgets import QMainWindow, QWidget

from manim_studio.command_palette import TIMELINE
from manim_studio.editor.main_window import MainWindow
from manim_studio.editor.palette_sources import build_items
from manim_studio.editor.timeline_ops import TimelineOpActions
from manim_studio.editor.timeline_widget import TimelineWidget
from manim_studio.timeline_ops import OPS, TimelineFacts, op_states


@pytest.fixture
def host(qtbot) -> QMainWindow:  # type: ignore[no-untyped-def]
    window = QMainWindow()
    qtbot.addWidget(window)
    return window


@pytest.fixture
def window(qtbot) -> MainWindow:  # type: ignore[no-untyped-def]
    win = MainWindow(restore_settings=False, auto_preview=False, completion=False)
    qtbot.addWidget(win)
    return win


def test_each_operation_has_exactly_one_action(host: QMainWindow) -> None:
    ops = TimelineOpActions(host)
    assert set(ops.actions) == {op.key for op in OPS}
    assert all(action.parent() is host for action in ops.actions.values())


def test_operations_start_disabled_and_follow_the_states(host: QMainWindow) -> None:
    ops = TimelineOpActions(host)
    assert not any(action.isEnabled() for action in ops.actions.values())
    ops.apply(op_states(TimelineFacts(edit_count=2)))
    assert ops.action("apply").isEnabled()
    assert not ops.action("discard_orphans").isEnabled()
    assert ops.action("discard_orphans").toolTip().endswith("no orphaned edits")


def test_window_shortcuts_are_bound_and_display_only_ones_are_not(host: QMainWindow) -> None:
    ops = TimelineOpActions(host)
    ops.attach_to_window()
    assert ops.action("apply").shortcut().toString() == "Ctrl+Shift+A"
    assert ops.action("apply") in host.actions()
    # Ctrl+Z is the code editor's undo and the timeline's own key: the menu shows it only.
    assert ops.action("undo").shortcut().isEmpty()
    assert ops.action("undo").text() == "Undo timeline edit\tCtrl+Z"


def test_timeline_keys_attach_only_to_the_timeline(host: QMainWindow) -> None:
    timeline = QWidget(host)
    ops = TimelineOpActions(host)
    ops.attach_to_timeline(timeline)
    assert ops.action("delete") in timeline.actions()
    assert ops.action("delete").shortcutContext() == Qt.ShortcutContext.WidgetWithChildrenShortcut
    assert ops.action("apply") not in timeline.actions()


def test_activating_an_operation_emits_its_key(host: QMainWindow) -> None:
    ops = TimelineOpActions(host)
    spy = QSignalSpy(ops.requested)
    action = ops.action("hold")
    action.setEnabled(True)
    action.trigger()
    assert spy.count() == 1
    assert spy.at(0)[0] == "hold"


def test_timeline_menus_offer_the_shared_operations(host: QMainWindow) -> None:
    timeline = TimelineWidget(host)
    timeline.set_operation_actions(TimelineOpActions(host).actions)
    empty_area = [action.text() for action in timeline.context_menu(None).actions()]
    assert "Apply timeline edits to source" in empty_area
    assert "Undo timeline edit\tCtrl+Z" in empty_area
    on_block = [action.text() for action in timeline.context_menu(0).actions()]
    assert "Apply timeline edits to source" in on_block


def test_buttons_and_actions_show_the_same_states(window: MainWindow) -> None:
    window._refresh_edit_buttons()
    for key, action in window._timeline_ops.actions.items():
        assert action.isEnabled() == window.transport_bar.edit_button(key).isEnabled(), key


def test_each_timeline_operation_is_listed_once(window: MainWindow) -> None:
    titles = [item.entry.title for item in build_items(window)]
    for op in OPS:
        assert titles.count(op.title.rstrip("…")) == 1, op.title


def test_apply_is_listed_under_timeline_with_its_key(window: MainWindow) -> None:
    items = [
        item for item in build_items(window) if item.entry.title == "Apply timeline edits to source"
    ]
    assert len(items) == 1
    entry = items[0].entry
    assert entry.group == TIMELINE
    assert entry.shortcut == "Ctrl+Shift+A"
    assert not entry.enabled
    assert entry.reason == "no timeline edits"


def test_timeline_menu_carries_every_operation(window: MainWindow) -> None:
    menus = [a.menu() for a in window.menuBar().actions() if a.text() == "&Timeline"]
    assert len(menus) == 1
    texts = {action.text().split("\t")[0] for action in menus[0].actions() if action.text()}
    assert {op.title for op in OPS} <= texts


def test_snap_indicator_and_view_menu_stay_in_step(window: MainWindow) -> None:
    snap = window.transport_bar._snap_button
    window._snap_action.setChecked(False)
    assert not snap.isChecked()
    assert not window.timeline.snapping
    snap.setChecked(True)
    assert window._snap_action.isChecked()
    assert window.timeline.snapping
