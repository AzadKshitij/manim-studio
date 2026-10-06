"""Qt-side controller for the preview host process.

Owns the ``QProcess``, the loopback ``QTcpServer`` and the two socket channels
(control + frames), and republishes host events as Qt signals.  All socket I/O
happens on the GUI thread via Qt's event-driven sockets — no extra threads, no
blocking reads.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Any

from PySide6.QtCore import QObject, QProcess, QTimer, Signal
from PySide6.QtGui import QImage
from PySide6.QtNetwork import QHostAddress, QTcpServer, QTcpSocket

from manim_editor import ipc
from manim_editor.ipc import Channel, Command, Event, FrameParser

__all__ = ["PreviewController"]

log = logging.getLogger(__name__)


class PreviewController(QObject):
    """Spawns/monitors ``manim-editor-preview`` and routes the protocol."""

    helloReceived = Signal(dict)
    logReceived = Signal(dict)
    statusChanged = Signal(str, dict)
    tickReceived = Signal(float, int)
    stoppedReceived = Signal(dict)
    errorReceived = Signal(dict)
    execResultReceived = Signal(dict)
    frameReceived = Signal(QImage)
    hostLogReceived = Signal(str)
    stateChanged = Signal(str)  # starting | running | stopped | crashed

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._server = QTcpServer(self)
        self._server.newConnection.connect(self._on_new_connection)
        self._process = QProcess(self)
        self._process.setProcessChannelMode(QProcess.ProcessChannelMode.SeparateChannels)
        self._process.readyReadStandardError.connect(self._on_host_stderr)
        self._process.readyReadStandardOutput.connect(self._on_host_stdout)
        self._process.finished.connect(self._on_process_finished)
        self._process.errorOccurred.connect(self._on_process_error)
        self._control: QTcpSocket | None = None
        self._frames: QTcpSocket | None = None
        self._frame_parser = FrameParser()
        self._awaiting_hello: set[QTcpSocket] = set()
        self._expected_stop = False
        self.scene_path: Path | None = None
        self.scene_name: str | None = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    @property
    def is_running(self) -> bool:
        return self._process.state() != QProcess.ProcessState.NotRunning

    @property
    def is_connected(self) -> bool:
        return (
            self._control is not None
            and self._control.state() == QTcpSocket.SocketState.ConnectedState
        )

    def start(
        self,
        scene_path: str | Path,
        scene_name: str | None = None,
        *,
        embedded: bool = True,
        size: tuple[int, int] = (1280, 720),
        initial_frame: str = "end",
        fps: int | None = None,
    ) -> None:
        """(Re)start the host on the given scene file."""
        self._force_stop()
        self.scene_path = Path(scene_path).resolve()
        self.scene_name = scene_name
        if not self._server.isListening() and not self._server.listen(
            QHostAddress.SpecialAddress.LocalHost, 0
        ):
            raise RuntimeError(f"cannot listen on loopback: {self._server.errorString()}")
        port = self._server.serverPort()
        width, height = size
        args = [
            "-m",
            "manim_editor.preview_host",
            str(self.scene_path),
            "--port",
            str(port),
            "--size",
            f"{width}x{height}",
            "--initial-frame",
            initial_frame,
        ]
        if embedded:
            args.append("--embedded")
        if scene_name:
            args += ["--scene-name", scene_name]
        if fps is not None:
            args += ["--fps", str(fps)]
        self._expected_stop = False
        self.stateChanged.emit("starting")
        self._process.start(sys.executable, args)

    def shutdown(self, *, timeout_ms: int = 3000) -> None:
        """Ask the host to quit, then guarantee termination.

        The QUIT write must be flushed *before* blocking on the process, otherwise
        the socket never transmits it (no event loop while ``waitForFinished``
        blocks) and we would always fall back to killing the child.
        """
        self._expected_stop = True
        if self.is_connected and self._control is not None:
            self.send(Command.QUIT)
            self._control.flush()
            self._control.waitForBytesWritten(200)
        if self.is_running and not self._process.waitForFinished(timeout_ms):
            self._process.terminate()
            if not self._process.waitForFinished(1000):
                self._process.kill()
                self._process.waitForFinished(1000)
        self._close_sockets()
        self._server.close()

    def _force_stop(self) -> None:
        self._expected_stop = True
        if self.is_running:
            self._process.kill()
            self._process.waitForFinished(2000)
        self._close_sockets()
        if self._server.isListening():
            self._server.close()

    def _close_sockets(self) -> None:
        for socket in (self._control, self._frames):
            if socket is not None:
                socket.abort()
                socket.setParent(None)
                socket.deleteLater()
        self._control = None
        self._frames = None
        self._frame_parser.reset()
        self._awaiting_hello.clear()

    # ------------------------------------------------------------------
    # Commands
    # ------------------------------------------------------------------

    def send(self, command: str, **payload: Any) -> bool:
        """Send a control command; returns False when the channel is down."""
        if not self.is_connected:
            return False
        assert self._control is not None
        data = ipc.encode_message({"cmd": command, **payload})
        return self._control.write(data) == len(data)

    def reload(self, path: str | Path | None = None) -> bool:
        payload: dict[str, Any] = {}
        if path is not None:
            payload["path"] = str(Path(path).resolve())
        return self.send(Command.RELOAD, **payload)

    def load(self, path: str | Path, scene_name: str | None = None) -> bool:
        if scene_name is not None:
            self.scene_name = scene_name
        payload: dict[str, Any] = {"path": str(Path(path).resolve())}
        if scene_name is not None:
            payload["scene_name"] = scene_name
        return self.send(Command.LOAD, **payload)

    def play(self, from_index: int = 0) -> bool:
        return self.send(Command.PLAY, **{"from": int(from_index)})

    def stop_playback(self) -> bool:
        return self.send(Command.STOP)

    def seek(self, index: int) -> bool:
        return self.send(Command.SEEK, index=int(index))

    def scrub(self, time_seconds: float) -> bool:
        """Pose the scene at an arbitrary time (coalesced host-side)."""
        return self.send(Command.SCRUB, time=float(time_seconds))

    def exec_code(self, code: str) -> bool:
        return self.send(Command.EXEC, code=code)

    def ping(self) -> bool:
        return self.send(Command.PING)

    # ------------------------------------------------------------------
    # Socket handling
    # ------------------------------------------------------------------

    def _on_new_connection(self) -> None:
        socket = self._server.nextPendingConnection()
        if socket is None:
            return
        socket.setParent(self)
        self._awaiting_hello.add(socket)
        socket.readyRead.connect(lambda s=socket: self._on_socket_ready(s))
        socket.disconnected.connect(lambda s=socket: self._on_socket_closed(s))

    def _on_socket_ready(self, socket: QTcpSocket) -> None:
        if socket in self._awaiting_hello:
            if not socket.canReadLine():
                return
            raw = ipc.as_bytes(socket.readLine())
            try:
                hello = ipc.decode_message(raw)
            except ipc.ProtocolError as exc:
                log.warning("bad hello: %s", exc)
                socket.close()
                return
            self._awaiting_hello.discard(socket)
            channel = hello.get("channel")
            if channel == Channel.CONTROL:
                self._control = socket
            elif channel == Channel.FRAMES:
                self._frames = socket
                self._frame_parser.reset()
            else:
                log.warning("unknown channel %r", channel)
                socket.close()
                return
            self.helloReceived.emit(hello)
        if socket is self._control:
            self._read_control()
        elif socket is self._frames:
            self._read_frames()

    def _on_socket_closed(self, socket: QTcpSocket) -> None:
        if socket is self._control:
            self._control = None
        elif socket is self._frames:
            self._frames = None
            self._frame_parser.reset()

    def _read_control(self) -> None:
        assert self._control is not None
        while self._control.canReadLine():
            raw = ipc.as_bytes(self._control.readLine())
            try:
                message = ipc.decode_message(raw)
            except ipc.ProtocolError as exc:
                log.warning("bad control message: %s", exc)
                continue
            self._dispatch(message)

    def _read_frames(self) -> None:
        assert self._frames is not None
        data = ipc.as_bytes(self._frames.readAll())
        if not data:
            return
        try:
            frames = self._frame_parser.feed(data)
        except ipc.ProtocolError as exc:
            log.warning("frame protocol error: %s", exc)
            self._frame_parser.reset()
            return
        for frame in frames:
            image = QImage(
                frame.rgba,
                frame.width,
                frame.height,
                frame.width * 4,
                QImage.Format.Format_RGBA8888,
            ).copy()
            self.frameReceived.emit(image)

    def _dispatch(self, message: dict[str, Any]) -> None:
        event = message.get("event")
        if event == Event.LOG:
            self.logReceived.emit(message)
        elif event == Event.STATUS:
            self.statusChanged.emit(str(message.get("state", "unknown")), message)
        elif event == Event.TICK:
            self.tickReceived.emit(float(message.get("time", 0.0)), int(message.get("index", -1)))
        elif event == Event.STOPPED:
            self.stoppedReceived.emit(message)
        elif event == Event.ERROR:
            self.errorReceived.emit(message)
        elif event == Event.EXEC_RESULT:
            self.execResultReceived.emit(message)
        elif event == Event.PONG:
            pass
        else:
            log.debug("unhandled event %r", event)

    # ------------------------------------------------------------------
    # Process handling
    # ------------------------------------------------------------------

    def _on_host_stderr(self) -> None:
        raw = ipc.as_bytes(self._process.readAllStandardError()).decode("utf-8", "replace")
        for line in raw.splitlines():
            if line.strip():
                self.hostLogReceived.emit(line)

    def _on_host_stdout(self) -> None:
        raw = ipc.as_bytes(self._process.readAllStandardOutput()).decode("utf-8", "replace")
        for line in raw.splitlines():
            if line.strip():
                self.hostLogReceived.emit(line)

    def _on_process_finished(self, exit_code: int, _status: Any) -> None:
        self._close_sockets()
        if self._expected_stop:
            self.stateChanged.emit("stopped")
        else:
            self.stateChanged.emit("crashed")
            self.hostLogReceived.emit(f"preview host exited unexpectedly (code {exit_code})")

    def _on_process_error(self, error: QProcess.ProcessError) -> None:
        if error == QProcess.ProcessError.FailedToStart:
            self.stateChanged.emit("crashed")
            self.hostLogReceived.emit("preview host failed to start")

    def restarting_ping_later(self, delay_ms: int = 50) -> None:
        """Convenience for tests/tools: ping after the event loop settles."""
        QTimer.singleShot(delay_ms, self.ping)
