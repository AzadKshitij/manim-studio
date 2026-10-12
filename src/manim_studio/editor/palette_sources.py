"""The commands the palette offers for an open editor window.

Everything is read from the live window each time the palette opens, so enabled
states, the scene list, recent files and blocks are current.  Menu and toolbar actions
come from the window's own QActions (nothing is hand-maintained).  The timeline
operations are among those actions, so the palette, menus, shortcuts and buttons run
the same code and show the same states.
"""

from __future__ import annotations

from collections.abc import Callable
from functools import partial
from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtGui import QAction, QKeySequence

from manim_studio.command_palette import ACTIONS, BLOCKS, RECENT, SCENES, TIMELINE, PaletteEntry
from manim_studio.editor.palette_dialog import PaletteItem
from manim_studio.timeline_model import format_duration

if TYPE_CHECKING:
    from manim_studio.editor.main_window import MainWindow

__all__ = ["build_items"]

#: Actions that would reopen the palette or duplicate it; never listed.
_EXCLUDED_TITLES = frozenset({"command palette"})


def build_items(window: MainWindow) -> list[PaletteItem]:
    """Every command available in ``window`` right now, in display order."""
    return [
        *_action_items(window),
        *_timeline_items(window),
        *_scene_items(window),
        *_recent_items(window),
        *_block_items(window),
    ]


def _action_items(window: MainWindow) -> list[PaletteItem]:
    items: list[PaletteItem] = []
    seen: set[str] = set()
    for action in window.findChildren(QAction):
        # Only the window's own actions: menu-owned entries (recent files, "(none)")
        # are reached through their own menus.
        if action.parent() is not window:
            continue
        title = _clean_title(action.text())
        folded = title.casefold()
        if not title or folded in seen or folded in _EXCLUDED_TITLES:
            continue
        seen.add(folded)
        enabled = action.isEnabled()
        detail = ("on" if action.isChecked() else "off") if action.isCheckable() else ""
        # Timeline operations name their group and show their key (bound elsewhere).
        shortcut = action.property("shortcut_text") or action.shortcut().toString(
            QKeySequence.SequenceFormat.NativeText
        )
        entry = PaletteEntry(
            key=f"action:{folded}",
            title=title,
            group=str(action.property("palette_group") or ACTIONS),
            detail=detail,
            shortcut=str(shortcut),
            enabled=enabled,
            reason="" if enabled else _unavailable_reason(action.toolTip()),
        )
        items.append(PaletteItem(entry, action.trigger))
    return items


def _timeline_items(window: MainWindow) -> list[PaletteItem]:
    transport = window.transport_bar
    has_scene = window.has_scene()
    transport_ops: tuple[tuple[str, str, str, Callable[[], object]], ...] = (
        ("play", "Play / pause", "Space", transport.playPauseRequested.emit),
        ("start", "Jump to start", "Home", partial(transport.jumpRequested.emit, "start")),
        ("end", "Jump to end", "End", partial(transport.jumpRequested.emit, "end")),
        ("back", "Step back one frame", "Left", partial(transport.stepRequested.emit, -1)),
        ("forward", "Step forward one frame", "Right", partial(transport.stepRequested.emit, 1)),
        ("loop", "Toggle loop", "", lambda: transport.set_loop(not transport.loop_enabled())),
        ("zoom-in", "Zoom timeline in", "+", partial(transport.zoomRequested.emit, "in")),
        ("zoom-out", "Zoom timeline out", "-", partial(transport.zoomRequested.emit, "out")),
        (
            "zoom-selection",
            "Zoom to selected range",
            "",
            partial(transport.zoomRequested.emit, "selection"),
        ),
    )
    items: list[PaletteItem] = []
    for key, title, shortcut, run in transport_ops:
        entry = PaletteEntry(
            key=f"timeline:{key}",
            title=title,
            group=TIMELINE,
            shortcut=shortcut,
            enabled=has_scene,
            reason="" if has_scene else "open a scene first",
        )
        items.append(PaletteItem(entry, run))

    return items


def _scene_items(window: MainWindow) -> list[PaletteItem]:
    current = window.current_scene_name()
    return [
        PaletteItem(
            PaletteEntry(
                key=f"scene:{name}",
                title=f"Preview scene: {name}",
                group=SCENES,
                detail="current" if name == current else "",
                keywords=name,
            ),
            partial(window.select_scene, name),
        )
        for name in window.scene_names()
    ]


def _recent_items(window: MainWindow) -> list[PaletteItem]:
    items: list[PaletteItem] = []
    for path in window.recent_files():
        scene = Path(path)
        entry = PaletteEntry(
            key=f"recent:{path}",
            title=f"Open {scene.name}",
            group=RECENT,
            detail=str(scene.parent),
            keywords=path,
        )
        items.append(PaletteItem(entry, partial(window.open_path, path)))
    return items


def _block_items(window: MainWindow) -> list[PaletteItem]:
    items: list[PaletteItem] = []
    for block in window.timeline.log.blocks:
        line = window.events.call_line(block.index)
        entry = PaletteEntry(
            key=f"block:{block.index}",
            title=f"#{block.index}  {block.name}",
            group=BLOCKS,
            detail=f"{format_duration(block.start)} · {format_duration(block.duration)}",
            keywords=f"line {line}" if line is not None else "",
        )
        items.append(PaletteItem(entry, partial(window.jump_to_block, block.index)))
    return items


def _clean_title(text: str) -> str:
    """The menu text's name: no shortcut column, mnemonics or trailing ellipsis."""
    return text.split("\t", 1)[0].replace("&", "").rstrip("…").strip()


def _unavailable_reason(tooltip: str) -> str:
    """Scene-dependent actions carry their reason after an em dash in the tooltip."""
    return tooltip.rsplit(" — ", 1)[-1] if " — " in tooltip else "not available right now"
