"""Offscreen tests for the export dialog (options, validation, run flow).

The dialog never spawns processes itself — it hands plans to an
``ExportController``.  Plan-content tests build plans and inspect argv; flow
tests use the controller with ``python -c`` children so everything stays
deterministic and never touches manimgl.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from PySide6.QtWidgets import QComboBox

from manim_studio.editor.export_controller import ExportController
from manim_studio.editor.export_dialog import ExportDialog
from manim_studio.export import ExportPlan


def _scene_file(tmp_path: Path) -> Path:
    path = tmp_path / "demo_scene.py"
    path.write_text(
        "from manimlib import Scene\n\n\nclass DemoScene(Scene):\n    pass\n",
        encoding="utf-8",
    )
    return path


@pytest.fixture
def dialog(qtbot, tmp_path: Path):  # type: ignore[no-untyped-def]
    scene = _scene_file(tmp_path)
    dlg = ExportDialog(
        scene_path=scene,
        scene_names=("Alpha", "DemoScene"),
        current_scene="DemoScene",
        block_count=6,
        settings=None,
    )
    qtbot.addWidget(dlg)
    dlg.show()  # visibility assertions need a shown dialog
    return dlg


def _format_combo(dlg: ExportDialog) -> QComboBox:
    return dlg._format_combo  # type: ignore[no-any-return]


class TestInitialRange:
    def test_initial_range_preselects_the_blocks(self, qtbot, tmp_path: Path) -> None:
        scene = _scene_file(tmp_path)
        dlg = ExportDialog(
            scene_path=scene,
            scene_names=("DemoScene",),
            current_scene="DemoScene",
            block_count=6,
            settings=None,
            initial_range=(2, 3),
        )
        qtbot.addWidget(dlg)
        assert dlg._whole_scene_check.isChecked() is False
        assert dlg._animation_range() == (2, 3)

    def test_initial_range_is_clamped(self, qtbot, tmp_path: Path) -> None:
        scene = _scene_file(tmp_path)
        dlg = ExportDialog(
            scene_path=scene,
            scene_names=("DemoScene",),
            current_scene="DemoScene",
            block_count=3,
            settings=None,
            initial_range=(5, 9),
        )
        qtbot.addWidget(dlg)
        assert dlg._animation_range() == (2, 2)


class TestDefaults:
    def test_defaults(self, dialog: ExportDialog, tmp_path: Path) -> None:
        assert dialog._scene_combo.currentText() == "DemoScene"
        assert dialog._format_mode() == "mp4"
        assert dialog._quality() == "high"
        assert dialog._fps() is None  # 0 == scene default
        assert dialog._animation_range() is None  # whole scene
        assert dialog._resolution() is None
        assert dialog._path_edit.text() == str(tmp_path / "demo_scene.mp4")
        assert dialog._export_button.isEnabled()
        assert "--hd" in dialog._plan.command_line()  # type: ignore[union-attr]
        assert dialog._plan.still is False  # type: ignore[union-attr]
        assert dialog._plan.gif is False  # type: ignore[union-attr]

    def test_scene_index_falls_back_to_first(self, qtbot, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
        scene = _scene_file(tmp_path)
        dlg = ExportDialog(scene_path=scene, scene_names=("OnlyScene",), current_scene="Nope")
        qtbot.addWidget(dlg)
        assert dlg._scene_name() == "OnlyScene"


class TestFormatModes:
    def test_still_disables_fps_and_range(self, dialog: ExportDialog, tmp_path: Path) -> None:
        _format_combo(dialog).setCurrentIndex(2)  # PNG still
        assert dialog._is_still()
        assert not dialog._fps_spin.isEnabled()
        assert not dialog._range_widget.isEnabled()
        assert dialog._fps() is None
        assert dialog._animation_range() is None
        assert dialog._path_edit.text().endswith(".png")
        plan = dialog._plan
        assert plan is not None and plan.still is True
        assert "-s" in plan.argv
        assert "n" not in plan.argv

    def test_gif_uses_manimgl_flag(self, dialog: ExportDialog, tmp_path: Path) -> None:
        _format_combo(dialog).setCurrentIndex(1)  # GIF animation
        assert dialog._is_gif()
        assert dialog._path_edit.text().endswith(".gif")
        plan = dialog._plan
        assert plan is not None and plan.gif is True
        assert "-i" in plan.argv

    def test_manual_path_suffix_is_respected(self, dialog: ExportDialog, tmp_path: Path) -> None:
        custom = tmp_path / "final_cut.mp4"
        dialog._path_edit.setText(str(custom))
        assert dialog._plan is not None
        assert dialog._plan.output_path == custom

    def test_mismatched_suffix_disables_export(self, dialog: ExportDialog, tmp_path: Path) -> None:
        dialog._path_edit.setText(str(tmp_path / "out.png"))  # .png while format is mp4
        assert dialog._plan is None
        assert not dialog._export_button.isEnabled()
        assert "does not match the format" in dialog._summary_label.text()

    def test_empty_path_disables_export(self, dialog: ExportDialog) -> None:
        dialog._path_edit.setText("   ")
        assert dialog._plan is None
        assert not dialog._export_button.isEnabled()
        assert "choose an output path" in dialog._summary_label.text()


class TestResolutionPresets:
    def test_custom_resolution_flag(self, dialog: ExportDialog) -> None:
        dialog._resolution_combo.setCurrentIndex(4)  # Custom…
        assert dialog._custom_widget.isVisible()
        dialog._custom_width.setValue(360)
        dialog._custom_height.setValue(240)
        plan = dialog._plan
        assert plan is not None
        assert plan.resolution == (360, 240)
        assert "-r" in plan.argv and "360x240" in plan.argv
        assert "--hd" not in plan.argv  # preset flags suppressed for custom size

    def test_preset_quality_flag(self, dialog: ExportDialog) -> None:
        dialog._resolution_combo.setCurrentIndex(0)  # Low
        plan = dialog._plan
        assert plan is not None
        assert "-l" in plan.argv
        assert "--hd" not in plan.argv

    def test_out_of_range_resolution_invalidates(self, dialog: ExportDialog) -> None:
        dialog._resolution_combo.setCurrentIndex(4)  # Custom…
        dialog._custom_width.setValue(32)  # below min 64
        assert dialog._plan is None
        assert not dialog._export_button.isEnabled()


class TestAnimationRange:
    def test_range_produces_manimgl_flag(self, dialog: ExportDialog) -> None:
        dialog._whole_scene_check.setChecked(False)
        dialog._from_spin.setValue(2)
        dialog._to_spin.setValue(4)
        assert dialog._animation_range() == (2, 4)
        plan = dialog._plan
        assert plan is not None
        assert "-n" in plan.argv and "2,4" in plan.argv

    def test_backwards_range_invalidates(self, dialog: ExportDialog) -> None:
        dialog._whole_scene_check.setChecked(False)
        dialog._from_spin.setValue(4)
        dialog._to_spin.setValue(2)
        plan = dialog._plan
        assert plan is None
        assert not dialog._export_button.isEnabled()  # type: ignore[union-attr]


class TestRunFlow:
    def _wire_running_child(self, dialog: ExportDialog, monkeypatch: pytest.MonkeyPatch) -> None:
        def fake_plan(*args: object, **kwargs: object) -> ExportPlan:
            output = kwargs.get("output")
            return ExportPlan(
                argv=(sys.executable, "-c", "import time; time.sleep(30)"),
                output_path=Path(str(output)).with_suffix(".mp4") if output else Path("out.mp4"),
                scene_name="DemoScene",
                quality="high",
            )

        monkeypatch.setattr(dialog, "_plan_builder", fake_plan)
        dialog._refresh()  # rebuild the plan with the fake (a stale real plan would run manimgl)

    def test_export_runs_through_controller(
        self,
        qtbot,  # type: ignore[no-untyped-def]
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        scene = _scene_file(tmp_path)
        controller = ExportController()
        dlg = ExportDialog(
            scene_path=scene,
            scene_names=("DemoScene",),
            current_scene="DemoScene",
            block_count=3,
            settings=None,
            controller=controller,
        )
        qtbot.addWidget(dlg)
        dlg.show()
        dlg._plan_builder = lambda *a, **k: ExportPlan(  # type: ignore[method-assign]
            argv=(sys.executable, "-c", "pass"),
            output_path=tmp_path / "out.mp4",
            scene_name="DemoScene",
            quality="high",
        )
        dlg._refresh()  # rebuild the plan with the fake (a stale real plan would run manimgl)
        dlg._export_button.click()
        assert dlg._state == "running"
        assert dlg._cancel_button.isVisible()
        assert not dlg._close_button.isEnabled()
        qtbot.waitUntil(lambda: dlg._state == "done", timeout=10_000)
        assert dlg._succeeded is True
        assert dlg._result_row.isVisible()
        assert dlg._status_label.text() == "export finished"

    def test_cancel_kills_and_returns_to_idle(
        self,
        qtbot,  # type: ignore[no-untyped-def]
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        scene = _scene_file(tmp_path)
        controller = ExportController()
        dlg = ExportDialog(
            scene_path=scene,
            scene_names=("DemoScene",),
            current_scene="DemoScene",
            block_count=3,
            settings=None,
            controller=controller,
        )
        qtbot.addWidget(dlg)
        self._wire_running_child(dlg, monkeypatch)
        dlg._export_button.click()
        assert dlg._state == "running"
        dlg._cancel_button.click()
        assert dlg._cancelled is True
        assert dlg._state in {"running", "idle"}  # cancelled once the kill signal lands
        qtbot.waitUntil(lambda: dlg._state == "idle", timeout=10_000)
        assert dlg._status_label.text() == "export cancelled"

    def test_export_busy_guard(
        self,
        qtbot,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,  # type: ignore[no-untyped-def]
    ) -> None:
        scene = _scene_file(tmp_path)
        controller = ExportController()
        dlg = ExportDialog(
            scene_path=scene,
            scene_names=("DemoScene",),
            current_scene="DemoScene",
            block_count=3,
            settings=None,
            controller=controller,
        )
        qtbot.addWidget(dlg)
        self._wire_running_child(dlg, monkeypatch)
        controller.export_plan(  # a foreign export already running
            ExportPlan(
                argv=(sys.executable, "-c", "import time; time.sleep(30)"),
                output_path=tmp_path / "other.mp4",
                scene_name="Other",
                quality="high",
            )
        )
        dlg._export_button.click()
        assert dlg._state == "idle"
        assert "already running" in dlg._status_label.text()
        controller.shutdown()  # stop the foreign export so teardown is quiet

    def test_open_folder_check_fires_on_success(
        self,
        qtbot,  # type: ignore[no-untyped-def]
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        scene = _scene_file(tmp_path)
        controller = ExportController()
        dlg = ExportDialog(
            scene_path=scene,
            scene_names=("DemoScene",),
            current_scene="DemoScene",
            block_count=3,
            settings=None,
            controller=controller,
        )
        qtbot.addWidget(dlg)
        dlg._open_folder_check.setChecked(True)
        opened: list[str] = []
        monkeypatch.setattr(dlg, "_open_folder", lambda: opened.append("open"))
        dlg._plan_builder = lambda *a, **k: ExportPlan(  # type: ignore[method-assign]
            argv=(sys.executable, "-c", "pass"),
            output_path=tmp_path / "out.mp4",
            scene_name="DemoScene",
            quality="high",
        )
        dlg._refresh()  # rebuild the plan with the fake (a stale real plan would run manimgl)
        dlg._export_button.click()
        qtbot.waitUntil(lambda: dlg._state == "done", timeout=10_000)
        assert opened == ["open"]
