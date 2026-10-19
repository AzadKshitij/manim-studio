"""FFmpeg for video export: find it, or install a checksum-verified copy for this user.

Export shells out to ``ffmpeg`` (manimgl encodes through it). The app looks for ffmpeg in its own
folder first, then on ``PATH``. ``manim-studio ffmpeg install`` can fetch the Windows "essentials"
build from the publisher (gyan.dev), check the download against the publisher's own SHA-256 file,
and unpack it under the user's data folder. No admin rights are needed. macOS and Linux get the
package-manager command instead of a download, because the app does not guess at system packages.

The download, retry, cancel and checksum helpers are the ones the LaTeX installer uses.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from manim_studio.latex_install import (
    InstallError,
    ProgressCallback,
    download_file,
    verify_sha256,
)

__all__ = [
    "ARCHIVE_NAME",
    "PUBLISHER_BASE_URL",
    "FfmpegInstallError",
    "FfmpegStatus",
    "bundled_bin_dir",
    "bundled_executable",
    "ffmpeg_status",
    "install_ffmpeg",
    "install_guidance",
    "install_root",
]

PUBLISHER_BASE_URL = "https://www.gyan.dev/ffmpeg/builds/"
ARCHIVE_NAME = "ffmpeg-release-essentials.zip"
MARKER_NAME = ".manim-studio-ffmpeg-installed"
#: Shown to the user before a download: the size is the publisher's, and changes per release.
APPROX_DOWNLOAD_MB = 110

_DIGEST = re.compile(r"^[0-9a-fA-F]{64}$")
_TEXT_TIMEOUT = 30.0
_PROBE_TIMEOUT = 60.0


class FfmpegInstallError(RuntimeError):
    """ffmpeg could not be installed; the message says what to do next."""


@dataclass(frozen=True, slots=True)
class FfmpegStatus:
    """Where ffmpeg is, if anywhere: the app's own copy, a copy on PATH, or nothing."""

    found: bool
    source: str  #: "bundled" | "path" | "none"
    path: str | None

    def to_dict(self) -> dict[str, Any]:
        return {"found": self.found, "source": self.source, "path": self.path}


def _executable_name() -> str:
    return "ffmpeg.exe" if sys.platform == "win32" else "ffmpeg"


def install_root() -> Path:
    """The folder the app unpacks ffmpeg into (per user)."""
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA")
        root = Path(base) if base else Path.home() / "AppData" / "Local"
        return root / "manim-studio" / "ffmpeg"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "manim-studio" / "ffmpeg"
    base = os.environ.get("XDG_DATA_HOME")
    root = Path(base) if base else Path.home() / ".local" / "share"
    return root / "manim-studio" / "ffmpeg"


def bundled_executable(root: Path | None = None) -> Path | None:
    """The ffmpeg executable inside the app's folder, or None when it is not installed there."""
    folder = root if root is not None else install_root()
    if not folder.is_dir():
        return None
    name = _executable_name()
    # The archive unpacks as <build-name>/bin/ffmpeg.exe; a flat layout is accepted too.
    for candidate in (*folder.glob(f"*/bin/{name}"), *folder.glob(f"bin/{name}")):
        if candidate.is_file():
            return candidate
    return None


def bundled_bin_dir() -> Path | None:
    """The folder holding the app's own ffmpeg, for putting on PATH; None when not installed."""
    executable = bundled_executable()
    return executable.parent if executable is not None else None


def ffmpeg_status() -> FfmpegStatus:
    """Detect ffmpeg: the app's own copy first, then PATH."""
    bundled = bundled_executable()
    if bundled is not None:
        return FfmpegStatus(True, "bundled", str(bundled))
    on_path = shutil.which("ffmpeg")
    if on_path is not None:
        return FfmpegStatus(True, "path", on_path)
    return FfmpegStatus(False, "none", None)


def install_guidance() -> str:
    """What to do when ffmpeg is missing and this platform cannot install it automatically."""
    if sys.platform == "darwin":
        return "install it with Homebrew: brew install ffmpeg"
    if sys.platform == "win32":
        return "run: manim-studio ffmpeg install"
    return "install it with your package manager, for example: sudo apt install ffmpeg"


def _fetch_text(url: str) -> str:
    request = urllib.request.Request(url, headers={"User-Agent": "manim-studio"})
    try:
        with urllib.request.urlopen(request, timeout=_TEXT_TIMEOUT) as response:
            return response.read().decode("utf-8", "replace")
    except OSError as exc:
        raise FfmpegInstallError(f"could not read {url}: {exc}") from exc


def _published_digest(text: str) -> str:
    """The first token of a publisher's ``.sha256`` file, which must be a SHA-256 hex digest."""
    tokens = text.split()
    if not tokens or not _DIGEST.match(tokens[0]):
        raise FfmpegInstallError("the publisher's checksum file did not contain a SHA-256 digest")
    return tokens[0].lower()


def _check_member_names(archive: zipfile.ZipFile, destination: Path) -> None:
    """Refuse archives whose entries would land outside the destination folder."""
    root = destination.resolve()
    for name in archive.namelist():
        target = (destination / name).resolve()
        if not target.is_relative_to(root):
            raise FfmpegInstallError(f"refusing archive entry outside the install folder: {name}")


def _probe(executable: Path) -> str:
    """Run ``ffmpeg -version`` so a broken copy is caught before it is kept."""
    try:
        completed = subprocess.run(
            [str(executable), "-version"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            stdin=subprocess.DEVNULL,
            timeout=_PROBE_TIMEOUT,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise FfmpegInstallError(f"the installed ffmpeg does not run: {exc}") from exc
    if completed.returncode != 0:
        raise FfmpegInstallError(f"the installed ffmpeg exited with code {completed.returncode}")
    return completed.stdout.splitlines()[0] if completed.stdout else ""


def install_ffmpeg(
    *,
    on_progress: ProgressCallback | None = None,
    cancel: Any = None,
    root: Path | None = None,
) -> Path:
    """Download, verify and unpack the Windows essentials build; returns its ``ffmpeg.exe``.

    The archive is checked against the SHA-256 that the publisher lists next to it. A mismatch,
    a broken archive or a copy that does not run leaves nothing installed. Raises
    :class:`FfmpegInstallError` with the reason.
    """
    if sys.platform != "win32":
        raise FfmpegInstallError(f"automatic install is for Windows only; {install_guidance()}")
    destination = root if root is not None else install_root()
    existing = bundled_executable(destination)
    if existing is not None:
        return existing

    archive_url = PUBLISHER_BASE_URL + ARCHIVE_NAME
    digest = _published_digest(_fetch_text(archive_url + ".sha256"))
    destination.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix="ffmpeg-", dir=destination.parent))
    try:
        archive = staging / ARCHIVE_NAME
        try:
            download_file(archive_url, archive, on_progress, cancel)
            verify_sha256(archive, digest)
        except InstallError as exc:
            raise FfmpegInstallError(str(exc)) from exc
        unpacked = staging / "unpacked"
        try:
            with zipfile.ZipFile(archive) as bundle:
                _check_member_names(bundle, unpacked)
                bundle.extractall(unpacked)
        except zipfile.BadZipFile as exc:
            raise FfmpegInstallError(f"the downloaded archive is not a zip file: {exc}") from exc
        executable = bundled_executable(unpacked)
        if executable is None:
            raise FfmpegInstallError("the archive has no bin/ffmpeg.exe")
        version = _probe(executable)
        if destination.exists():
            shutil.rmtree(destination)
        unpacked.rename(destination)
        (destination / MARKER_NAME).write_text(version + "\n", encoding="utf-8")
        installed = bundled_executable(destination)
        if installed is None:  # pragma: no cover - the same layout was just checked
            raise FfmpegInstallError("the installed copy is missing bin/ffmpeg.exe")
        return installed
    finally:
        shutil.rmtree(staging, ignore_errors=True)
