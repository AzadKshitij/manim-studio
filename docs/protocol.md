# Preview protocol (v3)

The editor (Qt) and the preview host (`manim_studio.preview_host`) communicate over
loopback TCP. The editor listens; the host connects back to the port passed on argv.
All message shapes live in `src/manim_studio/ipc.py`; bump `PROTOCOL_VERSION` for any
breaking change and update this document plus the tests in `tests/test_ipc.py`,
`tests/test_preview_host.py` and `tests/test_preview_controller.py`.

## Channels

The editor accepts two connections per host process. Every connection starts with a
`hello` message that declares its channel:

```json
{"event": "hello", "channel": "control", "protocol": 3, "role": "host",
 "pid": 1234, "scene": "C:/path/scene.py", "scene_name": "Demo",
 "embedded": true, "size": [1280, 720], "fps": 30}
```

| Channel | Direction | Transport |
| --- | --- | --- |
| `control` | both | newline-delimited JSON (`JsonLineWriter`/`JsonLineReader`) |
| `frames` | host → editor | length-prefixed zstd/zlib/raw RGBA (`encode_frame`/`FrameParser`) |

The frames channel is only opened in `--embedded` mode.

## Commands (editor → host)

| `cmd` | Fields | Meaning |
| --- | --- | --- |
| `load` | `path` | import and trace a (new) scene file, show the initial frame |
| `reload` | `path?` | re-import the current file (deep-reloads user imports) |
| `play` | `from?` (block index, default 0), `to?` (inclusive end block), `rate?` (speed, default 1.0) | replay in real time from a block; `to` bounds it to a range (looping) and `rate` is a review-speed multiplier (v3, additive) |
| `stop` | — | interrupt playback at the current frame |
| `seek` | `index` | jump to the state at the *end* of block `index` |
| `scrub` | `time` | show the scene at an exact time (filmstrip-first, replay on miss) |
| `set_timing` | `timings` (`[{index, duration}]`), `time?` | retime blocks (proportional scale of each play/wait) and re-pose at `time`; empty `timings` restores the trace (v3, additive) |
| `exec` | `code` | run Python in the live scene namespace (IPython) |
| `ping` | — | liveness check |
| `stats` | — | report full RAM/cache snapshot (v2) |
| `quit` | — | clean shutdown |

## Events (host → editor)

| `event` | Fields | Meaning |
| --- | --- | --- |
| `log` | `blocks[]`, `stats{}`, `events[]`, `total_duration`, `trace_seconds`, `scene`, `scene_name`, `trace_id`, `fps`, `call_sites[]` | runtime trace finished |
| `status` | `state` (`idle`/`playing`/`seeking`/`error`/`closed`), `index?`, `note?` | lifecycle |
| `tick` | `time`, `index`, `scrubbing?`, `cached?` | playhead update during playback/scrub (≤20 Hz) |
| `stopped` | `time`, `index`, `user_stopped` | playback ended |
| `error` | `message`, `traceback`, `line?`, `scene_file?`, `context` | user-scene or command failure |
| `exec_result` | `ok`, `stdout`, `stderr`, `result`, `error` | reply to `exec` |
| `filmstrip` | `trace_id`, `frames`, `done`, `capped`, `fps`, `bytes`, `max_bytes`, `max_frames`, `host_rss?`, `sent`, `dropped`, `timings` | background frame-cache progress with RAM accounting (v2 fields additive; older editors ignore them) |
| `stats` | `trace_id`, `frames`, `bytes`, `max_bytes`, `max_frames`, `host_rss?`, `sent`, `dropped`, `fps`, `blocks`, `total_duration`, `size`, `scene_name?`, `timings` | on-demand snapshot for the App stats dialog (v2) |
| `timing` | `trace_id`, `blocks[]`, `total_duration`, `timings` | reply to `set_timing` with the rebased timeline (v3, additive) |
| `pong` | `t` | reply to `ping` |

`log.blocks` is the spec `log.json` shape:
`[{ "name": str, "start": float, "duration": float, "index": int }, ...]`.

`log.call_sites` is a parallel array (same order/length as `blocks`) of source
line numbers in the *scene file* for each block, or `null` when the call site is
unknown or lives in an imported module. It powers Ctrl+double-click
"go to code" on the timeline and is additive (v3).

## Frame encoding

Each frame is a fixed header followed by one compressed payload. The scheme byte
selects the codec for that payload (``0`` zlib, ``1`` zstd, ``2`` raw); the host
picks the smallest of zstd/zlib and falls back to raw when compression would not
shrink the frame. This is v3: the scheme byte did not exist in v2.

```
offset 0   magic   "MFRM" (4 bytes)
offset 4   scheme  uint8 (0 zlib, 1 zstd, 2 raw)
offset 5   width   uint32 LE
offset 9   height  uint32 LE
offset 13  length  uint64 LE (payload bytes)
offset 21  payload upright RGBA8888, coded per scheme
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
