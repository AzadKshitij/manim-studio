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
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from manim_editor import __version__
from manim_editor.api_index import build_index, index_names
from manim_editor.export import ExportError, build_export_plan, run_export
from manim_editor.scene_runner import (
    SceneLoadError,
    check_scene_file,
    render_still,
    run_trace,
)
from manim_editor.templates import (
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
    from manim_editor.preview_host import parse_size

    return parse_size(text)


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------


def cmd_check(args: argparse.Namespace) -> int:
    result = check_scene_file(args.file)
    if args.json:
        _print_json(result.to_dict())
    else:
        status = "OK" if result.ok else "FAIL"
        print(f"{status} {result.path}")
        for scene in result.scenes:
            print(f"  scene {scene.name} (line {scene.lineno})")
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
        from manim_editor.timeline_model import save_log

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
    try:
        plan = build_export_plan(
            args.file,
            args.scene,
            output=args.output,
            quality=args.quality,
            fps=args.fps,
            still=args.still,
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


def cmd_preview(args: argparse.Namespace) -> int:
    from manim_editor.preview_host import HostConfig, SceneHost

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
        from manim_editor.editor.app import run_editor
    except ImportError as exc:  # pragma: no cover - missing Qt install
        print(f"error: GUI dependencies unavailable: {exc}", file=sys.stderr)
        return 1
    return run_editor(
        str(args.file) if args.file else None,
        embedded=not args.external,
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
        prog="manim-editor",
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

    return parser


def main(argv: list[str] | None = None) -> int:
    _ensure_utf8_console()
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


def gui_main() -> int:
    """Entry point for the no-console ``manim-editor-gui`` script."""
    return main(["gui", *sys.argv[1:]])


if __name__ == "__main__":
    raise SystemExit(main())
