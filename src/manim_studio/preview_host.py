"""Realtime preview host — the only process in the system that owns a GL context.

The host is spawned by the editor (or run standalone) and speaks the versioned
protocol from :mod:`manim_studio.ipc`:

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
from typing import TYPE_CHECKING, Any

from manim_studio import ipc
from manim_studio.filmstrip import Filmstrip
from manim_studio.ipc import (
    Channel,
    Command,
    Event,
    JsonLineReader,
    JsonLineWriter,
    encode_message,
    hello_message,
)
from manim_studio.patch_runtime import StudioRuntime
from manim_studio.patches import Patch, PatchedSites, PatchError, apply_patches, parse_patch
from manim_studio.reuse import NO_REUSE, ReusePlan, RunSnapshot, plan_reuse
from manim_studio.scene_runner import (
    SceneLoadError,
    check_scene_file,
    format_scene_error,
    load_scene_module,
    make_scene,
    manimlib_argv_guard,
    run_construct,
    select_scene_class,
    user_module_signature,
)
from manim_studio.scrub import (
    FilmstripCrawler,
    ScrubEngine,
    ScrubError,
    TimingScaler,
    read_upright_rgba,
)
from manim_studio.stats import TimingStats, process_rss_bytes
from manim_studio.timeline_model import TimelineBlock
from manim_studio.tracer import BlockSite, ClipTrace, TraceEvent, TraceRecorder

__all__ = ["FramePump", "HostConfig", "SceneHost", "TickThrottler", "main", "parse_size"]

if TYPE_CHECKING:
    from collections.abc import Callable

log = logging.getLogger("manim_studio.preview_host")

CONNECT_TIMEOUT_S = 15.0
TICK_INTERVAL_S = 0.05
DEFAULT_SIZE = (1280, 720)


def _silence_known_noise() -> None:
    """Hide known-benign import noise so the editor's Live shell stays readable.

    The filters are intentionally narrow: manimgl's pinned ``pkg_resources`` usage,
    PyOpenGL's missing ``OpenGL_accelerate`` info line, and pydub's escape-sequence
    SyntaxWarning are all unavoidable third-party artifacts, not signals.
    """
    import warnings

    warnings.filterwarnings(
        "ignore", message=r"pkg_resources is deprecated as an API.*", category=UserWarning
    )
    warnings.filterwarnings(
        "ignore", message=r"invalid escape sequence.*", category=SyntaxWarning, module=r"pydub.*"
    )
    logging.getLogger("OpenGL.acceleratesupport").setLevel(logging.WARNING)


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
    """Latest-frame-wins streamer.

    Callers hand over frames either raw (``offer``) or already encoded
    (``offer_encoded``).  Encoding and sending both happen on the pump thread, so
    the GL thread never blocks on compression.  A raw frame identical to the last
    one sent is dropped without re-encoding.  When the transport cannot keep up,
    frames are dropped instead of queued.  With ``timings`` set, encoding and
    sending are recorded as the ``encode``/``send`` phases.
    """

    def __init__(
        self,
        sock: socket.socket,
        *,
        level: int = 1,
        timings: TimingStats | None = None,
    ) -> None:
        self._sock = sock
        self.level = level
        self.timings = timings
        self.frames_sent = 0
        self.frames_dropped = 0
        self.frames_deduped = 0
        self._cond = threading.Condition()
        self._slot: bytes | tuple[bytes, int, int] | None = None
        self._running = True
        self._started = False
        self._last_raw: bytes | None = None
        self._thread = threading.Thread(target=self._loop, name="frame-pump", daemon=True)

    def start(self) -> None:
        self._started = True
        self._thread.start()

    def offer(self, rgba: bytes, width: int, height: int) -> None:
        """Queue raw upright RGBA; it is encoded on the pump thread (latest wins)."""
        with self._cond:
            if not self._running:
                return
            if self._slot is not None:
                self.frames_dropped += 1
            self._slot = (rgba, width, height)
            self._cond.notify()

    def offer_encoded(self, blob: bytes) -> None:
        """Queue a complete encoded frame payload (latest wins)."""
        with self._cond:
            if not self._running:
                return
            if self._slot is not None:
                self.frames_dropped += 1
            self._slot = blob
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
            if isinstance(item, tuple):
                rgba, width, height = item
                # Skip a frame identical to the last one actually sent: during a
                # wait or a static hold the GL loop re-renders the same pixels on
                # every tick, and re-encoding/sending them is pure waste.  The
                # comparison runs here on the pump thread, never on the GL thread.
                if self._last_raw is not None and rgba == self._last_raw:
                    with self._cond:
                        self.frames_deduped += 1
                    continue
                try:
                    if self.timings is None:
                        blob = ipc.encode_frame(width, height, rgba, level=self.level)
                    else:
                        with self.timings.phase("encode"):
                            blob = ipc.encode_frame(width, height, rgba, level=self.level)
                except (ipc.ProtocolError, ValueError):
                    continue  # a bad frame must not kill the pump thread
                raw = rgba
            else:
                blob = item
                raw = None
            try:
                if self.timings is None:
                    self._sock.sendall(blob)
                else:
                    with self.timings.phase("send"):
                        self._sock.sendall(blob)
            except OSError:
                with self._cond:
                    self._running = False
                return
            if raw is None:
                # An encoded frame's pixels are not available for comparison, so
                # forget the raw baseline; the next raw frame must be sent.
                self._last_raw = None
            else:
                self._last_raw = raw
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
    initial_frame: str = "end"
    fps: int | None = None
    fail_fast: bool = False
    #: Timeline edits the very first load runs with (the editor re-sends later ones).
    patches: tuple[Patch, ...] = ()


class SceneHost:
    """Owns the window, the scene lifecycle, playback and the IPC connections."""

    def __init__(self, config: HostConfig, logger: logging.Logger = log) -> None:
        self.config = config
        self.log = logger
        self.stop_event = threading.Event()
        self.quit_event = threading.Event()
        self.commands: queue.Queue[dict[str, Any]] = queue.Queue()
        #: Bumped on every STOP/QUIT so a play queued *before* a stop can tell it
        #: was superseded (a loop replay racing a pause must not resurrect audio).
        self._stop_gen = 0
        self.window: Any = None
        self.module: Any = None
        self.scene: Any = None
        self.scene_class: type | None = None
        self.blocks: tuple[TimelineBlock, ...] = ()
        self.stats: dict[str, int] = {}
        self.events: tuple[TraceEvent, ...] = ()
        self.control_sock: socket.socket | None = None
        self.call_lines: tuple[int | None, ...] = ()
        #: Timeline edits (sidecar patches) the loaded scene runs with.
        self._patches: tuple[Patch, ...] = config.patches
        self._studio_runtime = StudioRuntime()
        self._patch_report: dict[str, Any] = {"applied": 0, "orphaned": []}
        self.block_sites: tuple[BlockSite | None, ...] = ()
        self.block_clips: tuple[tuple[ClipTrace, ...], ...] = ()
        self.control: JsonLineWriter | None = None
        self.frame_pump: FramePump | None = None
        self.tick = TickThrottler()
        self.timings = TimingStats()
        self.shell: Any = None
        self._checkpoints: Any = None
        self._loaded_once = False
        self._shutdown_started = False
        self._scrub_engine: ScrubEngine | None = None
        self._pending_scrub: float | None = None
        self._filmstrip: Filmstrip | None = None
        self._crawler: FilmstripCrawler | None = None
        self._crawler_engine: ScrubEngine | None = None
        self._crawl_disabled = False
        #: The executed run the current filmstrip belongs to, and which of its blocks the
        #: filmstrip holds before the crawler exists (carried over from the last reload).
        self._run: RunSnapshot | None = None
        self._reused_blocks: frozenset[int] = frozenset()
        self._fresh_next = False  # next load must not reuse cached frames
        self._trace_id = 0
        self._fps = 30
        # Where the editor's playhead is; the crawler fills the filmstrip
        # nearest-first from here rather than always from the start.
        self._playhead_hint = 0.0

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
                    self._stop_gen += 1
                    self.stop_event.set()
                if command == Command.QUIT:
                    self.quit_event.set()
                    break
                # Stamp the stop generation at enqueue time: a later STOP makes
                # this command stale, so a play cannot clear the pause state.
                message["_stop_gen"] = self._stop_gen
                self.commands.put(message)
        except ipc.ProtocolError as exc:
            self.log.warning("control protocol error: %s", exc)
        finally:
            # The editor went away; there is nobody left to serve.
            self.stop_event.set()
            self.quit_event.set()
            self._arm_watchdog()

    def _arm_watchdog(self) -> None:
        """Guarantee the host dies with its editor even if the GL thread is stuck."""
        timer = threading.Timer(3.0, self._watchdog_exit)
        timer.daemon = True
        timer.start()

    def _watchdog_exit(self) -> None:
        if not self._shutdown_started:
            self.log.warning("editor connection lost; forcing process exit")
            os._exit(0)

    def _start_frames(self) -> None:
        sock = self._connect(Channel.FRAMES)
        sock.sendall(encode_message(hello_message(Channel.FRAMES, role="host")))
        self.frame_pump = FramePump(sock, timings=self.timings)
        self.frame_pump.start()

    # ------------------------------------------------------------------
    # Window + scene lifecycle
    # ------------------------------------------------------------------

    def _create_window(self) -> None:
        # manimgl parses sys.argv on first import; embedding the host in a process
        # with its own CLI arguments (tests, tools) must not feed it those.
        with manimlib_argv_guard():
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
        scene_class: type | None = None,
    ) -> Any:
        target_class = scene_class if scene_class is not None else self.scene_class
        if target_class is None:
            raise SceneLoadError("no scene loaded")
        self._bind_default_framebuffer()
        scene = make_scene(
            target_class,
            window=self.window,
            resolution=self.config.size,
            fps=self.config.fps,
            skip_animations=skip_animations,
            start_at_animation_number=start,
            end_at_animation_number=end,
            preview_while_skipping=False,
        )
        self._neutralize_embed(scene)
        return scene

    def _neutralize_embed(self, scene: Any) -> None:
        """Stop ``scene.embed()`` from launching a terminal shell inside the host.

        manimgl's embed opens a blocking ``InteractiveShellEmbed`` on stdin — in a
        background process that wedges the preview forever (the editor never writes
        to the host's stdin).  The editor's Live shell provides the same experience,
        so ``embed()`` becomes a logged no-op for every scene, tracing included.
        """

        def embed(*_args: Any, **_kwargs: Any) -> None:
            self.log.info("scene.embed() ignored in the live preview; use the editor's Live shell")
            self.emit_status("idle", note="embed() ignored — use the Live shell")

        with contextlib.suppress(Exception):
            scene.embed = embed

    def _bind_default_framebuffer(self) -> None:
        """Bind the window's default framebuffer before constructing a scene.

        manimgl's ``Camera.init_fbo`` captures ``ctx.detect_framebuffer()`` as its
        ``window_fbo``.  If an old offscreen FBO is still bound at that moment (which
        is exactly what happens on reload), the new scene's ``window_fbo`` aliases the
        old scene's FBO; releasing the old scene then invalidates it and every blit
        fails with ``GL_INVALID_OPERATION``.  Binding the default framebuffer first
        keeps every scene's ``window_fbo`` at 0.
        """
        if self.window is None:
            return
        screen = getattr(getattr(self.window, "ctx", None), "screen", None)
        if screen is None:
            return
        with contextlib.suppress(Exception):
            screen.use()

    def load(self, path: str | Path | None = None, *, cause: str = "load") -> None:
        """(Re)load the scene atomically: import, trace, build, then commit.

        ``cause`` is echoed in the ``log`` event (``"patches"`` for a reload that
        only applies timeline edits) so the editor can keep the user's zoom and
        playhead instead of treating it as a fresh scene.

        Nothing in the live host changes until the user's scene has been imported,
        traced and successfully constructed.  A broken edit therefore leaves the
        previous scene, timeline and filmstrip fully intact — and, crucially, never
        leaves a new scene class paired with a stale block list for the crawler to
        trip over.
        """
        if path is not None:
            self.config.scene_path = Path(path)
        scene_path = self.config.scene_path
        load_started = time.perf_counter()
        phases: dict[str, float] = {}

        def lap(name: str, since: float) -> float:
            now = time.perf_counter()
            phases[name] = round(now - since, 4)
            return now

        mark = load_started
        check = check_scene_file(scene_path)
        if not check.ok:
            raise SceneLoadError(f"{scene_path}: {check.error}")
        mark = lap("check", mark)

        # Timeline edits run as patched in-memory source; the file on disk is untouched.
        patch_result = apply_patches(scene_path.read_text(encoding="utf-8"), self._patches)
        patched = patch_result.changed
        mark = lap("patch", mark)
        module = load_scene_module(
            scene_path,
            is_reload=self._loaded_once,
            source=patch_result.text if patched else None,
            runtime=self._studio_runtime if patched else None,
        )
        scene_class = select_scene_class(module, self.config.scene_name, path=scene_path)
        mark = lap("import", mark)
        user_modules = user_module_signature(scene_path)
        mark = lap("signature", mark)

        # ---- staged (failure-prone) phase: live state untouched -----------------
        # When the preview opens on the end state, the traced scene *is* that scene: reuse
        # it instead of running construct() a second time.
        snapshot, trace_seconds, traced_scene = self._trace_scene_class(
            scene_class,
            scene_path,
            self._site_resolver(scene_path, patch_result.patched_sites()),
            keep_scene=self.config.initial_frame == "end",
        )
        mark = lap("trace", mark)
        initial_scene = (
            traced_scene if traced_scene is not None else self._build_initial_scene(scene_class)
        )
        mark = lap("initial_scene", mark)

        # ---- commit -------------------------------------------------------------
        self.module = module
        self.scene_class = scene_class
        self._loaded_once = True
        self.blocks = snapshot.blocks
        self.call_lines = self._scene_call_lines(snapshot.call_sites, scene_path)
        self.block_sites = snapshot.block_sites
        self.block_clips = snapshot.block_clips
        self._patch_report = {
            "applied": len(patch_result.applied),
            "orphaned": [
                {"patch": orphan.patch.to_dict(), "reason": orphan.reason}
                for orphan in patch_result.orphaned
            ],
        }
        self.stats = snapshot.stats
        self.events = snapshot.events
        self._fps = int(getattr(initial_scene.camera, "fps", 30)) or 30
        self._trace_id += 1
        new_run = RunSnapshot(
            text=patch_result.text,
            scene_name=scene_class.__name__,
            blocks=snapshot.blocks,
            call_lines=self.call_lines,
            sites=snapshot.block_sites,
            signature=(tuple(self.config.size), self.config.fps, user_modules),
        )
        old_cached = self._crawler.cached_blocks if self._crawler else self._reused_blocks
        old_strip = self._filmstrip
        plan = NO_REUSE if self._fresh_next else plan_reuse(self._run, new_run, old_cached)
        self._fresh_next = False
        self._dispose_scrub_engine()
        self._dispose_filmstrip()
        self._filmstrip, self._reused_blocks = self._carry_filmstrip(old_strip, plan, new_run)
        self._run = new_run
        reuse_report = {
            "blocks": len(self._reused_blocks),
            "total": len(self.blocks),
            "frames": self._filmstrip.frames,
            "reason": plan.reason,
        }
        self._crawl_disabled = False
        phases["commit"] = round(time.perf_counter() - mark, 4)
        phases["total"] = round(time.perf_counter() - load_started, 4)
        self.log.info("loaded %s (%s) %s", scene_class.__name__, scene_path, phases)

        total = self.blocks[-1].end if self.blocks else 0.0
        self._playhead_hint = total if self.config.initial_frame == "end" else 0.0
        self.emit(
            Event.LOG,
            blocks=[block.to_dict() for block in self.blocks],
            stats=self.stats,
            events=[event.to_dict() for event in self.events],
            total_duration=total,
            trace_seconds=trace_seconds,
            scene=str(scene_path),
            scene_name=scene_class.__name__,
            trace_id=self._trace_id,
            fps=self._fps,
            call_sites=list(self.call_lines),
            sites=[None if site is None else site.to_dict() for site in self.block_sites],
            patches=self._patch_report,
            cause=cause,
            clips=[[clip.to_dict() for clip in clips] for clips in self.block_clips],
            load_timings=phases,
            reuse=reuse_report,
        )
        self._activate_scene(initial_scene)
        self.stop_event.clear()
        self._render_current_frame()
        self.push_current_frame()
        self.emit_status("idle", index=0)
        self._refresh_shell_namespace()

    def _carry_filmstrip(
        self, old: Filmstrip | None, plan: ReusePlan, run: RunSnapshot
    ) -> tuple[Filmstrip, frozenset[int]]:
        """Keep the frames of ``plan``'s blocks in the old filmstrip, else start empty.

        A block owns the frame indices ``[round(start*fps), round(end*fps))``; its last
        boundary frame is re-rendered as the next block's first.  Blocks whose frames
        were partly evicted from the LRU are dropped from the plan.
        """
        if old is None or not plan.blocks:
            return Filmstrip(), frozenset()
        fps = self._fps
        ranges: dict[int, tuple[int, int]] = {}
        for index in plan.blocks:
            block = run.blocks[index]
            start = round(block.start * fps)
            stop = max(round(block.end * fps), start + 1)
            if old.has_range(start, stop):
                ranges[index] = (start, stop)
        old.retain(ranges.values())
        return old, frozenset(ranges)

    @staticmethod
    def _site_resolver(
        scene_path: Path, sites: PatchedSites
    ) -> Callable[[str, int, int], str | None]:
        """Tracer hook: ``(file, line, col)`` of a call -> original call-site id.

        Only calls in the scene file resolve; helper modules return ``None``.
        """
        try:
            target = scene_path.resolve()
        except OSError:  # pragma: no cover - defensive
            target = scene_path
        in_scene: dict[str, bool] = {}

        def resolve(filename: str, line: int, col: int) -> str | None:
            match = in_scene.get(filename)
            if match is None:
                try:
                    match = Path(filename).resolve() == target
                except OSError:  # pragma: no cover - defensive
                    match = False
                in_scene[filename] = match
            return sites.resolve(line, col) if match else None

        return resolve

    def _trace_scene_class(
        self,
        scene_class: type,
        scene_path: Path,
        site_resolver: Callable[[str, int, int], str | None] | None = None,
        *,
        keep_scene: bool = False,
    ) -> tuple[Any, float, Any | None]:
        """Run the scene once in skip mode: ``(snapshot, wall seconds, scene or None)``.

        With ``keep_scene`` the finished scene is returned (tracer detached) instead of
        released, so the caller can show its end state without running ``construct()`` a
        second time.
        """
        scene = self._make_scene(skip_animations=True, scene_class=scene_class)
        recorder = TraceRecorder(record_events=True, site_resolver=site_resolver)
        recorder.attach(scene)
        started = time.perf_counter()
        keep = False
        try:
            run_construct(scene)
            snapshot = recorder.snapshot()
            elapsed = time.perf_counter() - started
            keep = keep_scene
        finally:
            recorder.detach()
            if not keep:
                self._release_scene(scene)
        total = snapshot.blocks[-1].end if snapshot.blocks else 0.0
        self.log.info(
            "trace: %d blocks, %.3fs, %.2fs wall",
            len(snapshot.blocks),
            total,
            elapsed,
        )
        return snapshot, elapsed, scene if keep else None

    @staticmethod
    def _scene_call_lines(
        call_sites: tuple[tuple[str, int] | None, ...], scene_path: Path
    ) -> tuple[int | None, ...]:
        """Per-block line numbers that live in ``scene_path`` (else ``None``).

        Blocks recorded inside imported helper modules are dropped so the editor
        only ever jumps within the scene file it already has open.
        """
        try:
            target = scene_path.resolve()
        except OSError:  # pragma: no cover - defensive
            target = scene_path
        lines: list[int | None] = []
        for site in call_sites:
            if site is None:
                lines.append(None)
                continue
            filename, line = site
            try:
                matches = Path(filename).resolve() == target
            except OSError:  # pragma: no cover - defensive
                matches = False
            lines.append(line if matches else None)
        return tuple(lines)

    def _build_initial_scene(self, scene_class: type) -> Any:
        """Create and *run* the scene that shows the initial state.

        Running ``construct()`` here (before anything is committed) is what makes
        a broken scene abort the load instead of replacing the working preview.
        """
        if self.config.initial_frame == "end":
            scene = self._make_scene(skip_animations=True, scene_class=scene_class)
        else:
            scene = self._make_scene(skip_animations=True, end=0, scene_class=scene_class)
        run_construct(scene)
        return scene

    def _activate_scene(self, scene: Any) -> None:
        self._dispose_current_scene()
        self.scene = scene
        self._install_hooks(scene)
        if self.config.embedded:
            # Read back from the offscreen buffer instead of the hidden window.
            scene.camera.use_window_fbo(False)

    def _install_hooks(self, scene: Any) -> None:
        from manimlib.scene.scene import EndScene

        # Tracks whether the most recent update_frame actually drew.  emit_frame()
        # is called after every play/wait step, including in skip-animations mode
        # where update_frame returns before rendering; pushing those would stream
        # blank (never-drawn) frames.
        drew = {"value": False}

        original_update = scene.update_frame

        def update_frame(dt: float = 0, force_draw: bool = False) -> Any:
            if self.stop_event.is_set():
                raise EndScene()
            with self.timings.phase("render"):
                result = original_update(dt, force_draw)
            drew["value"] = bool(force_draw) or not bool(getattr(scene, "skip_animations", False))
            return result

        scene.update_frame = update_frame

        original_emit = scene.emit_frame

        def emit_frame() -> Any:
            result = original_emit()
            if drew["value"]:
                self._on_rendered_frame(scene)
            return result

        scene.emit_frame = emit_frame

    def _dispose_current_scene(self) -> None:
        if self.scene is not None:
            self._release_scene(self.scene)
            self.scene = None

    def _dispose_scrub_engine(self) -> None:
        if self._scrub_engine is not None:
            self._release_scene(self._scrub_engine.scene)
            self._scrub_engine = None
            self._pending_scrub = None

    def _dispose_filmstrip(self) -> None:
        """Drop the cache and the crawler that fills it (reload/shutdown)."""
        if self._crawler_engine is not None:
            self._release_scene(self._crawler_engine.scene)
        self._crawler = None
        self._crawler_engine = None
        self._filmstrip = None

    def _ensure_crawler(self) -> None:
        """Lazily start filling the filmstrip in the background."""
        if (
            self._filmstrip is None
            or self._crawler is not None
            or self._crawl_disabled
            or not self.blocks
            or not self.config.embedded
            or self.frame_pump is None
        ):
            return
        scene = self._make_scene(skip_animations=True)
        scene.camera.use_window_fbo(False)
        engine = ScrubEngine(scene, self.blocks)
        self._crawler_engine = engine
        self._crawler = FilmstripCrawler(
            engine,
            self._filmstrip,
            self._fps,
            on_progress=self._on_crawl_progress,
            timings=self.timings,
            start_time=self._playhead_hint,
            cached_blocks=self._reused_blocks,
        )
        self.log.info(
            "filmstrip: crawl started (%d blocks, %d reused)",
            len(self.blocks),
            len(self._reused_blocks),
        )

    def _step_crawler(self) -> None:
        """Advance the crawl, never letting user-code errors kill the host."""
        if self._crawler is None or self._crawler.done:
            return
        try:
            self._crawler.step()
        except Exception as exc:  # the host must survive anything
            cause: BaseException = exc.__cause__ if exc.__cause__ is not None else exc
            self.emit_error(cause, context="filmstrip")
            self._crawl_disabled = True
            self._crawler = None
            if self._crawler_engine is not None:
                self._release_scene(self._crawler_engine.scene)
                self._crawler_engine = None

    def _on_crawl_progress(self, frames: int, done: bool, capped: bool) -> None:
        self.emit(Event.FILMSTRIP, **self._filmstrip_payload(frames, done, capped))
        if done:
            self.log.info("filmstrip: %d frames ready (capped=%s)", frames, capped)

    def _filmstrip_payload(self, frames: int, done: bool, capped: bool) -> dict[str, Any]:
        """Filmstrip progress plus RAM accounting for the editor's stats readout."""
        strip = self._filmstrip
        pump = self.frame_pump
        return {
            "trace_id": self._trace_id,
            "frames": frames,
            "done": done,
            "capped": capped,
            "fps": self._fps,
            "bytes": strip.byte_size if strip is not None else 0,
            "max_bytes": strip.max_bytes if strip is not None else 0,
            "max_frames": strip.max_frames if strip is not None else 0,
            "host_rss": process_rss_bytes(),
            "sent": pump.frames_sent if pump is not None else 0,
            "dropped": pump.frames_dropped if pump is not None else 0,
            "deduped": pump.frames_deduped if pump is not None else 0,
            "time_to_playhead": (
                self._crawler.seconds_to_playhead if self._crawler is not None else None
            ),
            "timings": self.timings.snapshot(),
        }

    def _stats_payload(self) -> dict[str, Any]:
        """Full on-demand snapshot for the editor's App stats dialog."""
        strip = self._filmstrip
        pump = self.frame_pump
        total = self.blocks[-1].end if self.blocks else 0.0
        return {
            "trace_id": self._trace_id,
            "frames": strip.frames if strip is not None else 0,
            "bytes": strip.byte_size if strip is not None else 0,
            "max_bytes": strip.max_bytes if strip is not None else 0,
            "max_frames": strip.max_frames if strip is not None else 0,
            "host_rss": process_rss_bytes(),
            "sent": pump.frames_sent if pump is not None else 0,
            "dropped": pump.frames_dropped if pump is not None else 0,
            "deduped": pump.frames_deduped if pump is not None else 0,
            "fps": self._fps,
            "blocks": len(self.blocks),
            "total_duration": total,
            "size": list(self.config.size),
            "scene_name": self.scene_class.__name__ if self.scene_class is not None else None,
            "timings": self.timings.snapshot(),
        }

    def _block_at(self, time_seconds: float) -> TimelineBlock | None:
        if not self.blocks:
            return None
        for block in self.blocks:
            if block.start <= time_seconds < block.end:
                return block
        return self.blocks[0] if time_seconds < self.blocks[0].start else self.blocks[-1]

    # ------------------------------------------------------------------
    # Timeline patches
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_patches(raw: Any) -> tuple[Patch, ...]:
        if not isinstance(raw, list):
            raise PatchError(f"'patches' must be a list, got {type(raw).__name__}")
        return tuple(parse_patch(item) for item in raw)

    def _load_with_patches(
        self, patches: tuple[Patch, ...] | None, path: Path | None, *, cause: str = "load"
    ) -> None:
        """Load with a new patch set, restoring the old one if the load fails."""
        previous = self._patches
        if patches is not None:
            self._patches = patches
        try:
            self.load(path, cause=cause)
        except BaseException:
            self._patches = previous
            raise

    def _cmd_set_patches(self, message: dict[str, Any]) -> None:
        """Apply a new patch set: reload the patched scene and re-pose the playhead."""
        patches = self._parse_patches(message.get("patches") or [])
        self.stop_event.clear()
        self._load_with_patches(patches, None, cause="patches")
        total = self.blocks[-1].end if self.blocks else 0.0
        self._pending_scrub = max(0.0, min(float(message.get("time") or 0.0), total))
        self._playhead_hint = self._pending_scrub
        self.emit_status("idle", index=0)

    @staticmethod
    def _release_scene(scene: Any) -> None:
        camera = getattr(scene, "camera", None)
        if camera is None:
            return
        window_fbo = getattr(camera, "window_fbo", None)
        for attribute in ("fbo_for_files", "draw_fbo"):
            fbo = getattr(camera, attribute, None)
            # Never release the window framebuffer, even if a scene aliased it.
            if fbo is None or fbo is window_fbo:
                continue
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

    def push_current_frame(self, scene: Any | None = None) -> None:
        """Read back the current offscreen frame and hand it to the pump.

        Frames are flipped to upright RGBA so the editor can build a ``QImage``
        without per-pixel work.
        """
        target = scene if scene is not None else self.scene
        if self.frame_pump is None or target is None:
            return
        import numpy as np

        camera = target.camera
        width, height = (int(v) for v in camera.default_pixel_shape)
        with self.timings.phase("readback"):
            raw = camera.get_raw_fbo_data()
            array = np.frombuffer(raw, dtype=np.uint8).reshape(height, width, 4)[::-1]
            upright = array.tobytes()
        self.log.debug(
            "push %dx%d fbo=%s head=%s",
            width,
            height,
            getattr(camera.fbo, "glo", "?"),
            raw[:12].hex(),
        )
        with self.timings.phase("handoff"):
            self.frame_pump.offer(upright, width, height)

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
            elif command == Command.SCRUB:
                # Scrubbing arrives at pointer rate; keep only the newest request
                # and let the idle loop execute it (replay + render coalescing).
                self._pending_scrub = max(0.0, float(message.get("time") or 0.0))
            elif command == Command.STOP:
                pass  # handled by the fast path
            elif command == Command.EXEC:
                self._cmd_exec(message)
            elif command == Command.PING:
                self.emit(Event.PONG, t=time.time())
            elif command == Command.STATS:
                self.emit(Event.STATS, **self._stats_payload())
            elif command == Command.SET_PATCHES:
                self._cmd_set_patches(message)
            elif command in (Command.LOAD, Command.RELOAD):
                self.stop_event.clear()
                path = message.get("path")
                scene_name = message.get("scene_name")
                if scene_name:
                    self.config.scene_name = str(scene_name)
                # ``patches`` is authoritative when present; absent keeps what we had.
                raw_patches = message.get("patches")
                patches = None if raw_patches is None else self._parse_patches(raw_patches)
                self._fresh_next = bool(message.get("fresh"))
                self._load_with_patches(patches, Path(path) if path else None)
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
        if message.get("_stop_gen") != self._stop_gen:
            # A STOP arrived after this play was queued (a loop replay racing a
            # user pause): honour the stop instead of starting another cycle.
            self.emit_status("idle")
            return
        start = int(message.get("from") or 0)
        start = max(0, min(start, len(self.blocks) - 1))
        # ``to`` (inclusive block index) bounds playback to a range for looping.
        to = message.get("to")
        end = None
        if to is not None:
            end = max(start, min(int(to), len(self.blocks) - 1)) + 1
        # ``rate`` is a review-speed multiplier (0.25x..2x); 2x plays twice as fast.
        rate = float(message.get("rate") or 1.0)
        if rate <= 0:
            rate = 1.0
        self.stop_event.clear()
        scene = self._make_scene(skip_animations=False, start=start, end=end)
        self._activate_scene(scene)
        if rate != 1.0:
            TimingScaler(scene, {}, speed=rate)
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
        self._playhead_hint = block.end
        if self._crawler is not None:
            self._crawler.prioritize(block.end)
        self.emit(Event.TICK, time=block.end, index=index)
        self.emit_status("idle", index=index)

    def _cmd_scrub_time(self, time_seconds: float) -> None:
        """Show the scene at an arbitrary time, cache first.

        A filmstrip hit is sent directly with no GL work at all (smooth, like a
        video seek but always fresh).  On a miss the scene is posed by replay and
        the rendered frame is added to the filmstrip for the next visit.  Newer
        scrub requests replace each other in ``self._pending_scrub`` while this
        runs, so drags never build a backlog.
        """
        if not self.blocks:
            return
        self._playhead_hint = max(0.0, float(time_seconds))
        if self._crawler is not None:
            self._crawler.prioritize(self._playhead_hint)
        fps = self._fps
        index = round(float(time_seconds) * fps)
        if self._filmstrip is not None and self.frame_pump is not None:
            blob = self._filmstrip.get(index)
            if blob is None:
                blob = self._filmstrip.nearest(index, max_distance=3)
            if blob is not None:
                self.frame_pump.offer_encoded(blob)
                block = self._block_at(time_seconds)
                self.emit(
                    Event.TICK,
                    time=round(float(time_seconds), 4),
                    index=block.index if block is not None else 0,
                    scrubbing=True,
                    cached=True,
                )
                return

        if self._scrub_engine is None:
            scene = self._make_scene(skip_animations=True)
            if self.config.embedded:
                scene.camera.use_window_fbo(False)
            self._scrub_engine = ScrubEngine(scene, self.blocks)
        engine = self._scrub_engine
        try:
            block = engine.pose(time_seconds)
        except ScrubError as exc:
            self.emit_error(exc, context="scrub")
            return
        self.timings.record("pose", engine.last_pose_seconds)
        scene = engine.scene
        scene.update_frame(dt=0, force_draw=True)
        blob = self._grab_frame_blob(scene.camera)
        if self._filmstrip is not None:
            self._filmstrip.put(index, blob)
        if self.frame_pump is not None:
            self.frame_pump.offer_encoded(blob)
        self.emit(
            Event.TICK,
            time=round(float(time_seconds), 4),
            index=block.index if block is not None else 0,
            scrubbing=True,
            cached=False,
        )

    def _grab_frame_blob(self, camera: Any) -> bytes:
        """Read one frame and return the complete encoded payload."""
        with self.timings.phase("readback"):
            rgba, width, height = read_upright_rgba(camera)
        with self.timings.phase("encode"):
            return ipc.encode_frame(width, height, rgba)

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
        _silence_known_noise()
        from manim_studio.latex import apply_runtime_path

        apply_runtime_path()
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
            pending_scrub, self._pending_scrub = self._pending_scrub, None
            if pending_scrub is not None:
                self._cmd_scrub_time(pending_scrub)
                continue
            self._ensure_crawler()
            if self._crawler is not None and not self._crawler.done:
                self._step_crawler()
                continue
            if self.window is not None and getattr(self.window, "is_closing", False):
                break
            if self.window is not None and not self.config.embedded:
                with contextlib.suppress(Exception):
                    self.window._window.dispatch_events()
            time.sleep(0.004)

    def _shutdown(self) -> None:
        self._shutdown_started = True
        self._dispose_scrub_engine()
        self._dispose_filmstrip()
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
        prog="manim-studio-preview",
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
        default="end",
        help="state shown right after loading (default: final frame)",
    )
    parser.add_argument("--fps", type=int, default=None, help="camera fps override")
    parser.add_argument(
        "--fail-fast",
        action="store_true",
        help="exit when startup fails instead of waiting for a reload",
    )
    parser.add_argument(
        "--patch-file",
        type=Path,
        default=None,
        help="sidecar-format JSON of timeline edits to run the scene with",
    )
    parser.add_argument("--log-level", default="INFO")
    return parser


def _read_patches(path: Path | None) -> tuple[Patch, ...]:
    """Patches from ``--patch-file``; an unreadable file is logged and ignored."""
    if path is None:
        return ()
    from manim_studio.sidecar import SidecarError, read_patch_file

    try:
        return read_patch_file(path)
    except SidecarError as exc:
        log.error("ignoring --patch-file: %s", exc)
        return ()


def main(argv: list[str] | None = None) -> int:
    _silence_known_noise()
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
        patches=_read_patches(args.patch_file),
    )
    # manimgl parses sys.argv at import time; hide our arguments from it.
    from manim_studio.scene_runner import manimlib_argv_guard

    with manimlib_argv_guard():
        return SceneHost(config).run()


if __name__ == "__main__":
    raise SystemExit(main())
