# Preview protocol (v4)

The editor (Qt) and the preview host (`manim_studio.preview_host`) communicate over
loopback TCP. The editor listens; the host connects back to the port passed on argv.
All message shapes live in `src/manim_studio/ipc.py`; bump `PROTOCOL_VERSION` for any
breaking change and update this document plus the tests in `tests/test_ipc.py`,
`tests/test_preview_host.py` and `tests/test_preview_controller.py`.

## Channels

The editor accepts two connections per host process. Every connection starts with a
`hello` message that declares its channel:

```json
{"event": "hello", "channel": "control", "protocol": 4, "role": "host",
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
| `load` | `path`, `scene_name?`, `patches?` | import and trace a (new) scene file, show the initial frame; `patches` (v4) replaces the timeline edits the scene runs with |
| `reload` | `path?`, `patches?` | re-import the current file (deep-reloads user imports); omitted `patches` keeps the current set |
| `play` | `from?` (block index, default 0), `to?` (inclusive end block), `rate?` (speed, default 1.0) | replay in real time from a block; `to` bounds it to a range (looping) and `rate` is a review-speed multiplier (v3, additive) |
| `stop` | — | interrupt playback at the current frame |
| `seek` | `index` | jump to the state at the *end* of block `index` |
| `scrub` | `time` | show the scene at an exact time (filmstrip-first, replay on miss) |
| `set_patches` | `patches` (`[patch]`), `time?` | v4: run the scene with a new set of timeline edits (patched in-memory source), re-trace, answer with a `log`, then re-pose at `time` |
| `exec` | `code` | run Python in the live scene namespace (IPython) |
| `ping` | — | liveness check |
| `stats` | — | report full RAM/cache snapshot (v2) |
| `quit` | — | clean shutdown |

## Events (host → editor)

| `event` | Fields | Meaning |
| --- | --- | --- |
| `log` | `blocks[]`, `stats{}`, `events[]`, `total_duration`, `trace_seconds`, `scene`, `scene_name`, `trace_id`, `fps`, `call_sites[]`, `sites[]`, `patches{}`, `cause` | runtime trace finished; `cause` is `"load"` or `"patches"` (a reload that only applied timeline edits, so the editor keeps zoom and playhead) |
| `status` | `state` (`idle`/`playing`/`seeking`/`error`/`closed`), `index?`, `note?` | lifecycle |
| `tick` | `time`, `index`, `scrubbing?`, `cached?` | playhead update during playback/scrub (≤20 Hz) |
| `stopped` | `time`, `index`, `user_stopped` | playback ended |
| `error` | `message`, `traceback`, `line?`, `scene_file?`, `context` | user-scene or command failure |
| `exec_result` | `ok`, `stdout`, `stderr`, `result`, `error` | reply to `exec` |
| `filmstrip` | `trace_id`, `frames`, `done`, `capped`, `fps`, `bytes`, `max_bytes`, `max_frames`, `host_rss?`, `sent`, `dropped`, `deduped`, `time_to_playhead?`, `timings` | background frame-cache progress with RAM accounting (v2 fields additive; older editors ignore them) |
| `stats` | `trace_id`, `frames`, `bytes`, `max_bytes`, `max_frames`, `host_rss?`, `sent`, `dropped`, `deduped`, `fps`, `blocks`, `total_duration`, `size`, `scene_name?`, `timings` | on-demand snapshot for the App stats dialog (v2) |
| `pong` | `t` | reply to `ping` |

`log.blocks` is the spec `log.json` shape:
`[{ "name": str, "start": float, "duration": float, "index": int }, ...]`.

`log.call_sites` is a parallel array (same order/length as `blocks`) of source
line numbers in the *scene file* for each block, or `null` when the call site is
unknown or lives in an imported module. It powers Ctrl+double-click
"go to code" on the timeline and is additive (v3).

`log.sites` (v4) is another parallel array: for each block, `{"id": str, "occ": int}`
naming the call site that produced it (a fingerprint from `site_index`, stable across
unrelated edits and retiming) and which execution of it this is (0-based loop
iteration), or `null` for calls outside the scene file. Timeline edits are anchored on
these ids, never on block indices.

`log.patches` (v4) reports how the timeline edits fared against the current source:
`{"applied": int, "orphaned": [{"patch": {...}, "reason": str}]}`. An orphaned edit is
one whose call site no longer exists (or cannot take the edit); it is kept by the
editor, shown as such, and never applied to a different call.

### Patches (v4)

A patch is an anchored timeline edit (`src/manim_studio/patches.py`):

```json
{"op": "set_duration", "site": "Demo.construct:wait:3fa9c1d2e0:0", "occurrence": 1, "seconds": 2.5}
{"op": "delete", "site": "Demo.construct:play:9b1e44aa10:0"}
```

`occurrence` (optional, 0-based) narrows a `set_duration` to one loop iteration;
without it the edit applies to every iteration. The host applies patches by splicing
source text and executing the result as the scene file, so preview, scrub, filmstrip
and export all run the same code; the `.py` on disk is never modified. Exports read the
same edits from the `<scene>.studio.json` sidecar (`manimgl_launcher --patch-file`).

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
