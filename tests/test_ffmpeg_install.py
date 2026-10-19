"""ffmpeg: detection, the checksum-verified install, the export guard and the CLI (no network)."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys
import zipfile
from pathlib import Path
from typing import Any

import pytest

from manim_studio import agent_tools, ffmpeg_install, latex
from manim_studio.cli import main
from manim_studio.export import ExportError, build_export_plan
from manim_studio.ffmpeg_install import (
    ARCHIVE_NAME,
    FfmpegInstallError,
    FfmpegStatus,
    bundled_executable,
    install_ffmpeg,
)

GOOD = {
    "ffmpeg-9.0.0-essentials_build/bin/ffmpeg.exe": b"MZ-fake-ffmpeg",
    "ffmpeg-9.0.0-essentials_build/LICENSE.txt": b"GPL",
}
NOT_FOUND = FfmpegStatus(False, "none", None)


def _archive(folder: Path, members: dict[str, bytes]) -> tuple[Path, str]:
    """A zip with ``members``, and the SHA-256 the publisher would list for it."""
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / ARCHIVE_NAME
    with zipfile.ZipFile(path, "w") as bundle:
        for name, data in members.items():
            bundle.writestr(name, data)
    return path, hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.fixture
def windows(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "platform", "win32")


@pytest.fixture
def publisher(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> dict[str, Any]:
    """Stands in for gyan.dev: a checksum file, a download, and a probe that always works."""
    archive, digest = _archive(tmp_path / "publisher", GOOD)
    state: dict[str, Any] = {"digest": digest, "archive": archive, "downloads": []}

    def fetch_text(url: str) -> str:
        return f"{state['digest']}  {ARCHIVE_NAME}\n"

    def download(url: str, dest: Path, on_progress: Any = None, cancel: Any = None) -> None:
        state["downloads"].append(url)
        shutil.copyfile(state["archive"], dest)

    monkeypatch.setattr(ffmpeg_install, "_fetch_text", fetch_text)
    monkeypatch.setattr(ffmpeg_install, "download_file", download)
    monkeypatch.setattr(ffmpeg_install, "_probe", lambda executable: "ffmpeg version fake")
    return state


class TestStatus:
    def test_none_when_nothing_is_installed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(ffmpeg_install, "bundled_executable", lambda root=None: None)
        monkeypatch.setattr(ffmpeg_install.shutil, "which", lambda name: None)
        assert ffmpeg_install.ffmpeg_status() == NOT_FOUND

    def test_a_copy_on_path_is_used_when_nothing_is_bundled(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(ffmpeg_install, "bundled_executable", lambda root=None: None)
        monkeypatch.setattr(ffmpeg_install.shutil, "which", lambda name: "/usr/bin/ffmpeg")
        assert ffmpeg_install.ffmpeg_status() == FfmpegStatus(True, "path", "/usr/bin/ffmpeg")

    def test_the_bundled_copy_wins_over_path(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        bundled = tmp_path / "ffmpeg.exe"
        monkeypatch.setattr(ffmpeg_install, "bundled_executable", lambda root=None: bundled)
        monkeypatch.setattr(ffmpeg_install.shutil, "which", lambda name: "/usr/bin/ffmpeg")
        assert ffmpeg_install.ffmpeg_status() == FfmpegStatus(True, "bundled", str(bundled))


class TestInstall:
    def test_installs_a_verified_copy_and_records_it(
        self, windows: None, publisher: dict[str, Any], tmp_path: Path
    ) -> None:
        root = tmp_path / "apps" / "ffmpeg"
        executable = install_ffmpeg(root=root)
        assert executable.name == "ffmpeg.exe"
        assert bundled_executable(root) == executable
        marker = root / ffmpeg_install.MARKER_NAME
        assert marker.read_text(encoding="utf-8") == "ffmpeg version fake\n"
        assert publisher["downloads"] == [ffmpeg_install.PUBLISHER_BASE_URL + ARCHIVE_NAME]

    def test_a_second_install_reuses_the_copy(
        self, windows: None, publisher: dict[str, Any], tmp_path: Path
    ) -> None:
        root = tmp_path / "ffmpeg"
        first = install_ffmpeg(root=root)
        second = install_ffmpeg(root=root)
        assert first == second
        assert len(publisher["downloads"]) == 1

    def test_a_checksum_mismatch_installs_nothing(
        self, windows: None, publisher: dict[str, Any], tmp_path: Path
    ) -> None:
        publisher["digest"] = "0" * 64
        root = tmp_path / "apps" / "ffmpeg"
        with pytest.raises(FfmpegInstallError, match="checksum mismatch"):
            install_ffmpeg(root=root)
        assert not root.exists()
        assert list((tmp_path / "apps").iterdir()) == []  # the staging folder is cleaned up

    def test_a_publisher_file_without_a_digest_is_refused(
        self, windows: None, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setattr(ffmpeg_install, "_fetch_text", lambda url: "<html>oops</html>")
        with pytest.raises(FfmpegInstallError, match="did not contain a SHA-256 digest"):
            install_ffmpeg(root=tmp_path / "ffmpeg")

    def test_an_archive_without_the_executable_is_refused(
        self, windows: None, publisher: dict[str, Any], tmp_path: Path
    ) -> None:
        archive, digest = _archive(tmp_path / "bare", {"ffmpeg-9/LICENSE.txt": b"GPL"})
        publisher["archive"], publisher["digest"] = archive, digest
        with pytest.raises(FfmpegInstallError, match=r"no bin/ffmpeg\.exe"):
            install_ffmpeg(root=tmp_path / "ffmpeg")

    def test_archive_entries_that_escape_the_folder_are_refused(
        self, windows: None, publisher: dict[str, Any], tmp_path: Path
    ) -> None:
        archive, digest = _archive(tmp_path / "evil", {"../escaped.txt": b"x"})
        publisher["archive"], publisher["digest"] = archive, digest
        with pytest.raises(FfmpegInstallError, match="outside the install folder"):
            install_ffmpeg(root=tmp_path / "apps" / "ffmpeg")
        assert not (tmp_path / "apps" / "escaped.txt").exists()

    def test_a_copy_that_does_not_run_is_not_kept(
        self,
        windows: None,
        publisher: dict[str, Any],
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        def broken(executable: Path) -> str:
            raise FfmpegInstallError("the installed ffmpeg does not run")

        monkeypatch.setattr(ffmpeg_install, "_probe", broken)
        root = tmp_path / "ffmpeg"
        with pytest.raises(FfmpegInstallError, match="does not run"):
            install_ffmpeg(root=root)
        assert not root.exists()

    def test_other_platforms_get_the_package_manager_instead(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setattr(sys, "platform", "darwin")
        with pytest.raises(FfmpegInstallError, match="brew"):
            install_ffmpeg(root=tmp_path / "ffmpeg")


class TestExportGuard:
    def test_video_export_refuses_without_ffmpeg(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        scene = tmp_path / "scene.py"
        scene.write_text("x = 1\n", encoding="utf-8")
        monkeypatch.setattr("manim_studio.export.ffmpeg_status", lambda: NOT_FOUND)
        with pytest.raises(ExportError, match="needs ffmpeg"):
            build_export_plan(scene, quality="low")

    def test_a_still_does_not_need_ffmpeg(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        scene = tmp_path / "scene.py"
        scene.write_text("x = 1\n", encoding="utf-8")
        monkeypatch.setattr("manim_studio.export.ffmpeg_status", lambda: NOT_FOUND)
        assert build_export_plan(scene, quality="low", still=True).still

    def test_the_agent_check_reports_ffmpeg(self) -> None:
        payload = agent_tools.check(Path(__file__).parent / "scenes", "feature_showcase.py")
        assert set(payload["ffmpeg"]) == {"found", "source", "path"}


class TestRuntimePath:
    def test_the_bundled_ffmpeg_goes_on_path_ahead_of_the_rest(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        # The LaTeX folder is prepended first, so ffmpeg sits just in front of the old PATH.
        bundled = tmp_path / "ffmpeg" / "bin"
        monkeypatch.setattr(ffmpeg_install, "bundled_bin_dir", lambda: bundled)
        parts = latex.runtime_env({"PATH": os.pathsep.join(["C:\\Windows", "C:\\Tools"])})[
            "PATH"
        ].split(os.pathsep)
        assert parts.index(str(bundled)) < parts.index("C:\\Windows")
        assert parts[-1] == "C:\\Tools"

    def test_no_bundled_ffmpeg_leaves_path_alone(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(ffmpeg_install, "bundled_bin_dir", lambda: None)
        env = latex.runtime_env({"PATH": "C:\\Tools"})
        assert str(ffmpeg_install.install_root()) not in env["PATH"]
        assert env["PATH"].endswith("C:\\Tools")


class _Terminal:
    def isatty(self) -> bool:
        return True


class TestCli:
    def test_status_as_json(self, monkeypatch: pytest.MonkeyPatch, capsys: Any) -> None:
        found = FfmpegStatus(True, "path", "C:/tools/ffmpeg.exe")
        monkeypatch.setattr(ffmpeg_install, "ffmpeg_status", lambda: found)
        assert main(["ffmpeg", "status", "--json"]) == 0
        assert json.loads(capsys.readouterr().out) == found.to_dict()

    def test_status_when_missing_exits_one(
        self, monkeypatch: pytest.MonkeyPatch, capsys: Any
    ) -> None:
        monkeypatch.setattr(ffmpeg_install, "ffmpeg_status", lambda: NOT_FOUND)
        assert main(["ffmpeg", "status"]) == 1
        assert "not found" in capsys.readouterr().out

    def test_install_without_yes_and_no_terminal_downloads_nothing(
        self, windows: None, monkeypatch: pytest.MonkeyPatch, capsys: Any
    ) -> None:
        monkeypatch.setattr(ffmpeg_install, "ffmpeg_status", lambda: NOT_FOUND)
        monkeypatch.setattr(sys, "stdin", None)

        def must_not_run(**kwargs: Any) -> Path:
            raise AssertionError("install ran without confirmation")

        monkeypatch.setattr(ffmpeg_install, "install_ffmpeg", must_not_run)
        assert main(["ffmpeg", "install"]) == 2
        assert "--yes" in capsys.readouterr().err

    def test_install_with_yes_installs(
        self, windows: None, monkeypatch: pytest.MonkeyPatch, capsys: Any
    ) -> None:
        monkeypatch.setattr(ffmpeg_install, "ffmpeg_status", lambda: NOT_FOUND)
        installed = Path("C:/apps/ffmpeg/bin/ffmpeg.exe")
        monkeypatch.setattr(ffmpeg_install, "install_ffmpeg", lambda **kwargs: installed)
        assert main(["ffmpeg", "install", "--yes"]) == 0
        assert f"installed ffmpeg: {installed}" in capsys.readouterr().out

    def test_an_answer_of_no_at_the_prompt_cancels(
        self, windows: None, monkeypatch: pytest.MonkeyPatch, capsys: Any
    ) -> None:
        monkeypatch.setattr(ffmpeg_install, "ffmpeg_status", lambda: NOT_FOUND)
        monkeypatch.setattr(sys, "stdin", _Terminal())
        monkeypatch.setattr("builtins.input", lambda prompt="": "n")

        def must_not_run(**kwargs: Any) -> Path:
            raise AssertionError("install ran after the user said no")

        monkeypatch.setattr(ffmpeg_install, "install_ffmpeg", must_not_run)
        assert main(["ffmpeg", "install"]) == 1
        assert "nothing was downloaded" in capsys.readouterr().out

    def test_install_on_macos_points_to_homebrew(
        self, monkeypatch: pytest.MonkeyPatch, capsys: Any
    ) -> None:
        monkeypatch.setattr(ffmpeg_install, "ffmpeg_status", lambda: NOT_FOUND)
        monkeypatch.setattr(sys, "platform", "darwin")
        assert main(["ffmpeg", "install", "--yes"]) == 1
        assert "brew install ffmpeg" in capsys.readouterr().err
