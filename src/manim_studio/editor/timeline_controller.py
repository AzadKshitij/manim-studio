"""Scrub transport for rendered low-quality previews (timeline v1).

Wraps ``QMediaPlayer`` + ``QVideoSink`` so rendered frames arrive as ``QImage``
and can be shown in the same preview viewport as live frames.  No ``QVideoWidget``
is used, which keeps the embedded panel single-widget and offscreen-testable.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from PySide6.QtCore import QObject, QUrl, Signal
from PySide6.QtGui import QImage
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer, QVideoSink

__all__ = ["MediaTransport"]

log = logging.getLogger(__name__)


class MediaTransport(QObject):
    """Play/scrub one rendered video file, emitting frames as images."""

    frameReady = Signal(QImage)
    durationChanged = Signal(float)
    positionChanged = Signal(float)
    stateChanged = Signal(str)  # "loaded" | "playing" | "paused" | "stopped" | "failed"
    failed = Signal(str)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._player = QMediaPlayer(self)
        self._audio = QAudioOutput(self)
        self._audio.setVolume(0.0)
        self._player.setAudioOutput(self._audio)
        self._sink = QVideoSink(self)
        self._player.setVideoSink(self._sink)
        self._sink.videoFrameChanged.connect(self._on_video_frame)
        self._player.positionChanged.connect(self._on_position)
        self._player.durationChanged.connect(self._on_duration)
        self._player.errorOccurred.connect(self._on_error)
        self._path: Path | None = None

    # ------------------------------------------------------------------

    @property
    def path(self) -> Path | None:
        return self._path

    @property
    def duration(self) -> float:
        return self._player.duration() / 1000.0

    @property
    def position(self) -> float:
        return self._player.position() / 1000.0

    @property
    def is_playing(self) -> bool:
        return self._player.playbackState() == QMediaPlayer.PlaybackState.PlayingState

    @property
    def has_media(self) -> bool:
        return self._path is not None and self.duration > 0.0

    # ------------------------------------------------------------------

    def load(self, path: str | Path) -> None:
        """Load a rendered video; emits ``stateChanged('loaded')`` once ready."""
        target = Path(path)
        if not target.is_file():
            self.failed.emit(f"video not found: {target}")
            return
        self._path = target
        self._player.setSource(QUrl.fromLocalFile(str(target)))
        self.stateChanged.emit("loaded")

    def unload(self) -> None:
        self._player.stop()
        self._player.setSource(QUrl())
        self._path = None
        self.stateChanged.emit("stopped")

    def play(self) -> None:
        if self._path is not None:
            self._player.play()
            self.stateChanged.emit("playing")

    def pause(self) -> None:
        self._player.pause()
        self.stateChanged.emit("paused")

    def toggle(self) -> None:
        if self.is_playing:
            self.pause()
        else:
            self.play()

    def stop(self) -> None:
        self._player.stop()
        self.stateChanged.emit("stopped")

    def seek(self, seconds: float) -> None:
        """Seek and force a frame emission even while paused."""
        if self._path is None:
            return
        self._player.setPosition(max(0, int(seconds * 1000)))

    def set_rate(self, rate: float) -> None:
        """Set playback speed (1.0 = normal) for the rendered video."""
        try:
            self._player.setPlaybackRate(float(rate))
        except Exception:  # pragma: no cover - backend may not support rates
            log.debug("playback rate %s unsupported", rate)

    def set_looping(self, enabled: bool) -> None:
        """Loop the rendered video forever (or play once)."""
        loops = QMediaPlayer.Loops.Infinite if enabled else 1
        try:
            self._player.setLoops(loops)
        except Exception:  # pragma: no cover - backend may not support loops
            log.debug("looping unsupported")

    # ------------------------------------------------------------------

    def _on_video_frame(self, frame: Any) -> None:
        image = frame.toImage()
        if not image.isNull():
            self.frameReady.emit(image)

    def _on_position(self, milliseconds: int) -> None:
        self.positionChanged.emit(milliseconds / 1000.0)

    def _on_duration(self, milliseconds: int) -> None:
        self.durationChanged.emit(milliseconds / 1000.0)

    def _on_error(self, error: Any, message: str) -> None:
        log.warning("media error %s: %s", error, message)
        self.failed.emit(message or "media playback error")
