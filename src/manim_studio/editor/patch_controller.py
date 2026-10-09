"""Timeline edits as the editor sees them: history, persistence and orphans.

:class:`PatchController` owns the :class:`~manim_studio.sidecar.PatchStack` for the open
scene.  It turns timeline gestures ("block 3 should last 2.5 s") into anchored patches,
keeps ``<scene>.studio.json`` in step with every change, remembers which blocks come from
which call site (from the host's ``log`` event) and which edits the host could not apply
(orphans).  It never talks to the host itself: it emits :attr:`patchesChanged` and the
window forwards the payload, which keeps this class unit-testable without a process.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from PySide6.QtCore import QObject, Signal

from manim_studio.patches import (
    Anchor,
    Delete,
    Patch,
    PatchError,
    PatchResult,
    SetDuration,
    apply_patches,
    parse_patch,
)
from manim_studio.sidecar import PatchStack, SidecarError, load_sidecar, save_sidecar
from manim_studio.site_index import SiteIndex, index_source

if TYPE_CHECKING:
    from collections.abc import Sequence

__all__ = ["BlockRef", "OrphanedEdit", "PatchController"]

#: Shortest duration the timeline will write (one frame at 30 fps).
MIN_DURATION = 1.0 / 30.0


@dataclass(frozen=True, slots=True)
class BlockRef:
    """Which call site produced a timeline block, and which loop iteration of it."""

    site_id: str
    occurrence: int


@dataclass(frozen=True, slots=True)
class OrphanedEdit:
    """An edit the host could not apply, with the reason to show the user."""

    patch: Patch
    reason: str


class PatchController(QObject):
    """History, sidecar persistence and orphan tracking for timeline edits."""

    #: The patch set changed: the serialized patches the preview/export should run.
    patchesChanged = Signal(object)
    #: Edit/orphan counts changed (drives the status-bar chip and menu state).
    stateChanged = Signal()
    #: A short human-readable note for the status line.
    message = Signal(str)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._stack = PatchStack()
        self._scene_path: Path | None = None
        self._refs: tuple[BlockRef | None, ...] = ()
        self._orphans: tuple[OrphanedEdit, ...] = ()
        self._applied = 0
        self._index_cache: tuple[int, SiteIndex] | None = None

    # -- state -------------------------------------------------------------

    @property
    def patches(self) -> tuple[Patch, ...]:
        return self._stack.patches

    def payload(self) -> list[dict[str, Any]]:
        """The patches in wire form, as the host expects them."""
        return [patch.to_dict() for patch in self._stack.patches]

    @property
    def edit_count(self) -> int:
        return len(self._stack.patches)

    @property
    def orphans(self) -> tuple[OrphanedEdit, ...]:
        return self._orphans

    @property
    def orphan_count(self) -> int:
        return len(self._orphans)

    @property
    def applied_count(self) -> int:
        """How many edits the host applied to the scene it last traced."""
        return self._applied

    @property
    def can_undo(self) -> bool:
        return self._stack.can_undo

    @property
    def can_redo(self) -> bool:
        return self._stack.can_redo

    @property
    def revision(self) -> int:
        return self._stack.revision

    def summary(self) -> str:
        """``"3 edits (1 orphaned)"``, or ``""`` when there are none."""
        count = self.edit_count
        if count == 0:
            return ""
        text = f"{count} timeline edit{'s' if count != 1 else ''}"
        if self.orphan_count:
            text += f" ({self.orphan_count} orphaned)"
        return text

    # -- scene lifecycle ---------------------------------------------------

    def open_scene(self, path: str | Path) -> list[dict[str, Any]]:
        """Start a fresh history from the scene's sidecar; returns the wire payload."""
        self._scene_path = Path(path)
        try:
            patches = load_sidecar(self._scene_path)
        except SidecarError as exc:
            patches = ()
            self.message.emit(f"ignoring timeline edits: {exc}")
        self._stack = PatchStack(patches)
        self._refs = ()
        self._orphans = ()
        self._applied = 0
        self.stateChanged.emit()
        return self.payload()

    def close_scene(self) -> None:
        self._scene_path = None
        self._stack = PatchStack()
        self._refs = ()
        self._orphans = ()
        self.stateChanged.emit()

    # -- host feedback -----------------------------------------------------

    def on_log(self, message: dict[str, Any]) -> None:
        """Adopt the per-block call sites and the orphan report of a fresh trace."""
        self._refs = _parse_refs(message.get("sites"))
        self._orphans = _parse_orphans(message.get("patches"))
        report = message.get("patches")
        applied = report.get("applied") if isinstance(report, dict) else 0
        self._applied = applied if isinstance(applied, int) else 0
        self.stateChanged.emit()

    def block_ref(self, index: int) -> BlockRef | None:
        if 0 <= index < len(self._refs):
            return self._refs[index]
        return None

    def iteration_count(self, index: int) -> int:
        """How many blocks share block ``index``'s call site (``1`` outside loops)."""
        ref = self.block_ref(index)
        if ref is None:
            return 1
        return sum(1 for other in self._refs if other is not None and other.site_id == ref.site_id)

    def affected_blocks(self, index: int, *, iteration_only: bool = False) -> list[int]:
        """Blocks an edit of block ``index`` changes: its whole call site, or just it."""
        ref = self.block_ref(index)
        if ref is None or iteration_only:
            return [index]
        return [i for i, other in enumerate(self._refs) if other and other.site_id == ref.site_id]

    def sibling_duration_source(self, index: int) -> int | None:
        """Another block of the same call (a different loop iteration), if any."""
        ref = self.block_ref(index)
        if ref is None:
            return None
        for i, other in enumerate(self._refs):
            if other and other.site_id == ref.site_id and i != index:
                return i
        return None

    def block_is_edited(self, index: int) -> bool:
        ref = self.block_ref(index)
        return ref is not None and any(
            patch.anchor.site_id == ref.site_id for patch in self._stack.patches
        )

    # -- edits -------------------------------------------------------------

    def set_block_duration(
        self,
        index: int,
        seconds: float,
        *,
        iteration_only: bool = False,
        sibling_duration: float | None = None,
    ) -> bool:
        """Make block ``index`` last ``seconds``.

        By default the edit applies to the call site, i.e. every iteration of a loop
        body.  ``iteration_only`` narrows it to this block's iteration; the call then
        needs a value for the other iterations, which is ``sibling_duration`` (the
        duration the other iterations currently have) when the call has no explicit
        duration in the source yet.
        """
        ref = self.block_ref(index)
        if ref is None:
            self.message.emit(f"block {index} has no known call site, so it cannot be edited")
            return False
        value = float(seconds)
        if not math.isfinite(value) or value < MIN_DURATION:
            self.message.emit(f"a duration must be at least {MIN_DURATION:.3f}s")
            return False
        changed = False
        if iteration_only and self.iteration_count(index) > 1:
            site = self._site(ref.site_id)
            has_whole = any(
                isinstance(patch, SetDuration) and patch.anchor == Anchor(ref.site_id)
                for patch in self._stack.patches
            )
            if (site is None or site.duration.kind == "absent") and not has_whole:
                if sibling_duration is None:
                    self.message.emit("cannot edit one iteration: the other iterations are unknown")
                    return False
                changed |= self._stack.add(SetDuration(Anchor(ref.site_id), sibling_duration))
            changed |= self._stack.add(SetDuration(Anchor(ref.site_id, ref.occurrence), value))
        else:
            changed = self._stack.add(SetDuration(Anchor(ref.site_id), value))
        return self._commit(changed)

    def delete_block(self, index: int) -> bool:
        """Remove block ``index``'s call (and with it every iteration of a loop)."""
        ref = self.block_ref(index)
        if ref is None:
            self.message.emit(f"block {index} has no known call site, so it cannot be edited")
            return False
        return self._commit(self._stack.add(Delete(Anchor(ref.site_id))))

    def reset_block(self, index: int) -> bool:
        """Drop every edit of block ``index``'s call site."""
        ref = self.block_ref(index)
        if ref is None:
            return False
        return self._commit(self._stack.discard_site(ref.site_id))

    def undo(self) -> bool:
        return self._commit(self._stack.undo())

    def redo(self) -> bool:
        return self._commit(self._stack.redo())

    def clear(self) -> bool:
        """Discard every timeline edit."""
        return self._commit(self._stack.clear())

    def discard_edit(self, patch: Patch) -> bool:
        """Drop one specific edit (used by the orphan tray)."""
        return self._commit(self._stack.discard(patch))

    @staticmethod
    def describe(patch: Patch) -> str:
        """A short human description of an edit, e.g. ``wait in Demo.construct -> 2.50 s``."""
        parts = patch.anchor.site_id.split(":")
        where = f"{parts[1]} in {parts[0]}" if len(parts) == 4 else patch.anchor.site_id
        if patch.anchor.occurrence is not None:
            where += f" (iteration {patch.anchor.occurrence + 1})"
        if isinstance(patch, SetDuration):
            return f"{where} \u2192 {patch.seconds:.2f} s"
        return f"delete {where}"

    def discard_orphans(self) -> bool:
        """Drop the edits the host reported as orphaned."""
        orphaned = {orphan.patch for orphan in self._orphans}
        if not orphaned:
            return False
        kept = tuple(patch for patch in self._stack.patches if patch not in orphaned)
        return self._commit(self._stack.replace(kept))

    # -- apply to source ---------------------------------------------------

    def plan_apply(self, source: str) -> PatchResult | None:
        """What "Apply to source" would write (per-iteration edits are not bakeable)."""
        if not self._stack.patches:
            return None
        try:
            return apply_patches(source, self._stack.patches, runtime=False)
        except SyntaxError:
            return None

    def finish_apply(self, result: PatchResult) -> bool:
        """The planned text was written: keep only the edits that were not baked in."""
        applied = set(result.applied)
        remaining = tuple(patch for patch in self._stack.patches if patch not in applied)
        return self._commit(self._stack.replace(remaining))

    # -- internals ---------------------------------------------------------

    def _site(self, site_id: str) -> Any:
        index = self._source_index()
        return None if index is None else index.by_id(site_id)

    def _source_index(self) -> SiteIndex | None:
        """The call-site index of the scene file, cached until the file changes."""
        if self._scene_path is None:
            return None
        try:
            stamp = self._scene_path.stat().st_mtime_ns
        except OSError:
            return None
        if self._index_cache is not None and self._index_cache[0] == stamp:
            return self._index_cache[1]
        try:
            index = index_source(self._scene_path.read_text(encoding="utf-8"))
        except (OSError, SyntaxError):
            return None
        self._index_cache = (stamp, index)
        return index

    def duration_kind(self, index: int) -> str | None:
        """How block ``index``'s call spells its duration: literal, absent or expr."""
        ref = self.block_ref(index)
        site = None if ref is None else self._site(ref.site_id)
        return None if site is None else str(site.duration.kind)

    def _commit(self, changed: bool) -> bool:
        if not changed:
            return False
        if self._scene_path is not None:
            try:
                save_sidecar(self._scene_path, self._stack.patches)
            except OSError as exc:
                self.message.emit(f"could not save timeline edits: {exc}")
        self.stateChanged.emit()
        self.patchesChanged.emit(self.payload())
        return True


def _parse_refs(raw: object) -> tuple[BlockRef | None, ...]:
    if not isinstance(raw, list):
        return ()
    refs: list[BlockRef | None] = []
    for item in raw:
        if (
            isinstance(item, dict)
            and isinstance(item.get("id"), str)
            and isinstance(item.get("occ"), int)
            and not isinstance(item.get("occ"), bool)
        ):
            refs.append(BlockRef(item["id"], item["occ"]))
        else:
            refs.append(None)
    return tuple(refs)


def _parse_orphans(raw: object) -> tuple[OrphanedEdit, ...]:
    if not isinstance(raw, dict):
        return ()
    items: Sequence[object] = raw.get("orphaned") or ()
    orphans: list[OrphanedEdit] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        try:
            patch = parse_patch(item.get("patch"))
        except PatchError:
            continue
        orphans.append(OrphanedEdit(patch, str(item.get("reason", "cannot be applied"))))
    return tuple(orphans)
