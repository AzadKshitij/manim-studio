"""Generate ``docs/architecture.excalidraw`` from the current backend design.

Run with ``uv run python scripts/make_architecture_excalidraw.py``.  The diagram is
a hand-laid-out Excalidraw scene (no external deps), so it can be regenerated when
the process/thread/IPC layout changes.  Coordinates are a fixed grid; the layout is
versioned alongside the code, not derived from it.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

OUT = Path(__file__).resolve().parent.parent / "docs" / "architecture.excalidraw"

# Palette ---------------------------------------------------------------
CONTAINER_BG = "#e7f5ff"
CONTAINER_LINE = "#1c7ed6"
UI_BG = "#fff3bf"
UI_LINE = "#e67700"
CTRL_BG = "#d3f9d8"
CTRL_LINE = "#2f9e44"
CORE_BG = "#e5dbff"
CORE_LINE = "#7048e8"
IPC_BG = "#f3f0ff"
IPC_LINE = "#9c36b5"
HOST_BG = "#ffe3e3"
HOST_LINE = "#c92a2a"
EXEC_BG = "#e6fcf5"
EXEC_LINE = "#0ca678"
CACHE_BG = "#fff0f6"
CACHE_LINE = "#c2255c"

CTRL_ARROW = "#1971c2"
EVENT_ARROW = "#c2255c"
FRAME_ARROW = "#e8590c"
INNER_ARROW = "#2f9e44"
TEXT_DARK = "#1e1e1e"

_counter = 0


def _id(prefix: str) -> str:
    global _counter
    _counter += 1
    seed = 1000 + _counter
    return f"{prefix}{_counter}", seed


def _text_box(
    x: float,
    y: float,
    w: float,
    h: float,
    text: str,
    *,
    bg: str,
    line: str,
    fs: int = 16,
    dashed: bool = False,
    rounded: bool = True,
) -> list[dict[str, Any]]:
    rid, seed = _id("rect")
    tid, tseed = _id("text")
    rect = {
        "id": rid,
        "type": "rectangle",
        "x": x,
        "y": y,
        "width": w,
        "height": h,
        "angle": 0,
        "strokeColor": line,
        "backgroundColor": bg,
        "fillStyle": "solid",
        "strokeWidth": 2 if dashed else 1,
        "strokeStyle": "dashed" if dashed else "solid",
        "roughness": 1,
        "opacity": 100,
        "groupIds": [],
        "roundness": {"type": 3} if rounded else None,
        "seed": seed,
        "version": 1,
        "versionNonce": seed,
        "isDeleted": False,
        "boundElements": [{"type": "text", "id": tid}],
        "updated": 1700000000000,
        "link": None,
        "locked": False,
    }
    lines = text.count("\n") + 1
    text_el = {
        "id": tid,
        "type": "text",
        "x": x + 10,
        "y": y + 8,
        "width": w - 20,
        "height": lines * fs * 1.25,
        "angle": 0,
        "strokeColor": TEXT_DARK,
        "backgroundColor": "transparent",
        "fillStyle": "solid",
        "strokeWidth": 1,
        "strokeStyle": "solid",
        "roughness": 1,
        "opacity": 100,
        "groupIds": [],
        "roundness": None,
        "seed": tseed,
        "version": 1,
        "versionNonce": tseed,
        "isDeleted": False,
        "boundElements": None,
        "updated": 1700000000000,
        "link": None,
        "locked": False,
        "text": text,
        "fontSize": fs,
        "fontFamily": 1,
        "textAlign": "center",
        "verticalAlign": "middle",
        "containerId": rid,
        "originalText": text,
        "lineHeight": 1.25,
    }
    return [rect, text_el]


def _label(x: float, y: float, text: str, *, fs: int = 22, color: str = TEXT_DARK) -> dict[str, Any]:
    tid, tseed = _id("label")
    longest = max((len(line) for line in text.split("\n")), default=1)
    lines = text.count("\n") + 1
    return {
        "id": tid,
        "type": "text",
        "x": x,
        "y": y,
        "width": longest * fs * 0.6,
        "height": lines * fs * 1.25,
        "angle": 0,
        "strokeColor": color,
        "backgroundColor": "transparent",
        "fillStyle": "solid",
        "strokeWidth": 1,
        "strokeStyle": "solid",
        "roughness": 1,
        "opacity": 100,
        "groupIds": [],
        "roundness": None,
        "seed": tseed,
        "version": 1,
        "versionNonce": tseed,
        "isDeleted": False,
        "boundElements": None,
        "updated": 1700000000000,
        "link": None,
        "locked": False,
        "text": text,
        "fontSize": fs,
        "fontFamily": 1,
        "textAlign": "left",
        "verticalAlign": "top",
        "containerId": None,
        "originalText": text,
        "lineHeight": 1.25,
    }


def _arrow(
    x1: float,
    y1: float,
    x2: float,
    y2: float,
    *,
    color: str,
    double: bool = False,
    dashed: bool = False,
) -> dict[str, Any]:
    aid, seed = _id("arrow")
    x, y = min(x1, x2), min(y1, y2)
    points = [[x1 - x, y1 - y], [x2 - x, y2 - y]]
    return {
        "id": aid,
        "type": "arrow",
        "x": x,
        "y": y,
        "width": abs(x2 - x1),
        "height": abs(y2 - y1),
        "angle": 0,
        "strokeColor": color,
        "backgroundColor": "transparent",
        "fillStyle": "solid",
        "strokeWidth": 2,
        "strokeStyle": "dashed" if dashed else "solid",
        "roughness": 1,
        "opacity": 100,
        "groupIds": [],
        "roundness": {"type": 2},
        "seed": seed,
        "version": 1,
        "versionNonce": seed,
        "isDeleted": False,
        "boundElements": None,
        "updated": 1700000000000,
        "link": None,
        "locked": False,
        "points": points,
        "lastCommittedPoint": None,
        "startBinding": None,
        "endBinding": None,
        "startArrowhead": "arrow" if double else None,
        "endArrowhead": "arrow",
    }


def build() -> dict[str, Any]:
    elements: list[dict[str, Any]] = []

    def add(items: list[dict[str, Any]]) -> None:
        elements.extend(items)

    # --- Title -------------------------------------------------------
    add([_label(120, 20, "Manim Studio — backend architecture", fs=40, color="#0b7285")])
    add(
        [
            _label(
                120,
                78,
                "Two processes linked by loopback TCP: the Qt editor and the single-OpenGL-context "
                "preview host.  (docs/protocol.md · docs/adr/)",
                fs=18,
                color="#495057",
            )
        ]
    )

    # --- Editor process container -----------------------------------
    add(_text_box(120, 140, 2860, 880, "", bg=CONTAINER_BG, line=CONTAINER_LINE, dashed=True))
    add([_label(150, 152, "EDITOR PROCESS · Qt GUI · `manim-studio gui`", fs=24, color=CONTAINER_LINE)])

    # Row A: UI widgets
    add(_text_box(160, 205, 640, 165, "Code editor\nScintillaEdit + Pygments highlight", bg=UI_BG, line=UI_LINE))
    add(_text_box(860, 205, 640, 165, "Completion (Jedi QThread)\nCompletionWorker · latest-wins · api_index", bg=UI_BG, line=UI_LINE))
    add(_text_box(1560, 205, 640, 165, "Timeline widget\ntransport bar · block inspector · filmstrip rows", bg=UI_BG, line=UI_LINE))
    add(_text_box(2260, 205, 680, 165, "Preview panel\nQImage → QPixmap (scaled, smoothed after settle)", bg=UI_BG, line=UI_LINE))

    # Row B
    add(_text_box(160, 405, 640, 145, "Shell panel (dock)\nlive IPython `exec` into the running scene", bg=UI_BG, line=UI_LINE))
    add(_text_box(860, 405, 640, 145, "Dialogs\nexport · App stats · LaTeX setup", bg=UI_BG, line=UI_LINE))
    add(_text_box(1560, 405, 640, 145, "GUI thread rule\nUI only — no I/O, no GL, no sleeps;\nworker results arrive as Qt signals", bg="#f8f9fa", line="#868e96"))

    # Row C
    add(_text_box(160, 620, 640, 190, "PreviewController\nQProcess + QTcpServer\ncontrol socket + frames socket", bg=CTRL_BG, line=CTRL_LINE))
    add(_text_box(860, 620, 640, 190, "Patch / Export / Stats / VideoMode\ncontrollers (orchestration)", bg=CTRL_BG, line=CTRL_LINE)) 
    add(_text_box(1560, 620, 640, 190, "Pure core (stdlib only)\nipc · frame_codec · timeline_model\ntracer · patches · sidecar · filmstrip · reuse · theme", bg=CORE_BG, line=CORE_LINE))

    # Row D note
    add([_label(170, 840, "reload → host spawn/reload · file watcher → reload · timeline edit → <scene>.studio.json sidecar + patches", fs=16, color="#495057")])

    # --- IPC bus -----------------------------------------------------
    add(_text_box(120, 1090, 2860, 520, "", bg=IPC_BG, line=IPC_LINE, dashed=True))
    add([_label(150, 1102, "IPC — loopback TCP 127.0.0.1 (editor listens, host connects back)", fs=24, color=IPC_LINE)])
    add(
        _text_box(
            160,
            1170,
            1300,
            320,
            "control channel — newline-delimited JSON, both ways\n"
            "hello{channel, protocol=5} handshake\n"
            "→ commands: load · reload · play · stop · seek · scrub · resize\n"
            "                  exec · ping · stats · set_patches · quit\n"
            "← events:   log · status · tick · stopped · error\n"
            "                  exec_result · filmstrip · stats · pong\n"
            "backpressure: one in flight, newest wins",
            bg="#e5dbff",
            line=IPC_LINE,
            fs=17,
        )
    )
    add(
        _text_box(
            2260,
            1170,
            680,
            320,
            "frames channel — binary, host → editor\n"
            "(embedded mode only)\n"
            "header <4sBIIQ>:\n"
            "  magic 'MFRM' · scheme(0 zlib/1 zstd/2 raw)\n"
            "  width · height · payload_len\n"
            "+ upright RGBA8888 payload\n"
            "drops frames instead of queueing",
            bg="#ffe8cc",
            line=FRAME_ARROW,
            fs=17,
        )
    )
    add(
        _text_box(
            1560,
            1170,
            600,
            320,
            "lifecycle & handshake\n"
            "host retries connect for 15 s\n"
            "graceful quit, else kill after 3 s\n"
            "watchdog exits host if editor dies\n"
            "editor restarts host on next reload\n"
            "PROTOCOL_VERSION guards both sides",
            bg="#f8f9fa",
            line="#868e96",
            fs=16,
        )
    )

    # --- Preview host container -------------------------------------
    add(_text_box(120, 1660, 2860, 1080, "", bg=HOST_BG, line=HOST_LINE, dashed=True))
    add(
        [
            _label(
                150,
                1672,
                "PREVIEW HOST PROCESS · `manim-studio-preview` · owns the ONLY OpenGL context",
                fs=24,
                color=HOST_LINE,
            )
        ]
    )

    # Row A: threads
    add(_text_box(160, 1760, 640, 160, "Control reader thread\nJSON lines → command queue", bg=HOST_BG, line=HOST_LINE))
    add(_text_box(860, 1760, 640, 160, "Emitter\nstatus · tick · log · filmstrip · stats", bg=HOST_BG, line=HOST_LINE))
    add(_text_box(1560, 1760, 640, 160, "Playback / seek / scrub\ncoalesced; stop generation guard", bg=HOST_BG, line=HOST_LINE))
    add(_text_box(2260, 1760, 680, 160, "FramePump thread\nlatest-wins slot · dedupe raw == last · encode · send", bg=CACHE_BG, line=FRAME_ARROW))

    # Row B
    add(_text_box(160, 1950, 640, 160, "GL thread — idle loop\ndrain commands · pose · render · crawl", bg=HOST_BG, line=HOST_LINE))
    add(_text_box(860, 1950, 640, 160, "Live shell\nIPython InteractiveShell · checkpoint_paste", bg=HOST_BG, line=HOST_LINE))
    add(_text_box(1560, 1950, 640, 160, "TickThrottler\nplayhead ticks ≤ 20 Hz", bg=HOST_BG, line=HOST_LINE))
    add(_text_box(2260, 1950, 680, 160, "Render readback\ncamera.get_raw_fbo_data() → flip upright RGBA", bg=CACHE_BG, line=FRAME_ARROW))

    # Row C: scene execution
    add(_text_box(160, 2140, 640, 160, "scene_runner\nload_patched_module — splice edits in memory,\nnever rewrite the .py", bg=EXEC_BG, line=EXEC_LINE))
    add(_text_box(860, 2140, 640, 160, "run_trace + tracer\nwraps play()/wait() → blocks · sites · clips", bg=EXEC_BG, line=EXEC_LINE))
    add(_text_box(1560, 2140, 640, 160, "ScrubEngine\nreplay skip-mode pose + interpolate α", bg=EXEC_BG, line=EXEC_LINE))
    add(_text_box(2260, 2140, 680, 160, "FilmstripCrawler\nblock-granular, greenlet on the GL thread", bg=EXEC_BG, line=EXEC_LINE))

    # Row D: cache + deps
    add(_text_box(160, 2330, 640, 150, "filmstrip.py\nbounded LRU of encoded frames", bg=CACHE_BG, line=CACHE_LINE))
    add(_text_box(860, 2330, 640, 150, "reuse.py\nwhich cached blocks an edit cannot touch", bg=CACHE_BG, line=CACHE_LINE))
    add(_text_box(1560, 2330, 640, 150, "manimlib 1.7.x + moderngl\nthe only place GL is imported", bg=CACHE_BG, line=CACHE_LINE))
    add(_text_box(2260, 2330, 680, 150, "Flat GPU memory model\none context, one thread; Qt never touches GL", bg="#f8f9fa", line="#868e96"))

    # Bottom note
    add(
        [
            _label(
                170,
                2520,
                "Render loop:  scene.update_frame(dt=0) → FBO readback → upright RGBA → FramePump.offer() "
                "→ encode_frame() → frames channel → PreviewPanel QImage.",
                fs=17,
                color="#495057",
            ),
            _label(
                170,
                2552,
                "Scrub:  filmstrip hit = instant (no GL); miss = ScrubEngine.pose(time) then cache the result.  "
                "Edits:  apply_patches() runs the same source in preview, scrub, filmstrip and export.",
                fs=17,
                color="#495057",
            ),
        ]
    )

    # --- Cross-process arrows ---------------------------------------
    # commands (editor → host), down the left lane
    add([_arrow(480, 815, 480, 1165, color=CTRL_ARROW)])  # PreviewController → control box
    add([_arrow(480, 1495, 480, 1755, color=CTRL_ARROW)])  # control box → control reader
    add([_label(500, 980, "commands ↓", fs=16, color=CTRL_ARROW)])
    add([_label(500, 1600, "commands ↓", fs=16, color=CTRL_ARROW)])

    # events (host → editor), up the middle lane
    add([_arrow(1200, 1755, 1200, 1495, color=EVENT_ARROW)])  # emitter → control box
    add([_arrow(640, 1165, 640, 815, color=EVENT_ARROW)])  # control box → PreviewController
    add([_label(1220, 1600, "events ↑", fs=16, color=EVENT_ARROW)])
    add([_label(658, 980, "log / tick ↑", fs=16, color=EVENT_ARROW)])

    # frames (host → editor), up the right corridor
    add([_arrow(2600, 1755, 2600, 1495, color=FRAME_ARROW)])  # FramePump → frames box
    add([_arrow(2600, 1165, 2600, 375, color=FRAME_ARROW)])  # frames box → PreviewPanel
    add([_label(2618, 1600, "frames ↑", fs=16, color=FRAME_ARROW)])
    add([_label(2618, 640, "RGBA → QImage", fs=16, color=FRAME_ARROW)])

    # --- Host-internal arrows ----------------------------------------
    add([_arrow(480, 1925, 480, 1945, color=INNER_ARROW)])  # control reader → GL idle loop
    add([_label(500, 1926, "queue", fs=15, color=INNER_ARROW)])
    add([_arrow(2600, 1945, 2600, 1925, color=INNER_ARROW)])  # render → FramePump
    add([_label(2618, 1926, "offer(rgba)", fs=15, color=INNER_ARROW)])

    return {
        "type": "excalidraw",
        "version": 2,
        "source": "https://excalidraw.com",
        "elements": elements,
        "appState": {
            "gridSize": None,
            "viewBackgroundColor": "#ffffff",
        },
        "files": {},
    }


def main() -> int:
    OUT.write_text(json.dumps(build(), indent=2) + "\n", encoding="utf-8", newline="\n")
    print(f"wrote {OUT.relative_to(OUT.parent.parent)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
