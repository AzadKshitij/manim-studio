"""App stats dialog: filmstrip cache RAM, host/editor memory and timeline totals.

The dialog is intentionally dumb: the host snapshot arrives asynchronously via
``PreviewController.statsReceived`` and the editor snapshot is pulled through a
callable so the dialog never imports scene or GL code. A 1 s poll timer keeps
both halves fresh while the dialog is visible.
"""

from __future__ import annotations

import contextlib
from collections.abc import Callable
from typing import Any

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGroupBox,
    QLabel,
    QVBoxLayout,
    QWidget,
)

from manim_studio.stats import cache_summary, format_bytes
from manim_studio.timeline_model import format_duration

__all__ = ["AppStatsDialog"]


class AppStatsDialog(QDialog):
    """Live RAM/cache breakdown; polling starts on show, stops on hide."""

    def __init__(
        self,
        controller: Any,
        editor_snapshot: Callable[[], dict[str, Any]],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("App stats")
        self.setMinimumWidth(440)
        self._controller = controller
        self._editor_snapshot = editor_snapshot
        self._host: dict[str, Any] = {}

        self._cache_value = self._make_row()
        self._cache_status_value = self._make_row()
        self._host_rss_value = self._make_row()
        self._pump_value = self._make_row()
        self._scene_value = self._make_row()
        self._editor_rss_value = self._make_row()
        self._api_value = self._make_row()
        self._timeline_value = self._make_row()

        layout = QVBoxLayout(self)
        layout.addWidget(
            self._make_group(
                "Cache (filmstrip)",
                [("Usage", self._cache_value), ("Status", self._cache_status_value)],
            )
        )
        layout.addWidget(
            self._make_group(
                "Preview host",
                [
                    ("Memory", self._host_rss_value),
                    ("Frames", self._pump_value),
                    ("Scene", self._scene_value),
                ],
            )
        )
        layout.addWidget(
            self._make_group(
                "Editor",
                [("Memory", self._editor_rss_value), ("Completions", self._api_value)],
            )
        )
        layout.addWidget(self._make_group("Timeline", [("Blocks", self._timeline_value)]))
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self._timer = QTimer(self)
        self._timer.setInterval(1000)
        self._timer.timeout.connect(self.refresh)
        self._render_editor()

    # ------------------------------------------------------------------
    # Data
    # ------------------------------------------------------------------

    def set_host_stats(self, message: dict[str, Any]) -> None:
        self._host = dict(message)
        self._render_host()

    def refresh(self) -> None:
        """Poll the host once and repaint the editor half from the snapshot."""
        with contextlib.suppress(Exception):  # the dialog must never break the editor
            self._controller.stats()
        self._render_editor()
        self._render_host()

    # ------------------------------------------------------------------
    # Qt plumbing
    # ------------------------------------------------------------------

    def showEvent(self, event: Any) -> None:
        super().showEvent(event)
        self.refresh()
        if not self._timer.isActive():
            self._timer.start()

    def hideEvent(self, event: Any) -> None:
        self._timer.stop()
        super().hideEvent(event)

    # ------------------------------------------------------------------
    # Rendering
    # ------------------------------------------------------------------

    @staticmethod
    def _make_row() -> QLabel:
        label = QLabel("—")
        label.setTextInteractionFlags(
            label.textInteractionFlags() | Qt.TextInteractionFlag.TextSelectableByMouse
        )
        return label

    @staticmethod
    def _make_group(title: str, rows: list[tuple[str, QLabel]]) -> QGroupBox:
        group = QGroupBox(title)
        form = QFormLayout(group)
        for name, widget in rows:
            form.addRow(f"{name}:", widget)
        return group

    def _render_host(self) -> None:
        host = self._host
        if not host:
            return
        frames = host.get("frames")
        byte_size = host.get("bytes")
        max_bytes = host.get("max_bytes")
        max_frames = host.get("max_frames")
        try:
            frames_int = int(frames) if frames is not None else None
        except (TypeError, ValueError):
            frames_int = None
        self._cache_value.setText(cache_summary(frames_int, byte_size, max_bytes, max_frames))
        if "done" in host or "capped" in host:
            if host.get("capped"):
                status = "capped (budget reached)"
            elif host.get("done"):
                status = "ready"
            else:
                status = f"crawling… ({frames_int if frames_int is not None else '—'} frames)"
            self._cache_status_value.setText(status)
        self._host_rss_value.setText(format_bytes(host.get("host_rss")))
        sent = host.get("sent", "—")
        dropped = host.get("dropped", "—")
        self._pump_value.setText(f"{sent} sent · {dropped} dropped")
        fps = host.get("fps", "—")
        size = host.get("size")
        size_text = (
            f"{size[0]}x{size[1]}" if isinstance(size, (list, tuple)) and len(size) == 2 else "—"
        )
        scene = host.get("scene_name") or "—"
        self._scene_value.setText(f"{scene} · {fps} fps · {size_text}")

    def _render_editor(self) -> None:
        try:
            snapshot = self._editor_snapshot()
        except Exception:
            return
        self._editor_rss_value.setText(format_bytes(snapshot.get("editor_rss")))
        api_count = snapshot.get("api_names")
        self._api_value.setText(str(api_count) if api_count is not None else "—")
        blocks = snapshot.get("blocks")
        duration = snapshot.get("total_duration")
        if isinstance(blocks, int) and isinstance(duration, (int, float)):
            self._timeline_value.setText(f"{blocks} blocks · {format_duration(duration)}")
        else:
            self._timeline_value.setText("—")
