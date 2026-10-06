"""Realtime preview host — the only process in the system that owns a GL context.

The host is spawned by the editor (or run standalone) and speaks the versioned
protocol from :mod:`manim_editor.ipc`:

* **control** — JSON lines; commands in, events out.  A reader thread turns inbound
  commands into a queue and fast-paths ``stop``/``quit`` so playback can be
  interrupted from inside a blocking ``Scene.play``.
* **frames** — only in ``--embedded`` mode: RGBA frames (zlib, length-prefixed) from
  a hidden GL window, with latest-frame-wins backpressure.

Design rules (see AGENTS.md): one GL context, one thread; the Qt process never
imports manimlib; module import of this file must not require a GPU.
"""

from __future__ import annotations

import argparse
import contextlib
import io
import logging
import os
import queue
import socket
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from manim_editor import ipc
from manim_editor.ipc import (
    Channel,
    Command,
    Event,
    JsonLineReader,
    JsonLineWriter,
    encode_message,
    hello_message,
)
from manim_editor.scene_runner import (
    SceneLoadError,
    check_scene_file,
    format_scene_error,
    load_scene_module,
    make_scene,
    run_construct,
    select_scene_class,
)
from manim_editor.timeline_model import TimelineBlock
from manim_editor.tracer import TraceEvent, TraceRecorder

__all__ = ["FramePump", "HostConfig", "SceneHost", "TickThrottler", "main", "parse_size"]

log = logging.getLogger("manim_editor.preview_host")

CONNECT_TIMEOUT_S = 15.0
TICK_INTERVAL_S = 0.05
DEFAULT_SIZE = (1280, 720)


class TickThrottler:
    """Rate-limits playhead ticks; the clock is injectable for tests."""

    def __init__(self, interval: float = TICK_INTERVAL_S, clock: Any = time.monotonic) -> None:
        self.interval = interval
        self._clock = clock
        self._last: float | None = None

    def ready(self) -> bool:
        now = self._clock()
        if self._last is None or now - self._last >= self.interval:
            self._last = now
            return True
        return False

    def reset(self) -> None:
        self._last = None


class FramePump:
    """Latest-frame-wins RGBA streamer.

    ``offer()`` is called from the GL thread and never blocks; compression and the
    blocking send happen on the pump thread.  When the transport cannot keep up,
    frames are dropped instead of queued.
    """

    def __init__(self, sock: socket.socket, *, level: int = 1) -> None:
        self._sock = sock
        self.level = level
        self.frames_sent = 0
        self.frames_dropped = 0
        self._cond = threading.Condition()
        self._slot: tuple[bytes, int, int] | None = None
        self._running = True
        self._started = False
        self._thread = threading.Thread(target=self._loop, name="frame-pump", daemon=True)

    def start(self) -> None:
        self._started = True
        self._thread.start()

    def offer(self, rgba: bytes, width: int, height: int) -> None:
        with self._cond:
            if not self._running:
                return
            if self._slot is not None:
                self.frames_dropped += 1
            self._slot = (rgba, width, height)
            self._cond.notify()

    def _loop(self) -> None:
        while True:
            with self._cond:
                while self._running and self._slot is None:
                    self._cond.wait(0.5)
                if not self._running:
                    return
                item = self._slot
                self._slot = None
            assert item is not None
            rgba, width, height = item
            try:
                self._sock.sendall(ipc.encode_frame(width, height, rgba, level=self.level))
            except OSError:
                with self._cond:
                    self._running = False
                return
            with self._cond:
                self.frames_sent += 1

    def close(self) -> None:
        with self._cond:
            self._running = False
            self._slot = None
            self._cond.notify_all()
        with contextlib.suppress(OSError):
            self._sock.shutdown(socket.SHUT_RDWR)
        with contextlib.suppress(OSError):
            self._sock.close()
        if self._started:
            self._thread.join(timeout=1)


@dataclass
class HostConfig:
    """Everything the host needs to run."""

    scene_path: Path
    scene_name: str | None = None
    port: int | None = None
    embedded: bool = False
    size: tuple[int, int] = DEFAULT_SIZE
    initial_frame: str = "start"
    fps: int | None = None
    fail_fast: bool = False


class SceneHost:
    """Owns the window, the scene lifecycle, playback and the IPC connections."""

    def __init__(self, config: HostConfig, logger: logging.Logger = log) -> None:
        self.config = config
        self.log = logger
        self.stop_event = threading.Event()
        self.quit_event = threading.Event()
        self.commands: queue.Queue[dict[str, Any]] = queue.Queue()
        self.window: Any = None
        self.module: Any = None
        self.scene: Any = None
        self.scene_class: type | None = None
        self.blocks: tuple[TimelineBlock, ...] = ()
        self.stats: dict[str, int] = {}
        self.events: tuple[TraceEvent, ...] = ()
        self.control_sock: socket.socket | None = None
        self.control: JsonLineWriter | None = None
        self.frame_pump: FramePump | None = None
        self.tick = TickThrottler()
        self.shell: Any = None
        self._checkpoints: Any = None
        self._loaded_once = False

    # ------------------------------------------------------------------
    # IPC plumbing
    # ------------------------------------------------------------------

    def emit(self, event: str, **payload: Any) -> None:
        message = {"event": event, **payload}
        if self.control is not None:
            if not self.control.send(message):
                self.quit_event.set()
        else:
            self.log.info("%s %s", event, payload)

    def emit_status(self, state: str, **extra: Any) -> None:
        self.emit(Event.STATUS, state=state, **extra)

    def emit_error(self, exc: BaseException, *, context: str = "") -> None:
        info = format_scene_error(exc, self.config.scene_path)
        self.emit(Event.ERROR, **info.to_dict(), context=context)
        self.log.error("%s%s", f"[{context}] " if context else "", info.message)
        if info.traceback_text:
            self.log.debug("%s", info.traceback_text)

    def _connect(self, channel: str) -> socket.socket:
        if self.config.port is None:
            raise ConnectionError("no port configured")
        deadline = time.monotonic() + CONNECT_TIMEOUT_S
        last: OSError | None = None
        while time.monotonic() < deadline:
            try:
                sock = socket.create_connection(("127.0.0.1", self.config.port), timeout=2.0)
                sock.settimeout(None)
                return sock
            except OSError as exc:
                last = exc
                time.sleep(0.1)
        raise ConnectionError(f"cannot reach editor on port {self.config.port} ({channel}): {last}")

    def _start_control(self) -> None:
        sock = self._connect(Channel.CONTROL)
        self.control_sock = sock
        self.control = JsonLineWriter(sock.makefile("wb"))
        self.control.send(
            hello_message(
                Channel.CONTROL,
                role="host",
                pid=os.getpid(),
                scene=str(self.config.scene_path),
                scene_name=self.config.scene_name,
                embedded=self.config.embedded,
                size=list(self.config.size),
                fps=self.config.fps or 30,
            )
        )
        reader = JsonLineReader(sock.makefile("rb"))
        threading.Thread(
            target=self._control_reader,
            args=(reader,),
            name="control-reader",
            daemon=True,
        ).start()

    def _control_reader(self, reader: JsonLineReader) -> None:
        try:
            for message in reader.messages():
                command = message.get("cmd")
                if command in (Command.STOP, Command.QUIT):
                    self.stop_event.set()
                if command == Command.QUIT:
                    self.quit_event.set()
                    break
                self.commands.put(message)
        except ipc.ProtocolError as exc:
            self.log.warning("control protocol error: %s", exc)
        finally:
            # The editor went away; there is nobody left to serve.
            self.stop_event.set()
            self.quit_event.set()

    def _start_frames(self) -> None:
        sock = self._connect(Channel.FRAMES)
        sock.sendall(encode_message(hello_message(Channel.FRAMES, role="host")))
        self.frame_pump = FramePump(sock)
        self.frame_pump.start()

    # ------------------------------------------------------------------
    # Window + scene lifecycle
    # ------------------------------------------------------------------

    def _create_window(self) -> None:
        from manimlib.window import Window

        width, height = self.config.size
        self.window = Window(size=(width, height))
        if self.config.embedded:
            self._hide_window()

    def _hide_window(self) -> None:
        if self.window is None:
            return
        inner = getattr(self.window, "_window", None)
        if inner is None:
            return
        with contextlib.suppress(Exception):
            inner.set_visible(False)
        with contextlib.suppress(Exception):
            inner.vsync = False

    def _make_scene(
        self,
        *,
        skip_animations: bool,
        start: int | None = None,
        end: int | None = None,
    ) -> Any:
        if self.scene_class is None:
            raise SceneLoadError("no scene loaded")
        return make_scene(
            self.scene_class,
            window=self.window,
            resolution=self.config.size,
            fps=self.config.fps,
            skip_animations=skip_animations,
            start_at_animation_number=start,
            end_at_animation_number=end,
            preview_while_skipping=False,
        )

    def load(self, path: str | Path | None = None) -> None:
        """(Re)load the scene: import, trace, show the initial frame and notify."""
        if path is not None:
            self.config.scene_path = Path(path)
        scene_path = self.config.scene_path
        check = check_scene_file(scene_path)
        if not check.ok:
            raise SceneLoadError(f"{scene_path}: {check.error}")

        module = load_scene_module(scene_path, is_reload=self._loaded_once)
        scene_class = select_scene_class(module, self.config.scene_name, path=scene_path)
        self.module = module
        self.scene_class = scene_class
        self._loaded_once = True
        self.log.info("loaded %s (%s)", scene_class.__name__, scene_path)

        self._trace(scene_path)
        self._show_initial_frame()
        self._refresh_shell_namespace()

    def _trace(self, scene_path: Path) -> None:
        scene = self._make_scene(skip_animations=True)
        recorder = TraceRecorder(record_events=True)
        recorder.attach(scene)
        started = time.perf_counter()
        try:
            run_construct(scene)
            snapshot = recorder.snapshot()
        finally:
            recorder.detach()
            self._release_scene(scene)
        self.blocks = snapshot.blocks
        self.stats = snapshot.stats
        self.events = snapshot.events
        total = self.blocks[-1].end if self.blocks else 0.0
        self.log.info(
            "trace: %d blocks, %.3fs, %.2fs wall",
            len(self.blocks),
            total,
            time.perf_counter() - started,
        )
        self.emit(
            Event.LOG,
            blocks=[block.to_dict() for block in self.blocks],
            stats=self.stats,
            events=[event.to_dict() for event in self.events],
            total_duration=total,
            trace_seconds=time.perf_counter() - started,
            scene=str(scene_path),
            scene_name=type(scene).__name__,
        )

    def _show_initial_frame(self) -> None:
        if self.config.initial_frame == "end":
            scene = self._make_scene(skip_animations=True)
        else:
            scene = self._make_scene(skip_animations=True, end=0)
        self._activate_scene(scene)
        self.stop_event.clear()
        self._render_current_frame()
        self.push_current_frame()
        self.emit_status("idle", index=0)

    def _activate_scene(self, scene: Any) -> None:
        self._dispose_current_scene()
        self.scene = scene
        self._install_hooks(scene)
        if self.config.embedded:
            # Read back from the offscreen buffer instead of the hidden window.
            scene.camera.use_window_fbo(False)

    def _install_hooks(self, scene: Any) -> None:
        from manimlib.scene.scene import EndScene

        original_update = scene.update_frame

        def update_frame(dt: float = 0, force_draw: bool = False) -> Any:
            if self.stop_event.is_set():
                raise EndScene()
            return original_update(dt, force_draw)

        scene.update_frame = update_frame

        original_emit = scene.emit_frame

        def emit_frame() -> Any:
            result = original_emit()
            self._on_rendered_frame(scene)
            return result

        scene.emit_frame = emit_frame

    def _dispose_current_scene(self) -> None:
        if self.scene is not None:
            self._release_scene(self.scene)
            self.scene = None

    @staticmethod
    def _release_scene(scene: Any) -> None:
        camera = getattr(scene, "camera", None)
        if camera is None:
            return
        for attribute in ("fbo_for_files", "draw_fbo"):
            fbo = getattr(camera, attribute, None)
            if fbo is not None:
                with contextlib.suppress(Exception):
                    fbo.release()

    # ------------------------------------------------------------------
    # Rendering + frames
    # ------------------------------------------------------------------

    def _render_current_frame(self) -> None:
        if self.scene is None:
            return
        self.scene.update_frame(dt=0, force_draw=True)

    def _on_rendered_frame(self, scene: Any) -> None:
        if self.frame_pump is not None:
            self.push_current_frame()
        if self.control is not None and self.tick.ready():
            self.emit(
                Event.TICK,
                time=round(float(scene.time), 4),
                index=int(scene.num_plays),
            )

    def push_current_frame(self) -> None:
        """Read back the current offscreen frame and hand it to the pump.

        Frames are flipped to upright RGBA so the editor can build a ``QImage``
        without per-pixel work.
        """
        if self.frame_pump is None or self.scene is None:
            return
        import numpy as np

        camera = self.scene.camera
        width, height = (int(v) for v in camera.default_pixel_shape)
        raw = camera.get_raw_fbo_data()
        array = np.frombuffer(raw, dtype=np.uint8).reshape(height, width, 4)[::-1]
        self.frame_pump.offer(array.tobytes(), width, height)

    # ------------------------------------------------------------------
    # Commands
    # ------------------------------------------------------------------

    def _drain_commands(self) -> None:
        while True:
            try:
                message = self.commands.get_nowait()
            except queue.Empty:
                return
            self._handle(message)

    def _handle(self, message: dict[str, Any]) -> None:
        command = message.get("cmd")
        try:
            if command == Command.PLAY:
                self._cmd_play(message)
            elif command == Command.SEEK:
                self._cmd_seek(message)
            elif command == Command.STOP:
                pass  # handled by the fast path
            elif command == Command.EXEC:
                self._cmd_exec(message)
            elif command == Command.PING:
                self.emit(Event.PONG, t=time.time())
            elif command in (Command.LOAD, Command.RELOAD):
                self.stop_event.clear()
                path = message.get("path")
                self.load(Path(path) if path else None)
                self.emit_status("idle", index=0)
            elif command == Command.QUIT:
                self.quit_event.set()
            else:
                self.emit(
                    Event.ERROR,
                    message=f"unknown command {command!r}",
                    traceback="",
                    line=None,
                    scene_file=None,
                    context="protocol",
                )
        except Exception as exc:  # keep the host alive on user errors
            self.emit_error(exc, context=f"command {command}")
            self.emit_status("error")

    def _cmd_play(self, message: dict[str, Any]) -> None:
        if not self.blocks:
            self.emit_status("idle", note="nothing to play")
            return
        start = int(message.get("from") or 0)
        start = max(0, min(start, len(self.blocks) - 1))
        self.stop_event.clear()
        scene = self._make_scene(skip_animations=False, start=start)
        self._activate_scene(scene)
        self.emit_status("playing", index=start)
        self.tick.reset()
        error: BaseException | None = None
        try:
            run_construct(scene)
        except Exception as exc:  # reported to the editor
            error = exc
        stopped = self.stop_event.is_set()
        self.stop_event.clear()
        if error is not None:
            self.emit_error(error, context="playback")
            self.emit_status("error")
            return
        index = min(int(scene.num_plays), len(self.blocks) - 1)
        self.emit(
            Event.STOPPED,
            time=round(float(scene.time), 4),
            index=index,
            user_stopped=stopped,
        )
        self.emit_status("idle")
        self._render_current_frame()
        self.push_current_frame()

    def _cmd_seek(self, message: dict[str, Any]) -> None:
        index = int(message.get("index", -1))
        if not 0 <= index < len(self.blocks):
            self.emit(
                Event.ERROR,
                message=f"seek index {index} out of range (0..{len(self.blocks) - 1})",
                traceback="",
                line=None,
                scene_file=None,
                context="seek",
            )
            return
        self.stop_event.clear()
        scene = self._make_scene(skip_animations=True, start=index, end=index + 1)
        self._activate_scene(scene)
        try:
            run_construct(scene)
        except Exception as exc:  # reported to the editor
            self.emit_error(exc, context=f"seek {index}")
            self.emit_status("error")
            return
        self._render_current_frame()
        self.push_current_frame()
        block = self.blocks[index]
        self.emit(Event.TICK, time=block.end, index=index)
        self.emit_status("idle", index=index)

    def _cmd_exec(self, message: dict[str, Any]) -> None:
        code = str(message.get("code", ""))
        result = self._run_shell_cell(code)
        self.emit(Event.EXEC_RESULT, **result)
        if self.scene is not None and not self.stop_event.is_set():
            with contextlib.suppress(Exception):
                self._render_current_frame()
                self.push_current_frame()

    # ------------------------------------------------------------------
    # Embedded live shell
    # ------------------------------------------------------------------

    def _scene_shortcuts(self) -> dict[str, Any]:
        scene = self.scene
        if scene is None:
            return {}
        return {
            "play": scene.play,
            "wait": scene.wait,
            "add": scene.add,
            "remove": scene.remove,
            "clear": scene.clear,
            "save_state": scene.save_state,
            "undo": scene.undo,
            "redo": scene.redo,
            "scene": scene,
            "self": scene,
            "reload": self._shell_reload,
            "checkpoint_paste": self._checkpoint_paste,
            "clear_checkpoints": self._clear_checkpoints,
        }

    def _checkpoint_paste(
        self,
        code: str | None = None,
        *,
        skip: bool = False,
        record: bool = False,
        progress_bar: bool = True,
    ) -> None:
        """Run a code block with manimgl checkpoint semantics.

        A leading ``# comment`` marks a checkpoint: the first call snapshots the
        scene, later calls with the same comment restore that snapshot first.  With
        no ``code`` argument the clipboard is used, matching manimgl's embed flow.
        """
        from manimlib.scene.scene_embed import CheckpointManager

        if self._checkpoints is None:
            self._checkpoints = CheckpointManager()
        if self.shell is None:
            self.shell = self._make_shell()
        if code is not None:
            import pyperclip

            pyperclip.copy(code)
        with self.scene.temp_config_change(skip, record, progress_bar):
            self._checkpoints.checkpoint_paste(self.shell, self.scene)

    def _clear_checkpoints(self) -> None:
        if self._checkpoints is not None:
            self._checkpoints.clear_checkpoints()

    def _shell_reload(self, *args: Any, **kwargs: Any) -> None:
        self.load()

    def _make_shell(self) -> Any:
        from IPython.core.interactiveshell import InteractiveShell

        shell = InteractiveShell.instance()
        namespace: dict[str, Any] = {}
        if self.module is not None:
            namespace.update(vars(self.module))
        namespace.update(self._scene_shortcuts())
        with contextlib.suppress(Exception):
            exec("from manimlib import *", namespace)  # intentional shell namespace
        shell.user_ns = namespace
        return shell

    def _refresh_shell_namespace(self) -> None:
        if self.shell is None:
            return
        if self.module is not None:
            self.shell.user_ns.update(vars(self.module))
        self.shell.user_ns.update(self._scene_shortcuts())

    def _run_shell_cell(self, code: str) -> dict[str, Any]:
        if self.shell is None:
            self.shell = self._make_shell()
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            result = self.shell.run_cell(code, store_history=False)
        ok = bool(getattr(result, "success", True))
        error: str | None = None
        if not ok:
            failure = getattr(result, "error_in_exec", None)
            error = (
                f"{type(failure).__name__}: {failure}"
                if failure is not None
                else "execution failed"
            )
        value = getattr(result, "result", None)
        return {
            "ok": ok,
            "stdout": stdout.getvalue(),
            "stderr": stderr.getvalue(),
            "result": None if value is None else repr(value),
            "error": error,
        }

    # ------------------------------------------------------------------
    # Main loop
    # ------------------------------------------------------------------

    def run(self) -> int:
        try:
            if self.config.port is not None:
                self._start_control()
                if self.config.embedded:
                    self._start_frames()
            self._create_window()
            self.load()
            if self.config.port is None:
                self._cmd_play({})
        except Exception as exc:  # startup failures reported over IPC
            self.emit_error(exc, context="startup")
            if self.config.fail_fast or self.config.port is None:
                self._shutdown()
                return 1
        self._idle_loop()
        self._shutdown()
        return 0

    def _idle_loop(self) -> None:
        while not self.quit_event.is_set():
            self._drain_commands()
            if self.window is not None and getattr(self.window, "is_closing", False):
                break
            if self.window is not None and not self.config.embedded:
                with contextlib.suppress(Exception):
                    self.window._window.dispatch_events()
            time.sleep(0.004)

    def _shutdown(self) -> None:
        if self.frame_pump is not None:
            self.frame_pump.close()
            self.frame_pump = None
        if self.control is not None:
            with contextlib.suppress(Exception):
                self.control.send({"event": Event.STATUS, "state": "closed"})
            self.control.close()
            self.control = None
        if self.control_sock is not None:
            with contextlib.suppress(OSError):
                self.control_sock.close()
            self.control_sock = None
        if self.window is not None:
            with contextlib.suppress(Exception):
                self.window.destroy()
            self.window = None


def parse_size(text: str) -> tuple[int, int]:
    """Parse a ``WxH`` string into a validated tuple."""
    try:
        width_text, height_text = text.lower().split("x", 1)
        width, height = int(width_text), int(height_text)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"invalid size {text!r}; expected WxH") from exc
    if width < 64 or height < 64 or width > 4096 or height > 4096:
        raise argparse.ArgumentTypeError(f"size {text!r} out of range (64..4096)")
    return width, height


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="manim-editor-preview",
        description="Realtime ManimGL preview host (owns the GL context).",
    )
    parser.add_argument("scene", type=Path, help="scene file to preview")
    parser.add_argument("--scene-name", dest="scene_name", default=None, help="scene class name")
    parser.add_argument("--port", type=int, default=None, help="editor control port (loopback)")
    parser.add_argument(
        "--embedded",
        action="store_true",
        help="hide the GL window and stream frames to the editor",
    )
    parser.add_argument("--size", type=parse_size, default=DEFAULT_SIZE, help="frame size WxH")
    parser.add_argument(
        "--initial-frame",
        choices=("start", "end"),
        default="start",
        help="state shown right after loading",
    )
    parser.add_argument("--fps", type=int, default=None, help="camera fps override")
    parser.add_argument(
        "--fail-fast",
        action="store_true",
        help="exit when startup fails instead of waiting for a reload",
    )
    parser.add_argument("--log-level", default="INFO")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=getattr(logging, str(args.log_level).upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,
    )
    config = HostConfig(
        scene_path=args.scene,
        scene_name=args.scene_name,
        port=args.port,
        embedded=args.embedded,
        size=args.size,
        initial_frame=args.initial_frame,
        fps=args.fps,
        fail_fast=args.fail_fast,
    )
    # manimgl parses sys.argv at import time; hide our arguments from it.
    from manim_editor.scene_runner import manimlib_argv_guard

    with manimlib_argv_guard():
        return SceneHost(config).run()


if __name__ == "__main__":
    raise SystemExit(main())
