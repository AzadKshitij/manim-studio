"""Offline index of the installed ``manimlib`` API for autocomplete.

The index is built by AST-scanning the manimlib package on disk — never by
importing it — so the editor process stays free of manimgl imports and the scan
costs a few milliseconds.  Results are cached in memory and optionally on disk.
"""

from __future__ import annotations

import ast
import json
import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable

__all__ = [
    "ApiSymbol",
    "build_index",
    "find_manimlib_path",
    "index_names",
    "load_index",
    "save_index",
]

_SCAN_RELATIVE_DIRS = ("mobject", "animation", "scene", "utils", "camera", "event_handler")
_MAX_SYMBOLS = 4000


@dataclass(frozen=True, slots=True)
class ApiSymbol:
    """One public class or function exported by manimlib."""

    name: str
    kind: str  # "class" | "function"
    module: str
    lineno: int

    def to_dict(self) -> dict[str, str | int]:
        return {"name": self.name, "kind": self.kind, "module": self.module, "lineno": self.lineno}


def find_manimlib_path() -> Path | None:
    """Locate the installed manimlib package without importing it."""
    import importlib.util

    # Frozen build: manimlib's sources live inside the PYZ archive where find_spec
    # sees no readable files, but the bundle also ships the .py sources as data so
    # this AST scan keeps working (ADR 0005).
    bundle_root = getattr(sys, "_MEIPASS", None)
    if bundle_root:
        bundled = Path(bundle_root) / "manimlib"
        if (bundled / "__init__.py").is_file():
            return bundled
    spec = importlib.util.find_spec("manimlib")
    if spec is None or not spec.submodule_search_locations:
        return None
    return Path(next(iter(spec.submodule_search_locations)))


def _iter_source_files(root: Path) -> Iterable[Path]:
    for relative in _SCAN_RELATIVE_DIRS:
        directory = root / relative
        if not directory.is_dir():
            continue
        yield from sorted(directory.rglob("*.py"))
    init = root / "__init__.py"
    if init.is_file():
        yield init


def build_index(manimlib_path: str | Path | None = None) -> tuple[ApiSymbol, ...]:
    """Scan manimlib and return the sorted, de-duplicated public API symbols."""
    root = Path(manimlib_path) if manimlib_path is not None else find_manimlib_path()
    if root is None or not root.is_dir():
        return ()
    symbols: dict[str, ApiSymbol] = {}
    for source_file in _iter_source_files(root):
        try:
            tree = ast.parse(source_file.read_text(encoding="utf-8", errors="replace"))
        except (SyntaxError, OSError):
            continue
        module = str(source_file.relative_to(root).with_suffix("")).replace(os.sep, ".")
        for node in tree.body:
            if isinstance(node, ast.ClassDef):
                name, kind = node.name, "class"
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                name, kind = node.name, "function"
            else:
                continue
            if name.startswith("_"):
                continue
            existing = symbols.get(name)
            if existing is not None and (existing.kind == "class" or kind == "function"):
                continue
            symbols[name] = ApiSymbol(name=name, kind=kind, module=module, lineno=node.lineno)
            if len(symbols) >= _MAX_SYMBOLS:
                break
    return tuple(sorted(symbols.values(), key=lambda symbol: symbol.name))


def index_names(symbols: Iterable[ApiSymbol]) -> tuple[str, ...]:
    """Just the dotted completion strings, sorted."""
    return tuple(sorted({symbol.name for symbol in symbols}))


def save_index(path: str | Path, symbols: Iterable[ApiSymbol]) -> None:
    """Persist an index to JSON (atomic write)."""
    payload = {
        "version": 1,
        "created_at": time.time(),
        "symbols": [symbol.to_dict() for symbol in symbols],
    }
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(target.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=1), encoding="utf-8")
    temporary.replace(target)


def load_index(path: str | Path) -> tuple[ApiSymbol, ...]:
    """Load a persisted index; returns ``()`` for missing/corrupt files."""
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ()
    if not isinstance(payload, dict) or payload.get("version") != 1:
        return ()
    try:
        return tuple(
            ApiSymbol(
                name=item["name"],
                kind=item["kind"],
                module=item["module"],
                lineno=int(item["lineno"]),
            )
            for item in payload["symbols"]
        )
    except (KeyError, TypeError, ValueError):
        return ()
