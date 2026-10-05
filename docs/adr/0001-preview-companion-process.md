# ADR 0001 — Preview runs in a companion process; embedded mode streams frames

- Status: accepted (2026-10-06)
- Context: The spec describes Stage 2 as "single window via FBO readback ... all GL on
  GUI thread". Running pyglet/moderngl's event loop inside Qt's GUI thread means two
  event loops sharing one thread; a GPU or driver hiccup then takes down the editor.

## Decision

1. All OpenGL lives in `manim_editor/preview_host.py`, a companion process with one GL
   context on one thread. The editor spawns it, passes a loopback TCP port, and the host
   connects back.
2. Two presentation modes:
   - **Stage 1 — external window**: the host shows a normal GL window next to Qt.
   - **Stage 2 — embedded**: the host keeps its window hidden, renders the scene,
     reads back the framebuffer (`fbo.read()`), compresses RGBA with zlib and streams
     length-prefixed frames on a dedicated socket. The editor renders frames into a
     `QLabel` as `QImage`s, driven by a `QTimer` on the GUI thread.
3. Backpressure: at most one frame in flight; frames are dropped, not queued, when the
   transport is congested. Preview freshness beats preview completeness.
4. The Qt process never imports `manimlib` or `moderngl`, so scene crashes, GL driver
   failures and Python errors in user code cannot kill the editor.

## Consequences

- A rendering crash isolates to the host; the editor restarts it automatically.
- Slightly higher latency than same-process GL; acceptable for editing.
- The IPC protocol is a first-class, versioned, unit-tested interface.
- Export does not use the host: it shells out to `manimgl -w` for deterministic output.
