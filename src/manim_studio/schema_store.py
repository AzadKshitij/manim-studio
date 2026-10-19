"""The manimlib mobject schema without Qt: a disk cache keyed by the installed manimlib version, and
a blocking load that runs the probe in its own process on a miss (ADR 0012, ADR 0013).

The editor keeps its own background loader in :mod:`manim_studio.editor.schema_cache`, which uses
this module's cache helpers. Agents and the MCP server call :func:`load_schema` directly, so this
module must not import Qt or manimlib.
"""

from __future__ import annotations

import importlib.metadata
import json
import logging
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from manim_studio.mobject_schema import MobjectSchema, SchemaError, parse_schema
from manim_studio.spawn import schema_probe_command

__all__ = [
    "CACHE_NAME",
    "SchemaUnavailable",
    "default_cache_path",
    "installed_manimlib_version",
    "load_schema",
    "read_cache",
    "write_cache",
]

log = logging.getLogger(__name__)

CACHE_NAME = "mobject_schema.json"
_DEFAULT_TIMEOUT = 120.0


class SchemaUnavailable(RuntimeError):
    """No schema could be read or probed; the message says why."""


def default_cache_path() -> Path:
    """Where a headless process caches the schema (the editor uses Qt's cache location).

    Windows: ``%LOCALAPPDATA%\\manim-studio\\cache``; macOS: ``~/Library/Caches/manim-studio``;
    elsewhere: ``$XDG_CACHE_HOME`` or ``~/.cache`` under ``manim-studio``.
    """
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA")
        root = Path(base) if base else Path.home() / "AppData" / "Local"
        directory = root / "manim-studio" / "cache"
    elif sys.platform == "darwin":
        directory = Path.home() / "Library" / "Caches" / "manim-studio"
    else:
        base = os.environ.get("XDG_CACHE_HOME")
        root = Path(base) if base else Path.home() / ".cache"
        directory = root / "manim-studio"
    return directory / CACHE_NAME


def installed_manimlib_version() -> str:
    """The installed ManimGL version, read without importing manimlib.

    ``"unknown"`` when the package metadata is missing (some frozen builds drop it).  The probe
    still decides whether manimlib imports, so a bundled manimlib works either way.
    """
    try:
        return importlib.metadata.version("manimgl")
    except importlib.metadata.PackageNotFoundError:
        return "unknown"


def read_cache(path: Path, version: str) -> MobjectSchema | None:
    """The cached schema when it is valid and was made for ``version``; otherwise ``None``."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        schema = parse_schema(data)
    except (OSError, ValueError, SchemaError):
        return None
    return schema if schema.manimlib_version == version else None


def write_cache(path: Path, data: dict[str, Any]) -> None:
    """Persist the probe's JSON.  Written beside the target and renamed, so a crash never leaves
    half a file behind."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_suffix(".tmp")
        temp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        temp.replace(path)
    except OSError:
        log.debug("could not persist the mobject schema to %s", path)


def load_schema(cache: Path | None = None, *, timeout: float = _DEFAULT_TIMEOUT) -> MobjectSchema:
    """The schema for the installed manimlib: from the cache when it is current, else by running
    the probe process and caching its output.

    Raises :class:`SchemaUnavailable` when the probe cannot run, fails, or prints something that is
    not a schema.
    """
    path = cache if cache is not None else default_cache_path()
    cached = read_cache(path, installed_manimlib_version())
    if cached is not None:
        return cached

    executable, args = schema_probe_command()
    try:
        completed = subprocess.run(
            [executable, *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            stdin=subprocess.DEVNULL,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise SchemaUnavailable(f"the manimlib probe timed out after {timeout:.0f}s") from exc
    except OSError as exc:
        raise SchemaUnavailable(f"could not start the manimlib probe: {exc}") from exc

    if completed.returncode != 0:
        lines = completed.stderr.strip().splitlines()
        detail = lines[-1] if lines else f"exit code {completed.returncode}"
        raise SchemaUnavailable(f"the manimlib probe failed: {detail}")
    try:
        data = json.loads(completed.stdout)
        schema = parse_schema(data)
    except (ValueError, SchemaError) as exc:
        raise SchemaUnavailable(f"the manimlib probe output was not usable: {exc}") from exc
    write_cache(path, data)
    return schema
