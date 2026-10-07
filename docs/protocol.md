# Preview protocol (v2)

The editor (Qt) and the preview host (`manim_editor.preview_host`) communicate over
loopback TCP. The editor listens; the host connects back to the port passed on argv.
All message shapes live in `src/manim_editor/ipc.py`; bump `PROTOCOL_VERSION` for any
breaking change and update this document plus the tests in `tests/test_ipc.py`,
`tests/test_preview_host.py` and `tests/test_preview_controller.py`.

## Channels

The editor accepts two connections per host process. Every connection starts with a
`hello` message that declares its channel:

```json
{"event": "hello", "channel": "control", "protocol": 2, "role": "host",
 "pid": 1234, "scene": "C:/path/scene.py", "scene_name": "Demo",
 "embedded": true, "size": [1280, 720], "fps": 30}
```

| Channel | Direction | Transport |
| --- | --- | --- |
| `control` | both | newline-delimited JSON (`JsonLineWriter`/`JsonLineReader`) |
| `frames` | host → editor | length-prefixed zlib RGBA (`encode_frame`/`FrameParser`) |

The frames channel is only opened in `--embedded` mode.

## Commands (editor → host)

| `cmd` | Fields | Meaning |
| --- | --- | --- |
| `load` | `path` | import and trace a (new) scene file, show the initial frame |
| `reload` | `path?` | re-import the current file (deep-reloads user imports) |
| `play` | `from?` (block index, default 0) | replay in real time from a block |
| `stop` | — | interrupt playback at the current frame |
| `seek` | `index` | jump to the state at the *end* of block `index` |
| `scrub` | `time` | show the scene at an exact time (filmstrip-first, replay on miss) |
| `exec` | `code` | run Python in the live scene namespace (IPython) |
| `ping` | — | liveness check |
| `stats` | — | report full RAM/cache snapshot (v2) |
| `quit` | — | clean shutdown |

## Events (host → editor)

| `event` | Fields | Meaning |
| --- | --- | --- |
| `log` | `blocks[]`, `stats{}`, `events[]`, `total_duration`, `trace_seconds`, `scene`, `scene_name`, `trace_id`, `fps` | runtime trace finished |
| `status` | `state` (`idle`/`playing`/`seeking`/`error`/`closed`), `index?`, `note?` | lifecycle |
| `tick` | `time`, `index`, `scrubbing?`, `cached?` | playhead update during playback/scrub (≤20 Hz) |
| `stopped` | `time`, `index`, `user_stopped` | playback ended |
| `error` | `message`, `traceback`, `line?`, `scene_file?`, `context` | user-scene or command failure |
| `exec_result` | `ok`, `stdout`, `stderr`, `result`, `error` | reply to `exec` |
| `filmstrip` | `trace_id`, `frames`, `done`, `capped`, `fps`, `bytes`, `max_bytes`, `max_frames`, `host_rss?`, `sent`, `dropped` | background frame-cache progress with RAM accounting (v2 fields additive; older editors ignore them) |
| `stats` | `trace_id`, `frames`, `bytes`, `max_bytes`, `max_frames`, `host_rss?`, `sent`, `dropped`, `fps`, `blocks`, `total_duration`, `size`, `scene_name?` | on-demand snapshot for the App stats dialog (v2) |
| `pong` | `t` | reply to `ping` |

`log.blocks` is the spec `log.json` shape:
`[{ "name": str, "start": float, "duration": float, "index": int }, ...]`.

## Frame encoding

```
offset 0   magic   "MFRM" (4 bytes)
offset 4   width   uint32 LE
offset 8   height  uint32 LE
offset 12  length  uint64 LE (compressed payload bytes)
offset 20  payload zlib-compressed, upright RGBA8888
```

Backpressure rule: the host keeps at most one frame in flight per channel. If the
sender is still busy, `offer()` replaces the pending frame and increments
`frames_dropped`; preview freshness beats completeness. The editor must therefore
treat frames as a stream of latest states, never as a queue.  Scrub commands follow
the same rule: while a replay is running, newer `scrub` requests replace each other
in a single pending slot, so pointer-rate drags never build a backlog.

## Timeouts and liveness

- The host retries connecting for 15 s, then exits with a connection error.
- If the control channel closes, the host stops playback and shuts down (the editor
  owns the lifecycle).
- The editor kills the host after 3 s if a graceful `quit` does not complete.
- The editor auto-restarts the host on the next reload if it crashed.
