"""Unit tests for the export launcher's retiming argument handling."""

from __future__ import annotations

from pathlib import Path

import pytest

from manim_studio.manimgl_launcher import (
    _consume_patch_arg,
    _install_patched_loader,
)
from manim_studio.patches import Anchor, SetDuration
from manim_studio.sidecar import save_sidecar, sidecar_path
from manim_studio.site_index import index_source


class TestConsumePatchArg:
    def test_reads_sidecar_format_and_strips_the_flag(self, tmp_path: Path) -> None:
        patch = SetDuration(Anchor("S.construct:wait:abc:0"), 2.0)
        scene = tmp_path / "scene.py"
        save_sidecar(scene, [patch])
        argv = ["prog", "scene.py", "--patch-file", str(sidecar_path(scene)), "-w"]
        assert _consume_patch_arg(argv) == (patch,)
        assert argv == ["prog", "scene.py", "-w"]

    def test_equals_form(self, tmp_path: Path) -> None:
        patch = SetDuration(Anchor("S.construct:wait:abc:0"), 2.0)
        scene = tmp_path / "scene.py"
        save_sidecar(scene, [patch])
        argv = ["prog", f"--patch-file={sidecar_path(scene)}"]
        assert _consume_patch_arg(argv) == (patch,)
        assert argv == ["prog"]

    def test_absent_flag_means_no_patches(self) -> None:
        assert _consume_patch_arg(["prog", "scene.py"]) == ()

    def test_unreadable_file_is_ignored_with_a_warning(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        argv = ["prog", "--patch-file", str(tmp_path / "missing.json")]
        assert _consume_patch_arg(argv) == ()
        assert argv == ["prog"]
        assert "ignoring unreadable patch file" in capsys.readouterr().err


class TestInstallPatchedLoader:
    """The hook manimgl's ``ModuleLoader`` goes through to load the scene file."""

    SOURCE = (
        "from manimlib import Scene\n\n\n"
        "class Demo(Scene):\n"
        "    def construct(self):\n"
        "        self.wait(1)\n"
    )

    def _install(self, monkeypatch: pytest.MonkeyPatch, patches: tuple) -> object:
        from manimlib.module_loader import ModuleLoader

        # Registers the restore of the raw staticmethod.
        monkeypatch.setattr(ModuleLoader, "get_module", ModuleLoader.get_module)
        _install_patched_loader(patches)
        return ModuleLoader

    def test_scene_runs_as_patched_source(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        scene = tmp_path / "launcher_scene.py"
        scene.write_text(self.SOURCE, encoding="utf-8")
        (site,) = index_source(self.SOURCE).sites
        loader = self._install(monkeypatch, (SetDuration(Anchor(site.site_id), 4.0),))
        module = loader.get_module(str(scene))  # type: ignore[attr-defined]
        calls: list[float] = []

        class Recorder:
            def wait(self, duration: float = 1.0) -> None:
                calls.append(duration)

        module.Demo.construct(Recorder())
        assert calls == [4.0]
        assert scene.read_text(encoding="utf-8") == self.SOURCE

    def test_unrelated_patches_load_the_file_untouched(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        scene = tmp_path / "launcher_scene2.py"
        scene.write_text(self.SOURCE, encoding="utf-8")
        orphan = SetDuration(Anchor("Gone.construct:wait:dead000000:0"), 9.0)
        loader = self._install(monkeypatch, (orphan,))
        module = loader.get_module(str(scene))  # type: ignore[attr-defined]
        calls: list[float] = []

        class Recorder:
            def wait(self, duration: float = 1.0) -> None:
                calls.append(duration)

        module.Demo.construct(Recorder())
        assert calls == [1.0]
        assert "skipped a timeline edit" in capsys.readouterr().err
