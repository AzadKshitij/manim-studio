"""Scene-file helpers: on-disk comparison, naming and template creation.

Qt-free helpers for the editor's file handling.  They live here rather than in
``main_window`` so that buffer-vs-disk equality (the save/reload dedup rule),
the scene naming rules and the preview-video path are unit-testable without a
window.
"""

from __future__ import annotations

import keyword
import re
from pathlib import Path

from manim_studio.templates import render_scene_template

__all__ = [
    "buffer_matches_disk",
    "create_scene_file",
    "format_seconds",
    "is_valid_scene_name",
    "normalize_newlines",
    "preview_video_path",
    "read_disk_text",
    "scene_name_from_path",
    "snake_case",
    "wait_insertion",
]


def normalize_newlines(text: str) -> str:
    """Normalise CRLF/CR line endings so buffer and disk text compare equal."""
    return text.replace("\r\n", "\n").replace("\r", "\n")


def read_disk_text(path: str | Path) -> str | None:
    """The file's text with LF line endings, or ``None`` when it cannot be read."""
    try:
        return normalize_newlines(Path(path).read_text(encoding="utf-8"))
    except OSError:
        return None


def buffer_matches_disk(path: str | Path, buffer_text: str) -> bool:
    """True when ``buffer_text`` holds exactly the code currently on disk."""
    disk = read_disk_text(path)
    return disk is not None and disk == buffer_text


def has_unsaved_changes(path: str | Path | None, buffer_text: str, modified: bool) -> bool:
    """True when replacing or closing the buffer would throw away work.

    An untitled buffer counts once it holds any text.  A saved scene counts only when
    the editor reports edits *and* the text differs from the file on disk, so retyping
    the saved code is not mistaken for a change.
    """
    if path is None:
        return bool(buffer_text.strip())
    return modified and not buffer_matches_disk(path, buffer_text)


def preview_video_path(scene_path: str | Path) -> Path:
    """Where the low-quality scrub video for ``scene_path`` is rendered."""
    path = Path(scene_path)
    return path.parent / ".manim-studio" / f"{path.stem}_preview.mp4"


def scene_name_from_path(path: str | Path) -> str:
    """``my_intro_scene.py`` -> ``MyIntroScene`` (falling back to ``MyScene``)."""
    name = "".join(part.capitalize() for part in Path(path).stem.replace("-", "_").split("_"))
    return name if name.isidentifier() else "MyScene"


def snake_case(name: str) -> str:
    """``MyIntroScene`` -> ``my_intro_scene``, for a suggested file name."""
    return re.sub(r"(?<!^)(?=[A-Z])", "_", name).lower()


def is_valid_scene_name(name: str) -> bool:
    """True when ``name`` can be used as the generated scene class name."""
    return bool(name) and name.isidentifier() and not keyword.iskeyword(name)


def create_scene_file(path: str | Path, template_key: str, scene_name: str | None = None) -> Path:
    """Write a scene template to ``path``, deriving the class name when omitted."""
    target = Path(path)
    name = scene_name or scene_name_from_path(target)
    target.write_text(render_scene_template(template_key, name), encoding="utf-8")
    return target


def format_seconds(seconds: float) -> str:
    """Format a duration as a Python literal (``0.5``, ``1.0``, ``0.25``)."""
    text = f"{max(0.0, float(seconds)):.3f}".rstrip("0")
    if text.endswith("."):
        text += "0"
    return text


def wait_insertion(
    source: str, line: int, duration: float, *, before: bool = True
) -> tuple[int, str]:
    """Plan a ``self.wait(duration)`` insertion adjacent to 1-based ``line``.

    Returns ``(insert_line, text)``: the 1-based line the new statement should
    occupy and that line's text, indented to match the reference line.  ``before``
    places the wait ahead of ``line`` (a hold at that boundary); otherwise it goes
    on the following line.
    """
    lines = source.split("\n")
    if not 1 <= line <= len(lines):
        raise ValueError(f"line {line} is outside the file")
    reference = lines[line - 1]
    indent = reference[: len(reference) - len(reference.lstrip())]
    text = f"{indent}self.wait({format_seconds(duration)})"
    return (line if before else line + 1), text
