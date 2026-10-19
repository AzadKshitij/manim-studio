"""Agent-facing command line interface.

Every command is deterministic, scriptable and (with ``--json``) machine readable,
so an AI agent can create, validate, trace and export scenes without touching the
GUI.  Exit codes: 0 success, 1 user/scene error, 2 usage error.
"""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import math
import os
import sys
import tempfile
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from manim_studio import __version__
from manim_studio.api_index import build_index, index_names
from manim_studio.contact_sheet import SheetError, split_times
from manim_studio.export import ExportError, build_export_plan, run_export
from manim_studio.scene_runner import (
    SceneLoadError,
    check_scene_file,
    render_still,
    run_trace,
)
from manim_studio.templates import (
    SCENE_TEMPLATES,
    SNIPPET_TEMPLATES,
    TemplateError,
    render_scene_template,
)

__all__ = ["build_parser", "gui_main", "main"]


def _print_json(payload: Any) -> None:
    print(json.dumps(payload, indent=2, sort_keys=True))


def _ensure_utf8_console() -> None:
    """Windows consoles default to legacy code pages; never crash on Unicode output."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        with contextlib.suppress(OSError, ValueError):
            reconfigure(encoding="utf-8", errors="replace")


@contextlib.contextmanager
def _capture_scene_output() -> Iterator[io.StringIO]:
    """Keep user/scene prints out of structured CLI output.

    manimgl itself prints progress (e.g. LaTeX compilation) to stdout; when a
    command promises JSON on stdout, everything else must go elsewhere.
    """
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer), contextlib.redirect_stderr(buffer):
        yield buffer


def _parse_size(text: str) -> tuple[int, int]:
    from manim_studio.preview_host import parse_size

    return parse_size(text)


def _parse_resolution(text: str | None) -> tuple[int, int] | None:
    """``"360x240"`` -> ``(360, 240)``; ``None`` when the text is malformed."""
    if not text:
        return None
    parts = text.strip().lower().split("x")
    if len(parts) != 2:
        return None
    try:
        return (int(parts[0]), int(parts[1]))
    except ValueError:
        return None


def _parse_range(text: str | None) -> tuple[int, int] | None:
    """``"2,5"`` -> ``(2, 5)``; ``None`` when the text is malformed."""
    if not text:
        return None
    parts = text.split(",")
    if len(parts) != 2:
        return None
    try:
        return (int(parts[0].strip()), int(parts[1].strip()))
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------


def cmd_check(args: argparse.Namespace) -> int:
    result = check_scene_file(args.file)
    if args.json:
        from manim_studio.latex import latex_status

        payload = result.to_dict()
        payload["latex"] = latex_status().to_dict()
        _print_json(payload)
    else:
        status = "OK" if result.ok else "FAIL"
        print(f"{status} {result.path}")
        for scene in result.scenes:
            print(f"  scene {scene.name} (line {scene.lineno})")
        for warning in result.warnings:
            print(f"  warning: {warning}")
        if result.error:
            print(f"  error: {result.error}")
    return 0 if result.ok else 1


def cmd_trace(args: argparse.Namespace) -> int:
    try:
        with _capture_scene_output() as scene_output:
            result = run_trace(
                args.file,
                args.scene,
                resolution=tuple(args.resolution),
                fps=args.fps,
            )
    except SceneLoadError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:  # scene code can raise anything
        print(f"scene failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    if args.log:
        from manim_studio.timeline_model import save_log

        save_log(Path(args.log), result.snapshot.blocks)
    if args.json:
        payload = result.to_dict()
        payload["scene_output"] = scene_output.getvalue()
        _print_json(payload)
    else:
        print(
            f"{result.scene_name}: {len(result.snapshot.blocks)} blocks, "
            f"{result.snapshot.total_duration:.3f}s "
            f"({result.wall_seconds:.2f}s wall)"
        )
        for block in result.snapshot.blocks:
            print(f"  [{block.index:>3}] {block.start:8.3f} +{block.duration:.3f}  {block.name}")
        print(f"  stats: {result.snapshot.stats}")
        if scene_output.getvalue().strip():
            print("  scene output:", file=sys.stderr)
            print(scene_output.getvalue().rstrip(), file=sys.stderr)
    return 0


def cmd_export(args: argparse.Namespace) -> int:
    resolution = _parse_resolution(args.resolution)
    if args.resolution and resolution is None:
        print(f"error: invalid --resolution {args.resolution!r}; expected WxH", file=sys.stderr)
        return 2
    animation_range = _parse_range(args.range)
    if args.range and animation_range is None:
        print(f"error: invalid --range {args.range!r}; expected FROM,TO", file=sys.stderr)
        return 2
    try:
        plan = build_export_plan(
            args.file,
            args.scene,
            output=args.output,
            quality=args.quality,
            fps=args.fps,
            still=args.still,
            gif=args.gif,
            resolution=resolution,
            animation_range=animation_range,
            use_sidecar=not args.no_sidecar,
        )
    except ExportError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2 if args.still and args.fps else 1
    if args.json:
        _print_json({"argv": list(plan.argv), "output": str(plan.output_path)})
    else:
        print(f"exporting to {plan.output_path} …")
    result = run_export(plan, on_line=None if args.json else print)
    if args.json:
        _print_json(
            {
                "ok": result.ok,
                "returncode": result.returncode,
                "output": str(result.output_path),
                "log_tail": list(result.log_tail),
            }
        )
    elif not result.ok:
        print(f"export failed with code {result.returncode}", file=sys.stderr)
    return 0 if result.ok else 1


def cmd_new(args: argparse.Namespace) -> int:
    if args.list:
        for template in SCENE_TEMPLATES:
            print(f"{template.key:<20} {template.title}")
        return 0
    target = Path(args.file)
    if target.exists() and not args.force:
        print(f"error: {target} already exists (use --force)", file=sys.stderr)
        return 1
    scene_name = args.scene_name or _scene_name_from_path(target)
    try:
        code = render_scene_template(args.template, scene_name)
    except TemplateError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(code, encoding="utf-8")
    print(f"created {target} (template {args.template}, scene {scene_name})")
    return 0


def cmd_templates(args: argparse.Namespace) -> int:
    payload = {
        "scene": [
            {"key": t.key, "title": t.title, "description": t.description} for t in SCENE_TEMPLATES
        ],
        "snippet": [
            {"key": t.key, "title": t.title, "description": t.description, "code": t.code}
            for t in SNIPPET_TEMPLATES
        ],
    }
    if args.json:
        _print_json(payload)
    else:
        for kind, items in payload.items():
            print(f"{kind} templates:")
            for item in items:
                print(f"  {item['key']:<20} {item['title']}")
    return 0


def cmd_api_index(args: argparse.Namespace) -> int:
    symbols = build_index()
    if args.json:
        _print_json([symbol.to_dict() for symbol in symbols])
    else:
        for name in index_names(symbols):
            print(name)
    return 0


def cmd_still(args: argparse.Namespace) -> int:
    try:
        with _capture_scene_output():
            output = render_still(
                args.file,
                args.scene,
                output=args.output,
                resolution=tuple(args.resolution),
            )
    except SceneLoadError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:  # scene code can raise anything
        print(f"scene failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    print(str(output))
    return 0


def _scratch_png(name: str) -> Path:
    """A PNG path in a fresh temp folder, used when ``-o`` is not given."""
    return Path(tempfile.mkdtemp(prefix="manim-studio-")) / name


def _write_png(image: Any, output: Path) -> Path:
    output.parent.mkdir(parents=True, exist_ok=True)
    image.save(output)
    return output.resolve()


def cmd_frame(args: argparse.Namespace) -> int:
    from manim_studio.frames import FrameError, render_frame

    if not math.isfinite(args.at) or args.at < 0:
        print("error: --at must be a non-negative number of seconds", file=sys.stderr)
        return 2
    try:
        with _capture_scene_output():
            result = render_frame(
                args.file,
                args.at,
                args.scene,
                resolution=tuple(args.resolution) if args.resolution else None,
                use_sidecar=not args.no_sidecar,
            )
    except (SceneLoadError, FrameError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:  # scene code can raise anything
        print(f"scene failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    name = f"{result.prepared.scene_name}_t{result.time:.2f}.png"
    output = _write_png(result.image, args.output or _scratch_png(name))
    if args.json:
        _print_json({"output": str(output), **result.to_dict()})
    else:
        print(str(output))
    return 0


def cmd_contact_sheet(args: argparse.Namespace) -> int:
    from manim_studio.frames import FrameError, render_contact_sheet

    try:
        times = split_times(args.times) if args.times is not None else None
    except SheetError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    every = None if times is not None else (args.every if args.every is not None else 0.5)
    if every is not None and not (math.isfinite(every) and every > 0):
        print("error: --every must be a positive number of seconds", file=sys.stderr)
        return 2
    try:
        with _capture_scene_output():
            result = render_contact_sheet(
                args.file,
                args.scene,
                every=every,
                times=times,
                start=args.start,
                end=args.end,
                columns=args.columns,
                max_frames=args.max_frames,
                sheet_width=args.sheet_width,
                resolution=tuple(args.resolution) if args.resolution else None,
                use_sidecar=not args.no_sidecar,
            )
    except SheetError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except (SceneLoadError, FrameError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:  # scene code can raise anything
        print(f"scene failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    output = _write_png(
        result.image, args.output or _scratch_png(f"{result.prepared.scene_name}_sheet.png")
    )
    if args.json:
        _print_json({"output": str(output), **result.to_dict()})
    else:
        print(str(output))
    return 0


def cmd_mcp(args: argparse.Namespace) -> int:
    root = Path(args.root or os.environ.get("MANIM_STUDIO_ROOT") or Path.cwd()).resolve()
    if not root.is_dir():
        print(f"error: workspace root is not a directory: {root}", file=sys.stderr)
        return 2
    from manim_studio.mcp_server import serve

    return serve(root)


def cmd_latex(args: argparse.Namespace) -> int:
    from manim_studio import latex_install
    from manim_studio.latex import install_dir, latex_status, probe_compile

    target = Path(args.dir) if args.dir else None
    if args.action == "status":
        status = latex_status(target)
        if args.json:
            _print_json(status.to_dict())
        else:
            state = "OK" if status.ok else "MISSING"
            flavor = " (MiKTeX)" if status.miktex else ""
            print(f"LaTeX: {state}{flavor} (dir {status.install_dir})")
            if status.latex_path:
                print(f"  latex: {status.latex_path}")
            if status.dvisvgm_path:
                print(f"  dvisvgm: {status.dvisvgm_path}")
            if status.missing:
                print(f"  missing: {', '.join(status.missing)}")
                print("  run 'manim-studio latex install' or use Tools > LaTeX setup.")
        return 0 if status.ok else 1
    if args.action == "diagnose":
        probe = probe_compile()
        if args.json:
            _print_json(probe.to_dict())
        else:
            print(f"LaTeX probe: {'OK' if probe.ok else 'FAIL'} - {probe.summary}")
            if probe.detail:
                print(probe.detail)
        return 0 if probe.ok else 1
    if args.clear_cache:
        cleared = latex_install.clear_download_cache(target)
        print("cleared download cache" if cleared else "download cache already empty")
    if args.action == "uninstall":
        removed = latex_install.uninstall_latex(target)
        print(f"removed {install_dir(target)}" if removed else "nothing to remove")
        return 0
    # install
    try:
        root = latex_install.install_latex(
            target, on_progress=None if args.json else _latex_progress, use_cache=not args.no_cache
        )
    except latex_install.InstallError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    status = latex_status(root)
    if args.json:
        _print_json({"install_dir": str(root), "latex": status.to_dict()})
    else:
        print(f"installed to {root} (ok={status.ok})")
    return 0 if status.ok else 1


def _latex_progress(progress: Any) -> None:
    if progress.total_bytes:
        pct = (progress.fraction or 0.0) * 100
        print(f"latex {progress.stage}: {pct:.0f}%", file=sys.stderr)
    else:
        print(f"latex: {progress.stage}…", file=sys.stderr)


def cmd_preview(args: argparse.Namespace) -> int:
    from manim_studio.preview_host import HostConfig, SceneHost

    config = HostConfig(
        scene_path=Path(args.file),
        scene_name=args.scene,
        port=None,
        embedded=not args.external,
        size=args.size,
        fail_fast=args.fail_fast,
    )
    return SceneHost(config).run()


def cmd_gui(args: argparse.Namespace) -> int:
    try:
        from manim_studio.editor.app import run_editor
    except ImportError as exc:  # pragma: no cover - missing Qt install
        print(f"error: GUI dependencies unavailable: {exc}", file=sys.stderr)
        return 1
    return run_editor(
        str(args.file) if args.file else None,
        embedded=False if args.external else None,
        preview_size=args.size,
    )


def _scene_name_from_path(path: Path) -> str:
    name = "".join(part.capitalize() for part in path.stem.replace("-", "_").split("_"))
    return name if name.isidentifier() else "MyScene"


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="manim-studio",
        description="Realtime ManimGL editor: check, trace, export and preview scenes.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    subparsers = parser.add_subparsers(dest="command", required=True)

    gui = subparsers.add_parser("gui", help="open the desktop editor")
    gui.add_argument("file", nargs="?", type=Path, help="scene file to open")
    gui.add_argument("--external", action="store_true", help="use an external GL window")
    gui.add_argument("--size", type=_parse_size, default=(1280, 720), help="preview size WxH")
    gui.set_defaults(func=cmd_gui)

    check = subparsers.add_parser("check", help="syntax + scene discovery")
    check.add_argument("file", type=Path)
    check.add_argument("--json", action="store_true")
    check.set_defaults(func=cmd_check)

    trace = subparsers.add_parser("trace", help="runtime trace of play/wait blocks")
    trace.add_argument("file", type=Path)
    trace.add_argument("--scene", default=None, help="scene class name")
    trace.add_argument("--json", action="store_true", help="print the full JSON payload")
    trace.add_argument("--log", type=Path, default=None, help="write log.json blocks")
    trace.add_argument("--resolution", type=int, nargs=2, metavar=("W", "H"), default=(854, 480))
    trace.add_argument("--fps", type=int, default=None)
    trace.set_defaults(func=cmd_trace)

    export = subparsers.add_parser("export", help="render a video or still")
    export.add_argument("file", type=Path)
    export.add_argument("--scene", default=None)
    export.add_argument("-o", "--output", type=Path, default=None)
    export.add_argument(
        "--quality", choices=("preview", "low", "medium", "high", "4k"), default="high"
    )
    export.add_argument("--fps", type=int, default=None)
    export.add_argument("--still", action="store_true", help="render the final frame (PNG)")
    export.add_argument("--gif", action="store_true", help="render a GIF animation instead of MP4")
    export.add_argument(
        "--resolution", default=None, metavar="WxH", help="custom resolution (64..4096 per side)"
    )
    export.add_argument(
        "--range",
        default=None,
        metavar="FROM,TO",
        help="render only animation blocks FROM..TO (0-based, inclusive)",
    )
    export.add_argument(
        "--no-sidecar",
        action="store_true",
        help="ignore <scene>.studio.json and render the source exactly as written",
    )
    export.add_argument("--json", action="store_true")
    export.set_defaults(func=cmd_export)

    new = subparsers.add_parser("new", help="create a scene file from a template")
    new.add_argument("file", type=Path)
    new.add_argument(
        "--template",
        choices=[template.key for template in SCENE_TEMPLATES],
        default="circle_to_square",
    )
    new.add_argument("--scene-name", default=None)
    new.add_argument("--force", action="store_true")
    new.add_argument("--list", action="store_true", help="list scene templates and exit")
    new.set_defaults(func=cmd_new)

    still = subparsers.add_parser("still", help="render the final frame to a PNG")
    still.add_argument("file", type=Path)
    still.add_argument("--scene", default=None)
    still.add_argument("-o", "--output", type=Path, default=None)
    still.add_argument("--resolution", type=int, nargs=2, metavar=("W", "H"), default=(854, 480))
    still.set_defaults(func=cmd_still)

    frame = subparsers.add_parser("frame", help="render the scene at an exact time to a PNG")
    frame.add_argument("file", type=Path)
    frame.add_argument("--at", type=float, required=True, metavar="SECONDS", help="scene time")
    frame.add_argument("--scene", default=None)
    frame.add_argument("-o", "--output", type=Path, default=None)
    frame.add_argument("--resolution", type=int, nargs=2, metavar=("W", "H"), default=None)
    frame.add_argument("--no-sidecar", action="store_true", help="ignore <scene>.studio.json")
    frame.add_argument("--json", action="store_true")
    frame.set_defaults(func=cmd_frame)

    sheet = subparsers.add_parser(
        "contact-sheet", help="tile frames with timestamps and block names into one PNG"
    )
    sheet.add_argument("file", type=Path)
    when = sheet.add_mutually_exclusive_group()
    when.add_argument(
        "--every", type=float, default=None, metavar="SECONDS", help="sample spacing (default 0.5)"
    )
    when.add_argument("--times", default=None, metavar="0,1.5,3", help="explicit times")
    sheet.add_argument("--from", dest="start", type=float, default=0.0, metavar="SECONDS")
    sheet.add_argument("--to", dest="end", type=float, default=None, metavar="SECONDS")
    sheet.add_argument("-o", "--output", type=Path, default=None)
    sheet.add_argument("--scene", default=None)
    sheet.add_argument("--columns", type=int, default=None)
    sheet.add_argument("--max-frames", type=int, default=48)
    sheet.add_argument("--width", dest="sheet_width", type=int, default=1600, metavar="PX")
    sheet.add_argument("--resolution", type=int, nargs=2, metavar=("W", "H"), default=None)
    sheet.add_argument("--no-sidecar", action="store_true", help="ignore <scene>.studio.json")
    sheet.add_argument("--json", action="store_true")
    sheet.set_defaults(func=cmd_contact_sheet)

    mcp = subparsers.add_parser("mcp", help="run the stdio MCP server for AI agents")
    mcp.add_argument(
        "--root",
        default=None,
        metavar="DIR",
        help="workspace root every path must stay inside (default: $MANIM_STUDIO_ROOT or cwd)",
    )
    mcp.set_defaults(func=cmd_mcp)

    preview = subparsers.add_parser("preview", help="run the standalone preview host")
    preview.add_argument("file", type=Path)
    preview.add_argument("--scene", default=None)
    preview.add_argument("--external", action="store_true", help="show a visible GL window")
    preview.add_argument("--size", type=_parse_size, default=(1280, 720))
    preview.add_argument("--fail-fast", action="store_true")
    preview.set_defaults(func=cmd_preview)

    templates = subparsers.add_parser("templates", help="list templates")
    templates.add_argument("--json", action="store_true")
    templates.set_defaults(func=cmd_templates)

    api = subparsers.add_parser("api-index", help="scan the installed manimlib API")
    api.add_argument("--json", action="store_true")
    api.set_defaults(func=cmd_api_index)

    latex = subparsers.add_parser("latex", help="check/install/diagnose the LaTeX distribution")
    latex.add_argument("action", choices=("status", "install", "uninstall", "diagnose"))
    latex.add_argument("--dir", type=Path, default=None, help="override install location")
    latex.add_argument("--json", action="store_true")
    latex.add_argument(
        "--no-cache", action="store_true", help="install: ignore the cached TinyTeX download"
    )
    latex.add_argument(
        "--clear-cache", action="store_true", help="install/uninstall: delete cached downloads"
    )
    latex.set_defaults(func=cmd_latex)

    return parser


def main(argv: list[str] | None = None) -> int:
    _ensure_utf8_console()
    from manim_studio.latex import apply_runtime_path

    apply_runtime_path()
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


def gui_main() -> int:
    """Entry point for the no-console ``manim-studio-gui`` script."""
    return main(["gui", *sys.argv[1:]])


if __name__ == "__main__":
    raise SystemExit(main())
