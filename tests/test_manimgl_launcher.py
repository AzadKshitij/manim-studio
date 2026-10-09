"""Unit tests for the export launcher's retiming argument handling."""

from __future__ import annotations

from pathlib import Path

import pytest

from manim_studio.manimgl_launcher import (
    _consume_patch_arg,
    _consume_timing_arg,
    _install_patched_loader,
    _load_factors,
)
from manim_studio.patches import Anchor, SetDuration
from manim_studio.sidecar import save_sidecar, sidecar_path
from manim_studio.site_index import index_source


class TestConsumeTimingArg:
    def test_space_separated_form_is_consumed(self, tmp_path: Path) -> None:
        config = tmp_path / "timing.json"
        config.write_text('{"0": 2.0, "3": 0.5}', encoding="utf-8")
        argv = ["prog", "scene.py", "--timing-file", str(config), "-w"]
        assert _consume_timing_arg(argv) == {0: 2.0, 3: 0.5}
        assert argv == ["prog", "scene.py", "-w"]  # both tokens removed

    def test_equals_form_is_consumed(self, tmp_path: Path) -> None:
        config = tmp_path / "timing.json"
        config.write_text('{"1": 1.5}', encoding="utf-8")
        argv = ["prog", f"--timing-file={config}", "-w"]
        assert _consume_timing_arg(argv) == {1: 1.5}
        assert argv == ["prog", "-w"]

    def test_absent_flag_returns_empty(self) -> None:
        assert _consume_timing_arg(["prog", "scene.py", "-w"]) == {}

    def test_flag_without_value_returns_empty(self) -> None:
        argv = ["prog", "--timing-file"]
        assert _consume_timing_arg(argv) == {}


class TestLoadFactors:
    def test_missing_file_returns_empty(self, tmp_path: Path) -> None:
        assert _load_factors(tmp_path / "nope.json") == {}

    def test_invalid_json_returns_empty(self, tmp_path: Path) -> None:
        config = tmp_path / "bad.json"
        config.write_text("{nope", encoding="utf-8")
        assert _load_factors(config) == {}

    def test_non_dict_returns_empty(self, tmp_path: Path) -> None:
        config = tmp_path / "list.json"
        config.write_text("[1, 2, 3]", encoding="utf-8")
        assert _load_factors(config) == {}

    def test_non_numeric_entries_are_skipped(self, tmp_path: Path) -> None:
        config = tmp_path / "mixed.json"
        config.write_text('{"0": 2.0, "1": "nope"}', encoding="utf-8")
        assert _load_factors(config) == {0: 2.0}


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
