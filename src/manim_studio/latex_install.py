"""On-demand portable LaTeX installer (TinyTeX, user-local, no admin).

No Qt, no manimlib imports here: the editor drives this from a ``QThread``
worker, the CLI drives it directly, and unit tests inject a local ``fetcher``
so no network is needed.  Downloads are pinned TinyTeX release bundles over
HTTPS, verified against published SHA-256 digests.
"""

from __future__ import annotations

import hashlib
import logging
import platform
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from manim_studio.latex import (
    MANIM_TEX_PACKAGES,
    bin_dir,
    download_cache_dir,
    install_dir,
    runtime_env,
)

if TYPE_CHECKING:
    from collections.abc import Callable

__all__ = [
    "TINYTEX_SOURCES",
    "TINYTEX_VERSION",
    "InstallError",
    "InstallProgress",
    "ProgressCallback",
    "TinytexSource",
    "clear_download_cache",
    "install_latex",
    "installed_marker",
    "is_installed",
    "tinytex_source",
    "uninstall_latex",
]

log = logging.getLogger(__name__)

#: Pinned TinyTeX release.  Bump together with ``TINYTEX_SOURCES`` digests.
#: One line to update when a new monthly release is adopted.
TINYTEX_VERSION = "v2026.10"

_RELEASE_BASE = f"https://github.com/rstudio/tinytex-releases/releases/download/{TINYTEX_VERSION}"


@dataclass(frozen=True, slots=True)
class TinytexSource:
    """One pinned download bundle: filename, URL, digest and unbundle method."""

    filename: str
    url: str
    sha256: str
    kind: str  # "sfx-exe" (self-extracting, run with -y) or "archive"


#: Pinned TinyTeX-1 bundles per platform.  Digests are the ``sha256:`` values
#: published on the release page; the installer refuses a mismatching file.
#: Windows ships a self-extracting exe (``exe -y`` extracts ``TinyTeX/`` with
#: no installer UI and no admin); macOS/Linux ship ``.tar.xz`` trees.
TINYTEX_SOURCES: dict[str, TinytexSource] = {
    "win32": TinytexSource(
        filename=f"TinyTeX-1-windows-{TINYTEX_VERSION}.exe",
        url=f"{_RELEASE_BASE}/TinyTeX-1-windows-{TINYTEX_VERSION}.exe",
        sha256="36c329c644683d981a668419d13c8804696e2816ae4d287ad755788fee231a31",
        kind="sfx-exe",
    ),
    "darwin": TinytexSource(
        filename=f"TinyTeX-1-darwin-{TINYTEX_VERSION}.tar.xz",
        url=f"{_RELEASE_BASE}/TinyTeX-1-darwin-{TINYTEX_VERSION}.tar.xz",
        sha256="e40c294efae81026d8cc8d12c09d2dd456d268efe7fe092410478bc1973fd40f",
        kind="archive",
    ),
    "linux-x86_64": TinytexSource(
        filename=f"TinyTeX-1-linux-x86_64-{TINYTEX_VERSION}.tar.xz",
        url=f"{_RELEASE_BASE}/TinyTeX-1-linux-x86_64-{TINYTEX_VERSION}.tar.xz",
        sha256="4d519d6236ee6798e3ec0d8e2093d1fda01aeb267aa22c2a5586d76bcfce6566",
        kind="archive",
    ),
    "linux-aarch64": TinytexSource(
        filename=f"TinyTeX-1-linux-arm64-{TINYTEX_VERSION}.tar.xz",
        url=f"{_RELEASE_BASE}/TinyTeX-1-linux-arm64-{TINYTEX_VERSION}.tar.xz",
        sha256="1bc5465c7a7c6f13e13c1b54a52147577eb3f389033af0444bc097698eb6653f",
        kind="archive",
    ),
    "linuxmusl-x86_64": TinytexSource(
        filename=f"TinyTeX-1-linuxmusl-x86_64-{TINYTEX_VERSION}.tar.xz",
        url=f"{_RELEASE_BASE}/TinyTeX-1-linuxmusl-x86_64-{TINYTEX_VERSION}.tar.xz",
        sha256="118ddae121d25998625799690c732ebf49e583d0a86ebf27d45b52c2a4d4deeb",
        kind="archive",
    ),
}

_MARKER = ".manim-studio-latex-installed"

#: Download attempts (the official installer also retries aggressively).
_DOWNLOAD_ATTEMPTS = 3
_RETRY_DELAY_SECONDS = 5.0


class InstallError(Exception):
    """Raised when the LaTeX download/install cannot complete."""


@dataclass(frozen=True, slots=True)
class InstallProgress:
    """Progress snapshot passed to UI/CLI callbacks."""

    stage: str
    downloaded_bytes: int = 0
    total_bytes: int | None = None

    @property
    def fraction(self) -> float | None:
        if self.total_bytes:
            return min(1.0, self.downloaded_bytes / self.total_bytes)
        return None


if TYPE_CHECKING:
    ProgressCallback = Callable[[InstallProgress], None]
    Fetcher = Callable[[str, Path, ProgressCallback | None, Any], None]
else:
    ProgressCallback = Any  # type: ignore[no-redef]
    Fetcher = Any  # type: ignore[no-redef]


def tinytex_source() -> TinytexSource:
    """Pinned TinyTeX bundle for the current platform."""
    if sys.platform == "win32":
        return TINYTEX_SOURCES["win32"]
    if sys.platform == "darwin":
        return TINYTEX_SOURCES["darwin"]
    machine = platform.machine().lower()
    arch = "aarch64" if machine in ("aarch64", "arm64") else "x86_64"
    libc, _ = platform.libc_ver()
    if libc.lower() == "musl" and arch == "x86_64":
        return TINYTEX_SOURCES["linuxmusl-x86_64"]
    return TINYTEX_SOURCES[f"linux-{arch}"]


def installed_marker(target: str | Path | None = None) -> Path:
    return install_dir(target) / _MARKER


def is_installed(target: str | Path | None = None) -> bool:
    return installed_marker(target).is_file()


def uninstall_latex(target: str | Path | None = None) -> bool:
    """Remove the user-local LaTeX tree.  Returns True when something was removed."""
    root = install_dir(target)
    if not root.exists():
        return False
    shutil.rmtree(root, ignore_errors=False)
    return True


def install_latex(
    target: str | Path | None = None,
    *,
    on_progress: ProgressCallback | None = None,
    cancel: Any = None,
    fetcher: Fetcher | None = None,
    packages: tuple[str, ...] = MANIM_TEX_PACKAGES,
    skip_tlmgr: bool = False,
    kind: str | None = None,
    use_cache: bool = True,
) -> Path:
    """Download + unbundle TinyTeX and install manimgl's TeX packages.

    ``fetcher`` may replace the network step in tests: it receives
    ``(url, dest_file, on_progress, cancel)`` and must write the bundle.
    ``kind`` overrides the platform's unbundle method (tests use ``"archive"``
    with locally built zips/tars).  A previous install at ``target`` is
    replaced.  Raises :class:`InstallError` on failure; never touches the
    system LaTeX.
    """
    root = install_dir(target)
    source = tinytex_source()
    _check_cancelled(cancel)

    with tempfile.TemporaryDirectory(prefix="manim-studio-latex-") as tmp:
        bundle = Path(tmp) / source.filename
        # Only the real network path is cached; tests inject a fetcher.
        cache = download_cache_dir(target) if fetcher is None and use_cache else None
        if cache is not None and _restore_from_cache(cache, source, bundle):
            _emit(on_progress, InstallProgress(stage="cached"))
        else:
            _emit(on_progress, InstallProgress(stage="download"))
            fetch = fetcher or download_file
            try:
                fetch(source.url, bundle, on_progress, cancel)
            except InstallError:
                raise
            except Exception as exc:
                raise InstallError(f"download failed: {exc}") from exc
            if fetcher is None:
                # Pinned release bundle: refuse anything that fails verification.
                try:
                    verify_sha256(bundle, source.sha256)
                except InstallError as exc:
                    raise InstallError(f"{exc} (re-download from {source.url} to retry)") from exc
                if cache is not None:
                    _save_to_cache(cache, source, bundle)
        _check_cancelled(cancel)
        _emit(on_progress, InstallProgress(stage="extract"))
        # Fresh tree: drop any stale/partial previous install first.
        if root.exists():
            shutil.rmtree(root)
        root.mkdir(parents=True, exist_ok=True)
        try:
            if (kind or source.kind) == "sfx-exe":
                _extract_sfx_exe(bundle, root, cancel)
            else:
                _extract_archive(bundle, root, cancel)
        except InstallError:
            raise
        except Exception as exc:
            raise InstallError(f"extract failed: {exc}") from exc

    if not skip_tlmgr and packages:
        _emit(on_progress, InstallProgress(stage="packages"))
        _tlmgr_install(root, packages, cancel)

    installed_marker(root).write_text(f"{source.filename}\n", encoding="utf-8")
    _emit(on_progress, InstallProgress(stage="done"))
    log.info("LaTeX installed to %s", root)
    return root


def _restore_from_cache(cache: Path, source: TinytexSource, dest: Path) -> bool:
    """Copy a verified cached bundle to ``dest``; drop a corrupt entry."""
    cached = cache / source.filename
    try:
        if not cached.is_file():
            return False
        verify_sha256(cached, source.sha256)
        shutil.copyfile(cached, dest)
    except InstallError:
        log.warning("cached %s is corrupt; re-downloading", cached.name)
        cached.unlink(missing_ok=True)
        return False
    except OSError as exc:
        log.warning("cannot use download cache: %s", exc)
        return False
    log.info("using cached %s", cached)
    return True


def _save_to_cache(cache: Path, source: TinytexSource, bundle: Path) -> None:
    """Store a verified bundle atomically and prune other versions; never raises."""
    try:
        cache.mkdir(parents=True, exist_ok=True)
        partial = cache / f"{source.filename}.part"
        shutil.copyfile(bundle, partial)
        partial.replace(cache / source.filename)
        for stale in cache.iterdir():
            if stale.name != source.filename and stale.name.startswith("TinyTeX-"):
                stale.unlink(missing_ok=True)
    except OSError as exc:
        log.warning("cannot write download cache: %s", exc)


def clear_download_cache(target: str | Path | None = None) -> bool:
    """Delete cached TinyTeX bundles.  Returns True when something was removed."""
    cache = download_cache_dir(target)
    if not cache.exists():
        return False
    shutil.rmtree(cache, ignore_errors=True)
    return True


def download_file(
    url: str,
    dest: str | Path,
    on_progress: ProgressCallback | None = None,
    cancel: Any = None,
    *,
    timeout: float = 60.0,
    attempts: int = _DOWNLOAD_ATTEMPTS,
) -> None:
    """Stream ``url`` to ``dest`` with retries, progress + cancellation.

    Retries transient failures (like the official installer's
    ``curl --retry``); a 404 or other HTTP error fails fast with the URL so
    the user can fetch the bundle manually.
    """
    destination = Path(dest)
    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            _download_once(url, destination, on_progress, cancel, timeout=timeout)
            return
        except InstallError:
            raise
        except Exception as exc:
            last_error = exc
            leftover = destination if destination.is_file() else None
            if leftover is not None:
                leftover.unlink(missing_ok=True)
            if attempt < attempts:
                log.warning("download attempt %d/%d failed: %s", attempt, attempts, exc)
                _sleep_retry(cancel, attempt)
    raise InstallError(f"download failed for {url}: {last_error}")


def _download_once(
    url: str,
    destination: Path,
    on_progress: ProgressCallback | None,
    cancel: Any,
    *,
    timeout: float,
) -> None:
    request = urllib.request.Request(url, headers={"User-Agent": "manim-studio"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        total = response.getheader("Content-Length")
        total_bytes = int(total) if total and total.isdigit() else None
        downloaded = 0
        with destination.open("wb") as handle:
            while True:
                _check_cancelled(cancel)
                chunk = response.read(256 * 1024)
                if not chunk:
                    break
                handle.write(chunk)
                downloaded += len(chunk)
                _emit(
                    on_progress,
                    InstallProgress(
                        stage="download",
                        downloaded_bytes=downloaded,
                        total_bytes=total_bytes,
                    ),
                )


def _sleep_retry(cancel: Any, attempt: int) -> None:
    """Wait between attempts; still honors cancellation."""
    deadline = time.monotonic() + _RETRY_DELAY_SECONDS * attempt
    while time.monotonic() < deadline:
        _check_cancelled(cancel)
        time.sleep(0.2)


def verify_sha256(path: str | Path, expected: str) -> None:
    """Raise :class:`InstallError` when the file hash does not match."""
    digest = hashlib.sha256(Path(path).read_bytes()).hexdigest()
    if digest.lower() != expected.lower():
        raise InstallError("checksum mismatch — download may be corrupted")


def _extract_sfx_exe(bundle: Path, root: Path, cancel: Any = None) -> None:
    """Unbundle a TinyTeX self-extracting exe (``exe -y`` needs no UI/admin).

    The exe extracts a ``TinyTeX/`` tree into its working directory, so it is
    run inside a staging dir and the tree is then moved to ``root``.
    """
    _check_cancelled(cancel)
    with tempfile.TemporaryDirectory(prefix="manim-studio-latex-sfx-") as staging:
        try:
            process = subprocess.run(
                [str(bundle), "-y"],
                capture_output=True,
                text=True,
                timeout=10 * 60,
                cwd=staging,
            )
        except subprocess.TimeoutExpired as exc:
            raise InstallError("TinyTeX extractor timed out") from exc
        except OSError as exc:
            raise InstallError(f"cannot run TinyTeX extractor: {exc}") from exc
        _check_cancelled(cancel)
        if process.returncode != 0:
            detail = (process.stderr or process.stdout or "").strip().splitlines()
            raise InstallError(
                "TinyTeX extractor failed: " + " | ".join(detail[-5:] or ["unknown error"])
            )
        tree = Path(staging) / "TinyTeX"
        if not tree.is_dir():
            raise InstallError("TinyTeX extractor produced no TinyTeX/ tree")
        for child in tree.iterdir():
            shutil.move(str(child), root / child.name)


def _extract_archive(archive: Path, root: Path, cancel: Any = None) -> None:
    _check_cancelled(cancel)
    # Sniff content first: the bundle filename follows the platform release,
    # so a locally fetched test bundle may carry a different extension.
    if zipfile.is_zipfile(archive):
        with zipfile.ZipFile(archive) as bundle:
            bundle.extractall(root)
    elif tarfile.is_tarfile(archive):
        with tarfile.open(archive, "r:*") as bundle:
            _safe_extract(bundle, root)
    else:
        raise InstallError(f"unsupported archive {archive.name}")
    # TinyTeX archives nest one top-level dir; collapse it so bin/ is at root.
    children = [child for child in root.iterdir() if child.name != _MARKER]
    if len(children) == 1 and children[0].is_dir():
        nested = children[0]
        if (nested / "bin").is_dir() or (nested / "tlpkg").is_dir():
            for child in nested.iterdir():
                shutil.move(str(child), root / child.name)
            nested.rmdir()


def _safe_extract(bundle: tarfile.TarFile, root: Path) -> None:
    for member in bundle.getmembers():
        target = (root / member.name).resolve()
        if not str(target).startswith(str(root.resolve())):
            raise InstallError(f"unsafe archive entry: {member.name}")
    bundle.extractall(root)


def _tlmgr_install(root: Path, packages: tuple[str, ...], cancel: Any = None) -> None:
    tlmgr = _find_tlmgr(root)
    if tlmgr is None:
        raise InstallError("tlmgr not found in the downloaded LaTeX distribution")
    _check_cancelled(cancel)
    # A fresh TinyTeX often bundles a tlmgr older than the package
    # repository, in which case every install refuses with "tlmgr itself
    # needs to be updated".  Self-update first so the install below works.
    _tlmgr_run(tlmgr, ("update", "--self"), cancel, purpose="tlmgr self-update")
    _tlmgr_run(tlmgr, ("install", *packages), cancel, purpose="tlmgr install")
    _tlmgr_verify(tlmgr, packages, cancel)


def _tlmgr_run(
    tlmgr: Path, args: tuple[str, ...], cancel: Any, *, purpose: str, timeout: float = 20 * 60
) -> str:
    """Run tlmgr, raising :class:`InstallError` (with the log tail) on failure."""
    _check_cancelled(cancel)
    env = runtime_env()
    try:
        process = subprocess.run(
            [str(tlmgr), *args],
            capture_output=True,
            text=True,
            timeout=timeout,
            env=env,
        )
    except Exception as exc:
        raise InstallError(f"{purpose} failed: {exc}") from exc
    if process.returncode != 0:
        tail = (process.stderr or process.stdout or "").strip().splitlines()[-10:]
        raise InstallError(f"{purpose} failed: " + " | ".join(tail))
    return process.stdout or ""


def _tlmgr_verify(tlmgr: Path, packages: tuple[str, ...], cancel: Any = None) -> None:
    """Confirm tlmgr really installed every package.

    tlmgr can report success while installing nothing (e.g. a pending
    self-update aborts the run but the wrapper still exits 0), which used to
    leave a "ready" install that could not compile.  Never trust the exit
    code alone: ask tlmgr what is actually installed.
    """
    output = _tlmgr_run(tlmgr, ("info", *packages), cancel, purpose="tlmgr verify")
    missing = _missing_from_info(output, packages)
    if missing:
        raise InstallError(
            "tlmgr reported success but these packages are still missing: "
            + ", ".join(missing)
            + ". Retry the install or check the package repository."
        )


def _missing_from_info(info_text: str, packages: tuple[str, ...]) -> tuple[str, ...]:
    """Parse ``tlmgr info`` output into the packages not marked installed."""
    installed: dict[str, bool] = {}
    current: str | None = None
    for line in info_text.splitlines():
        stripped = line.strip()
        if stripped.startswith("package:"):
            current = stripped.split(":", 1)[1].strip()
        elif stripped.startswith("installed:") and current is not None:
            installed[current] = stripped.split(":", 1)[1].strip().lower() == "yes"
    return tuple(name for name in packages if not installed.get(name, False))


def _find_tlmgr(root: Path) -> Path | None:
    from manim_studio.latex import universal_bin_subdir

    for candidate in (
        root / "bin" / "windows" / "tlmgr.bat",
        root / "bin" / "win32" / "tlmgr.bat",
        root / "bin" / universal_bin_subdir() / "tlmgr",
        root / "bin" / "tlmgr",
    ):
        if candidate.is_file():
            return candidate
    found = shutil.which("tlmgr", path=str(bin_dir(root)))
    return Path(found) if found else None


def _emit(callback: ProgressCallback | None, progress: InstallProgress) -> None:
    if callback is not None:
        callback(progress)


def _check_cancelled(cancel: Any) -> None:
    if cancel is None:
        return
    flagged = bool(cancel() if callable(cancel) else getattr(cancel, "is_set", lambda: False)())
    if flagged:
        raise InstallError("installation cancelled")
