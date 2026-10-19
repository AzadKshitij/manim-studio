"""The editor and an agent on the same scene file, driven headlessly (offscreen Qt, no host).

Covers the two checks the plan left for a GUI session:

* a timeline edit made in the editor is saved to the sidecar, never to the source, and the
  agent's frames and totals honour it (``use_sidecar=false`` ignores it);
* a file the agent writes is taken by the open editor (the watcher's handler is called directly,
  since the watcher's debounce needs a real event loop and a real file system event).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from manim_studio import agent_tools
from manim_studio.editor.main_window import MainWindow
from manim_studio.external_changes import AUTO
from manim_studio.site_index import index_source

SCENE = """\
from manimlib import *


class Pair(Scene):
    def construct(self):
        self.add(Dot(color="#58C4DD"))
        self.wait(1.0)
        self.wait(1.0)
"""


def _host_log(source: str) -> dict[str, Any]:
    """The log event the host sends for ``source``: one entry per call site, in order."""
    sites = [site.site_id for site in index_source(source).sites]
    return {
        "sites": [{"id": site_id, "occ": 0} for site_id in sites],
        "patches": {"applied": 0, "orphaned": []},
    }


@pytest.fixture
def project(tmp_path: Path) -> Path:
    root = tmp_path / "project"
    root.mkdir()
    (root / "pair.py").write_text(SCENE, encoding="utf-8")
    return root


@pytest.fixture
def window(qtbot, project: Path, monkeypatch: pytest.MonkeyPatch) -> MainWindow:  # type: ignore[no-untyped-def]
    win = MainWindow(restore_settings=False, auto_preview=False, completion=False)
    qtbot.addWidget(win)
    monkeypatch.setattr(win, "reload_preview", lambda: None)  # no host in these tests
    assert win.open_path(project / "pair.py")
    win._set_external_policy(AUTO)
    return win


class TestTimelineEditReachesTheAgent:
    def test_the_edit_is_saved_to_the_sidecar_and_not_the_source(
        self, window: MainWindow, project: Path
    ) -> None:
        window.patches.on_log(_host_log(SCENE))
        assert window.patches.set_block_duration(1, 3.0)
        assert (project / "pair.studio.json").is_file()
        assert (project / "pair.py").read_text(encoding="utf-8") == SCENE

    @pytest.mark.gl
    def test_the_agent_sees_the_edited_duration(self, window: MainWindow, project: Path) -> None:
        window.patches.on_log(_host_log(SCENE))
        assert window.patches.set_block_duration(1, 3.0)

        honoured = agent_tools.frame(project, "pair.py", 0.5)
        ignored = agent_tools.frame(project, "pair.py", 0.5, use_sidecar=False)
        assert honoured.meta["total_duration"] == pytest.approx(4.0)
        assert ignored.meta["total_duration"] == pytest.approx(2.0)
        assert honoured.meta["patches_applied"] == 1


class TestAgentWriteReachesTheEditor:
    def test_an_agent_write_is_taken_by_the_open_editor(
        self, window: MainWindow, project: Path
    ) -> None:
        edited = SCENE.replace("Dot(color", "Square(color")
        result = agent_tools.write_scene(project, "pair.py", edited)
        assert result["check"]["ok"] is True
        # What the file watcher calls when the agent saves the file.
        window._on_file_changed(str(project / "pair.py"))
        assert window.editor.text() == edited
        assert window._external_bar.isHidden()
