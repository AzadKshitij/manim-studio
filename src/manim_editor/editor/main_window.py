"""Editor main window: code | preview split, timeline below, live shell dock.

This class is the orchestrator: it owns the widgets, the preview host controller,
the file watcher and the rendered-preview transport, and routes every event.
"""

from __future__ import annotations

import keyword
import logging
import re
from pathlib import Path
from typing import Any

from PySide6.QtCore import QProcess, QSettings, QStandardPaths, Qt, QTimer
from PySide6.QtGui import QAction, QCloseEvent, QImage, QKeySequence
from PySide6.QtWidgets import (
    QComboBox,
    QDockWidget,
    QFileDialog,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSlider,
    QSplitter,
    QStatusBar,
    QToolBar,
    QVBoxLayout,
    QWidget,
)

from manim_editor import APP_NAME, ipc
from manim_editor.api_index import build_index, index_names, load_index, save_index
from manim_editor.editor.code_editor import CodeEditor
from manim_editor.editor.completion_worker import CompletionWorker
from manim_editor.editor.file_watcher import FileWatcher
from manim_editor.editor.icon import load_icon
from manim_editor.editor.preview_controller import PreviewController
from manim_editor.editor.preview_panel import PreviewPanel
from manim_editor.editor.timeline_controller import MediaTransport
from manim_editor.editor.timeline_widget import TimelineWidget
from manim_editor.export import build_export_plan
from manim_editor.scene_runner import check_scene_file
from manim_editor.stats import format_bytes, process_rss_bytes, usage_percent
from manim_editor.templates import (
    SCENE_TEMPLATES,
    SNIPPET_TEMPLATES,
    find_template,
    render_scene_template,
)
from manim_editor.timeline_model import TimelineLog, format_duration, parse_log

__all__ = ["MainWindow"]

log = logging.getLogger(__name__)

PREVIEW_FPS = 15


class MainWindow(QMainWindow):
    """The application shell."""

    def __init__(
        self,
        path: str | Path | None = None,
        *,
        embedded: bool = True,
        preview_size: tuple[int, int] = (1280, 720),
        restore_settings: bool = True,
        auto_preview: bool = True,
        initial_frame: str = "end",
        completion: bool = True,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(APP_NAME)
        self.setWindowIcon(load_icon())
        self._embedded = embedded
        self._preview_size = preview_size
        self._initial_frame = initial_frame
        self._path: Path | None = None
        self._auto_reload = True
        self._auto_preview = auto_preview
        self._restore_settings = restore_settings
        self._video_mode = False
        self._playing = False
        self._play_from_index: int | None = None
        self._total_duration = 0.0
        self._preview_process: QProcess | None = None
        self._export_process: QProcess | None = None
        self._settings = QSettings("ManimEditor", "ManimEditor")
        self._api_names = self._load_api_names()
        self._stats_cache: dict[str, Any] = {}
        self._stats_dialog: Any = None

        self.editor = CodeEditor(self)
        self.preview_panel = PreviewPanel(self)
        self.timeline = TimelineWidget(self)
        self.controller = PreviewController(self)
        self.transport = MediaTransport(self)
        self.watcher = FileWatcher(parent=self)

        self._build_layout()
        self._build_actions()
        self._build_shell_dock()
        self._build_status_bar()
        self._connect_signals()

        self.editor.set_completions(self._api_names)
        self._completion_worker: CompletionWorker | None = None
        if completion:
            self._completion_worker = CompletionWorker(self)
            self._completion_worker.completionsReady.connect(self._on_completions_ready)
            self._completion_worker.signaturesReady.connect(self._on_signatures_ready)
            self.destroyed.connect(self._stop_completion_worker)
        if restore_settings:
            self._restore_geometry()
        if path is not None:
            self.open_path(path)

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_layout(self) -> None:
        horizontal = QSplitter(Qt.Orientation.Horizontal, self)
        horizontal.addWidget(self.editor)
        horizontal.addWidget(self.preview_panel)
        horizontal.setStretchFactor(0, 3)
        horizontal.setStretchFactor(1, 2)

        bottom = QWidget(self)
        bottom_layout = QVBoxLayout(bottom)
        bottom_layout.setContentsMargins(0, 0, 0, 0)
        bottom_layout.addWidget(self.timeline)

        self._scrub_slider = QSlider(Qt.Orientation.Horizontal, self)
        self._scrub_slider.setRange(0, 0)
        self._scrub_slider.setEnabled(False)
        self._scrub_slider.setToolTip("Scrub the rendered preview video")
        self._scrub_position = QLabel("—", self)
        scrub_row = QHBoxLayout()
        scrub_row.setContentsMargins(6, 0, 6, 4)
        self._render_preview_button = QPushButton("Render preview video", self)
        scrub_row.addWidget(self._render_preview_button)
        scrub_row.addWidget(self._scrub_slider, 1)
        scrub_row.addWidget(self._scrub_position)
        bottom_layout.addLayout(scrub_row)

        vertical = QSplitter(Qt.Orientation.Vertical, self)
        vertical.addWidget(horizontal)
        vertical.addWidget(bottom)
        vertical.setStretchFactor(0, 4)
        vertical.setStretchFactor(1, 1)
        self.setCentralWidget(vertical)

    def _build_actions(self) -> None:
        menu_bar = self.menuBar()

        file_menu = menu_bar.addMenu("&File")
        self._add_action(
            file_menu, "New scene…", self.new_scene_dialog, QKeySequence.StandardKey.New
        )
        self._add_action(file_menu, "Open…", self.open_dialog, QKeySequence.StandardKey.Open)
        self._add_action(file_menu, "Save", self.save_buffer, QKeySequence.StandardKey.Save)
        self._add_action(
            file_menu, "Save As…", self.save_buffer_as, QKeySequence.StandardKey.SaveAs
        )
        file_menu.addSeparator()
        self._add_action(file_menu, "Export video…", self.export_video_dialog, "Ctrl+E")
        self._add_action(file_menu, "Export still…", self.export_still_dialog, "Ctrl+Shift+E")
        file_menu.addSeparator()
        self._add_action(file_menu, "Quit", self.close, QKeySequence.StandardKey.Quit)

        edit_menu = menu_bar.addMenu("&Edit")
        self._add_action(
            edit_menu, "Undo", lambda: self.editor.undo(), QKeySequence.StandardKey.Undo
        )
        self._add_action(edit_menu, "Redo", lambda: self.editor.redo(), "Ctrl+Y")
        self._add_action(edit_menu, "Cut", lambda: self.editor.cut(), QKeySequence.StandardKey.Cut)
        self._add_action(
            edit_menu, "Copy", lambda: self.editor.copy(), QKeySequence.StandardKey.Copy
        )
        self._add_action(
            edit_menu, "Paste", lambda: self.editor.paste(), QKeySequence.StandardKey.Paste
        )
        self._add_action(
            edit_menu, "Select All", self._select_all, QKeySequence.StandardKey.SelectAll
        )
        edit_menu.addSeparator()
        self._add_action(
            edit_menu, "Trigger completion", self.editor.trigger_completion, "Ctrl+Space"
        )

        insert_menu = menu_bar.addMenu("&Insert")
        for template in SNIPPET_TEMPLATES:
            self._add_action(
                insert_menu,
                template.title,
                lambda checked=False, key=template.key: self.insert_template(key),
            )

        run_menu = menu_bar.addMenu("&Run")
        self._add_action(run_menu, "Reload preview", self.reload_preview, "F5")
        self._add_action(run_menu, "Play from playhead", self.request_play, "F6")
        self._add_action(run_menu, "Stop playback", self.request_stop, "F7")
        run_menu.addSeparator()
        self._add_action(run_menu, "Render preview video", self.render_preview_video, "Ctrl+R")
        self._add_action(run_menu, "Focus live shell", self.focus_shell, "Ctrl+`")

        view_menu = menu_bar.addMenu("&View")
        self._embedded_action = QAction("Embedded preview", self, checkable=True)
        self._embedded_action.setChecked(self._embedded)
        self._embedded_action.triggered.connect(self.set_embedded)
        view_menu.addAction(self._embedded_action)
        self._add_action(view_menu, "Fit timeline", self.timeline.fit, "Ctrl+0")

        tools_menu = menu_bar.addMenu("&Tools")
        self._add_action(tools_menu, "LaTeX setup…", self.open_latex_setup)

        help_menu = menu_bar.addMenu("&Help")
        self._add_action(help_menu, "App stats…", self.show_app_stats)
        self._add_action(help_menu, "About", self.show_about)

        toolbar = QToolBar("Main", self)
        toolbar.setMovable(False)
        self.addToolBar(toolbar)
        toolbar.addWidget(QLabel(" Scene: "))
        self._scene_combo = QComboBox(self)
        self._scene_combo.setMinimumWidth(140)
        self._scene_combo.currentTextChanged.connect(self._on_scene_selected)
        toolbar.addWidget(self._scene_combo)
        toolbar.addSeparator()
        self._add_toolbar_action(toolbar, "Reload", self.reload_preview)
        self._add_toolbar_action(toolbar, "Play", self.request_play)
        self._add_toolbar_action(toolbar, "Stop", self.request_stop)
        toolbar.addSeparator()
        self._add_toolbar_action(toolbar, "Render preview", self.render_preview_video)

    def _build_shell_dock(self) -> None:
        self.shell_output = QPlainTextEdit(self)
        self.shell_output.setReadOnly(True)
        self.shell_output.setMaximumBlockCount(2000)
        self.shell_output.setPlaceholderText("Preview host log and live shell output")
        self.shell_input = QLineEdit(self)
        self.shell_input.setPlaceholderText(">>> run Python against the live scene (Enter)")
        self.shell_input.returnPressed.connect(self.send_shell_line)
        dock_body = QWidget(self)
        layout = QVBoxLayout(dock_body)
        layout.setContentsMargins(2, 2, 2, 2)
        layout.addWidget(self.shell_output, 1)
        layout.addWidget(self.shell_input)
        self.shell_dock = QDockWidget("Live shell", self)
        self.shell_dock.setWidget(dock_body)
        self.shell_dock.setAllowedAreas(Qt.DockWidgetArea.BottomDockWidgetArea)
        self.addDockWidget(Qt.DockWidgetArea.BottomDockWidgetArea, self.shell_dock)
        self.shell_dock.hide()

    def _build_status_bar(self) -> None:
        bar = QStatusBar(self)
        self.setStatusBar(bar)
        self._host_state_label = QLabel("preview: off")
        self._mem_label = QLabel("cache: —")
        self._mem_label.setToolTip("Filmstrip cache RAM and process memory (Help > App stats…)")
        self._latex_label = QLabel("")
        self._cursor_label = QLabel("Ln 1, Col 1")
        bar.addPermanentWidget(self._cursor_label)
        bar.addPermanentWidget(self._latex_label)
        bar.addPermanentWidget(self._mem_label)
        bar.addPermanentWidget(self._host_state_label)
        self.refresh_latex_indicator()
        self._mem_timer = QTimer(self)
        self._mem_timer.setInterval(2000)
        self._mem_timer.timeout.connect(self._refresh_memory_label)
        self._mem_timer.start()

    def refresh_latex_indicator(self) -> None:
        from manim_editor.latex import latex_status

        try:
            status = latex_status()
        except Exception:  # never break window construction for a PATH probe
            self._latex_label.setText("")
            return
        self._latex_label.setText(f"LaTeX: {'ok' if status.ok else 'missing'}")

    def open_latex_setup(self) -> None:
        from manim_editor.editor.latex_dialog import LatexSetupDialog
        from manim_editor.latex import latex_status

        before = latex_status()
        dialog = LatexSetupDialog(self)
        dialog.exec()
        after = latex_status()
        self.refresh_latex_indicator()
        if self._path is not None and self._auto_preview:
            newly_ok = after.ok and not before.ok
            newly_bundled = after.bundled and not before.bundled
            if newly_ok or newly_bundled:
                # The running host's PATH predates the install: restart it so the
                # freshly installed LaTeX is visible to the scene.
                self._set_status("LaTeX updated — restarting preview")
                self._start_preview()

    def _connect_signals(self) -> None:
        self.editor.saveRequested.connect(self.save_buffer)
        self.editor.fileDropped.connect(self.open_path)
        self.editor.cursorMoved.connect(
            lambda line, column: self._cursor_label.setText(f"Ln {line}, Col {column}")
        )
        self.editor.modificationChanged.connect(self._update_title)
        self.preview_panel.reloadRequested.connect(self.reload_preview)
        self.preview_panel.playRequested.connect(self.request_play)
        self.preview_panel.stopRequested.connect(self.request_stop)
        self.timeline.scrubRequested.connect(self.scrub_to_time)
        self.timeline.playToggleRequested.connect(self._toggle_play)
        self.controller.logReceived.connect(self._on_log)
        self.controller.statusChanged.connect(self._on_status)
        self.controller.tickReceived.connect(self._on_tick)
        self.controller.stoppedReceived.connect(self._on_stopped)
        self.controller.errorReceived.connect(self._on_error)
        self.controller.execResultReceived.connect(self._on_exec_result)
        self.controller.filmstripReceived.connect(self._on_filmstrip)
        self.controller.statsReceived.connect(self._on_stats)
        self.controller.frameReceived.connect(self._on_frame)
        self.controller.hostLogReceived.connect(self._append_shell_output)
        self.controller.stateChanged.connect(self._on_host_state)
        self.watcher.file_changed.connect(self._on_file_changed)
        self.watcher.file_missing.connect(lambda path: self._set_status(f"file missing: {path}"))
        self._render_preview_button.clicked.connect(self.render_preview_video)
        self._scrub_slider.sliderMoved.connect(self._on_slider_moved)
        self._scrub_slider.sliderReleased.connect(self._on_slider_released)
        self.transport.frameReady.connect(self._on_transport_frame)
        self.transport.positionChanged.connect(self._on_transport_position)
        self.transport.durationChanged.connect(self._on_transport_duration)
        self.transport.failed.connect(lambda message: self._set_status(f"preview video: {message}"))
        self.editor.completionsRequested.connect(self._on_completions_requested)
        self.editor.signaturesRequested.connect(self._on_signatures_requested)

    # ------------------------------------------------------------------
    # Completion (Jedi worker)
    # ------------------------------------------------------------------

    def _completion_path(self) -> str | None:
        return str(self._path) if self._path is not None else None

    def _on_completions_requested(self, source: str, offset: int) -> None:
        if self._completion_worker is None:
            return
        self._completion_worker.request_completions(source, offset, self._completion_path())
        self._ensure_completion_worker()

    def _on_signatures_requested(self, source: str, offset: int) -> None:
        if self._completion_worker is None:
            return
        self._completion_worker.request_signatures(source, offset, self._completion_path())
        self._ensure_completion_worker()

    def _ensure_completion_worker(self) -> None:
        if self._completion_worker is not None and not self._completion_worker.isRunning():
            self._completion_worker.start()

    def _warm_up_completion(self) -> None:
        """Prime Jedi's cache for the freshly loaded buffer in the background."""
        if self._completion_worker is None:
            return
        self._completion_worker.request_warmup(self.editor.text(), self._completion_path())
        self._ensure_completion_worker()

    def _stop_completion_worker(self, *_args: Any) -> None:
        if self._completion_worker is not None:
            self._completion_worker.stop()

    def _on_completions_ready(self, offset: int, items: Any) -> None:
        self.editor.show_completions(offset, [item.insert for item in items])

    def _on_signatures_ready(self, offset: int, items: Any) -> None:
        if items:
            self.editor.show_calltip(offset, "\n".join(item.label for item in items))

    # ------------------------------------------------------------------
    # Actions and menus
    # ------------------------------------------------------------------

    def _add_action(self, menu: Any, text: str, slot: Any, shortcut: Any = None) -> QAction:
        action = QAction(text, self)
        action.triggered.connect(lambda checked=False: slot())
        if shortcut is not None:
            action.setShortcut(QKeySequence(shortcut) if isinstance(shortcut, str) else shortcut)
        menu.addAction(action)
        return action

    def _add_toolbar_action(self, toolbar: QToolBar, text: str, slot: Any) -> QAction:
        action = QAction(text, self)
        action.triggered.connect(lambda checked=False: slot())
        toolbar.addAction(action)
        return action

    def _select_all(self) -> None:
        self.editor.setSel(0, self.editor.textLength())

    def show_about(self) -> None:
        QMessageBox.about(
            self,
            f"About {APP_NAME}",
            f"{APP_NAME}\n\nRealtime ManimGL scene editor.\n"
            "Left: code. Right: live preview. Bottom: runtime timeline.",
        )

    def focus_shell(self) -> None:
        self.shell_dock.show()
        self.shell_input.setFocus()

    def set_embedded(self, checked: bool) -> None:
        self._embedded = bool(checked)
        self.preview_panel.set_mode(embedded=self._embedded)
        if self._path is not None and self._auto_preview and self._auto_reload:
            self._start_preview()

    # ------------------------------------------------------------------
    # Files
    # ------------------------------------------------------------------

    @property
    def current_path(self) -> Path | None:
        return self._path

    def open_dialog(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Open scene", str(Path.cwd()), "Python files (*.py)"
        )
        if path:
            self.open_path(path)

    def open_path(self, path: str | Path) -> bool:
        """Load a scene file into the editor and (re)start the preview."""
        target = Path(path)
        check = check_scene_file(target)
        if not check.ok:
            self._set_status(f"cannot open: {check.error}")
            self._append_shell_output(f"open failed: {target}: {check.error}")
            return False
        self.editor.load_file(target)
        self._path = target.resolve()
        self.watcher.watch(self._path)
        self._refresh_scene_list(self._path)
        self._update_title()
        self._set_status(f"opened {self._path.name} ({len(check.scenes)} scene(s))")
        self._warm_up_completion()
        if self._auto_reload:
            self._start_preview()
        return True

    def _refresh_scene_list(self, path: Path) -> tuple[str, ...]:
        """Repopulate the scene combo from the file without impersonating a user.

        Keeps the current selection when it still exists (so a reload after edits
        keeps the same scene) and falls back to the first scene when the selected
        class was renamed or removed.
        """
        check = check_scene_file(path)
        names = tuple(scene.name for scene in check.scenes)
        current = self._scene_combo.currentText()
        self._scene_combo.blockSignals(True)
        try:
            self._scene_combo.clear()
            self._scene_combo.addItems(names)
            if current in names:
                self._scene_combo.setCurrentIndex(names.index(current))
            elif names:
                self._scene_combo.setCurrentIndex(0)
        finally:
            self._scene_combo.blockSignals(False)
        return names

    def new_scene_dialog(self) -> None:
        template_keys = [template.key for template in SCENE_TEMPLATES]
        titles = {template.key: template.title for template in SCENE_TEMPLATES}
        key, accepted = QInputDialog.getItem(
            self,
            "New scene",
            "Template:",
            [f"{key} — {titles[key]}" for key in template_keys],
            0,
            False,
        )
        if not accepted:
            return
        template_key = key.split(" — ", 1)[0]
        suggested = _scene_name_from_path(Path(f"{template_key}.py"))
        scene_name = self._prompt_scene_name(suggested)
        if scene_name is None:
            return
        default_name = f"{_snake_case(scene_name)}.py"
        path, _ = QFileDialog.getSaveFileName(
            self, "Create scene", str(Path.cwd() / default_name), "Python files (*.py)"
        )
        if not path:
            return
        target = self.create_scene_file(path, template_key, scene_name)
        if target is not None:
            self.open_path(target)

    def _prompt_scene_name(self, default: str) -> str | None:
        """Ask for the scene class name until it is a usable Python identifier.

        Returns ``None`` when the user cancels; otherwise a validated name.
        """
        candidate = default
        while True:
            name, accepted = QInputDialog.getText(
                self,
                "New scene",
                "Scene class name:",
                QLineEdit.EchoMode.Normal,
                candidate,
            )
            if not accepted:
                return None
            name = name.strip()
            if _is_valid_scene_name(name):
                return name
            QMessageBox.warning(
                self,
                "New scene",
                f"{name or '(empty)'!r} is not a valid scene name.\n"
                "Use a Python identifier that is not a keyword, e.g. MyScene.",
            )
            candidate = name or candidate

    def create_scene_file(
        self, path: str | Path, template_key: str, scene_name: str | None = None
    ) -> Path | None:
        target = Path(path)
        scene_name = scene_name or _scene_name_from_path(target)
        code = render_scene_template(template_key, scene_name)
        target.write_text(code, encoding="utf-8")
        return target

    def save_buffer(self) -> None:
        if self._path is None:
            self.save_buffer_as()
            return
        # Compare the buffer to the file *before* writing.  The preview host reads
        # from disk, so a save only warrants a re-render when the code actually
        # changed.  ``is_modified()`` is Scintilla's save-point flag; it stays set
        # after retyping identical text, so content equality is the real check.
        changed = not self._buffer_matches_disk()
        if not self.editor.is_modified() and not changed:
            self._set_status(f"no changes — {self._path.name} is up to date")
            return
        self.editor.save_file(self._path)
        self.watcher.note_save()
        self._update_title()
        self._set_status(f"saved {self._path.name}")
        if changed and self._auto_reload and self.controller.is_running:
            self.controller.reload()

    def _buffer_matches_disk(self) -> bool:
        """True when the buffer holds exactly the code currently on disk."""
        if self._path is None:
            return False
        disk = _read_disk_text(self._path)
        return disk is not None and disk == self.editor.text()

    def save_buffer_as(self) -> None:
        suggested = str(self._path) if self._path else str(Path.cwd() / "scene.py")
        path, _ = QFileDialog.getSaveFileName(self, "Save scene", suggested, "Python files (*.py)")
        if path:
            self.editor.save_file(path)
            self.open_path(path)

    def insert_template(self, key: str) -> None:
        template = find_template(key, kind="snippet")
        self.editor.insert_snippet(template.render())

    # ------------------------------------------------------------------
    # Preview control
    # ------------------------------------------------------------------

    def _scene_name(self) -> str | None:
        name = self._scene_combo.currentText().strip()
        return name or None

    def _start_preview(self) -> None:
        if self._path is None or not self._auto_preview:
            return
        self.timeline.set_log(TimelineLog(()))
        self.preview_panel.set_status("starting")
        self.controller.start(
            self._path,
            self._scene_name(),
            embedded=self._embedded,
            size=self._preview_size,
            initial_frame=self._initial_frame,
        )

    def reload_preview(self) -> None:
        if self._path is None:
            return
        # A reload is an explicit "try again": drop the previous error marker in
        # both paths (restarting a crashed host included), not just when the host
        # happens to be running.
        self.editor.clear_error_marker()
        if not self.controller.is_running:
            self._start_preview()
            return
        # Use `load` with an explicit path and scene name (not `reload`) so renaming
        # the scene class or switching scenes in the combo always takes effect.
        self.controller.load(self._path, self._scene_name())

    def request_play(self) -> None:
        if not self.controller.is_running:
            self._start_preview()
            return
        # Play from the beginning unless the user explicitly picked a block.
        index = self._play_from_index if self._play_from_index is not None else 0
        self._video_mode = False
        self.controller.play(from_index=index)

    def request_stop(self) -> None:
        self.controller.stop_playback()

    def _toggle_play(self) -> None:
        if self._playing:
            self.request_stop()
        else:
            self.request_play()

    def seek_to_block(self, index: int) -> None:
        """Jump to the end state of a block (used by menus/tests; drag uses scrub)."""
        self._video_mode = False
        self._play_from_index = index
        block = self.timeline.log.block_by_index(index)
        if block is not None:
            self.timeline.set_playhead(block.end)
            self.preview_panel.set_time(block.end, self._total_duration)
        self.controller.seek(index)

    def scrub_to_time(self, time: float) -> None:
        """Continuous scrub: pose the live scene at exactly this time."""
        if self._path is None or not self.controller.is_running:
            return
        self._video_mode = False
        limit = max(self._total_duration, 0.0)
        position = min(max(float(time), 0.0), limit)
        self._play_from_index = self.timeline.log.index_at(position)
        self.timeline.set_playhead(position)
        self.preview_panel.set_time(position, self._total_duration)
        self.controller.scrub(position)

    # ------------------------------------------------------------------
    # Export / preview video
    # ------------------------------------------------------------------

    def preview_video_path(self) -> Path | None:
        if self._path is None:
            return None
        return self._path.parent / ".manim-editor" / f"{self._path.stem}_preview.mp4"

    def render_preview_video(self) -> None:
        if self._path is None:
            self._set_status("open a scene first")
            return
        if (
            self._preview_process is not None
            and self._preview_process.state() != QProcess.ProcessState.NotRunning
        ):
            self._set_status("preview render already running")
            return
        output = self.preview_video_path()
        assert output is not None
        plan = build_export_plan(
            self._path,
            self._scene_name(),
            output=output,
            quality="preview",
            fps=PREVIEW_FPS,
        )
        self._preview_process = self._spawn_render(plan.argv, self._on_preview_render_finished)
        self._set_status(f"rendering preview video ({output.name})…")

    def export_video_dialog(self) -> None:
        if self._path is None:
            return
        default = str(self._path.with_suffix(".mp4"))
        path, _ = QFileDialog.getSaveFileName(self, "Export video", default, "MP4 video (*.mp4)")
        if path:
            self.export_video_to(path)

    def export_video_to(
        self, output: str | Path, *, quality: str = "high", fps: int | None = None
    ) -> None:
        if self._path is None:
            self._set_status("open a scene first")
            return
        if (
            self._export_process is not None
            and self._export_process.state() != QProcess.ProcessState.NotRunning
        ):
            self._set_status("export already running")
            return
        plan = build_export_plan(
            self._path, self._scene_name(), output=output, quality=quality, fps=fps
        )
        self._export_process = self._spawn_render(plan.argv, self._on_export_finished)
        self._set_status(f"exporting {plan.output_path}…")

    def export_still_dialog(self) -> None:
        if self._path is None:
            return
        default = str(self._path.with_suffix(".png"))
        path, _ = QFileDialog.getSaveFileName(self, "Export still", default, "PNG image (*.png)")
        if path:
            plan = build_export_plan(self._path, self._scene_name(), output=path, still=True)
            self._export_process = self._spawn_render(plan.argv, self._on_export_finished)
            self._set_status(f"exporting still {plan.output_path.name}…")

    def _spawn_render(self, argv: tuple[str, ...], finished_slot: Any) -> QProcess:
        process = QProcess(self)
        process.readyReadStandardOutput.connect(lambda: self._drain_render_output(process))
        process.readyReadStandardError.connect(lambda: self._drain_render_output(process))
        process.finished.connect(finished_slot)
        process.start(argv[0], list(argv[1:]))
        return process

    def _drain_render_output(self, process: QProcess) -> None:
        for stream in (process.readAllStandardOutput(), process.readAllStandardError()):
            text = ipc.as_bytes(stream).decode("utf-8", "replace")
            for line in text.splitlines():
                if line.strip():
                    self._append_shell_output(line)

    def _on_preview_render_finished(self, exit_code: int, _status: Any) -> None:
        output = self.preview_video_path()
        if exit_code == 0 and output is not None and output.is_file():
            self.transport.load(output)
            self._scrub_slider.setEnabled(True)
            self._set_status(f"preview video ready: {output}")
        else:
            self._set_status(f"preview render failed (exit {exit_code})")

    def _on_export_finished(self, exit_code: int, _status: Any) -> None:
        self._set_status(
            "export finished" if exit_code == 0 else f"export failed (exit {exit_code})"
        )

    # ------------------------------------------------------------------
    # Video scrub transport
    # ------------------------------------------------------------------

    def _on_slider_moved(self, milliseconds: int) -> None:
        self._enter_video_mode()
        self.transport.seek(milliseconds / 1000.0)

    def _on_slider_released(self) -> None:
        self._enter_video_mode()

    def _enter_video_mode(self) -> None:
        if self._video_mode:
            return
        self._video_mode = True
        self.controller.stop_playback()
        self._set_status("scrubbing rendered preview (timeline click returns to live)")

    def _on_transport_frame(self, image: QImage) -> None:
        if self._video_mode:
            self.preview_panel.set_frame(image)

    def _on_transport_position(self, seconds: float) -> None:
        if self._video_mode:
            self.timeline.set_playhead(seconds)
            self.preview_panel.set_time(seconds, self.transport.duration)
            if not self._scrub_slider.isSliderDown():
                self._scrub_slider.blockSignals(True)
                self._scrub_slider.setValue(int(seconds * 1000))
                self._scrub_slider.blockSignals(False)

    def _on_transport_duration(self, seconds: float) -> None:
        self._scrub_slider.setRange(0, int(seconds * 1000))
        self._scrub_position.setText(f"0:00.0 / {format_duration(seconds)}")

    # ------------------------------------------------------------------
    # Preview host events
    # ------------------------------------------------------------------

    def _on_log(self, message: dict[str, Any]) -> None:
        try:
            timeline_log = parse_log(message.get("blocks", []))
        except Exception as exc:
            self._append_shell_output(f"bad trace payload: {exc}")
            return
        self.timeline.set_log(timeline_log)
        self._total_duration = timeline_log.total_duration
        # A successful trace means the scene now compiles/runs: clear any marker
        # left by the previous failure.
        self.editor.clear_error_marker()
        self.preview_panel.set_cache_status("")
        # The host shows the final frame by default; line the playhead up with it and
        # forget any previously selected block so Play restarts from the beginning.
        self.timeline.set_playhead(self._total_duration if self._initial_frame == "end" else 0.0)
        self.preview_panel.set_time(self.timeline.playhead_time, self._total_duration)
        self._play_from_index = None
        self._set_status(
            f"trace: {timeline_log.num_blocks} blocks, {format_duration(self._total_duration)}"
        )

    def _on_status(self, state: str, message: dict[str, Any]) -> None:
        note = str(message.get("note", ""))
        self._playing = state == "playing"
        self.preview_panel.set_status(state, note)
        self.timeline.set_playing(self._playing)

    def _on_tick(self, time_seconds: float, _index: int, scrubbing: bool = False) -> None:
        # While the user is dragging, the widget already owns the playhead: applying
        # host ticks here would fight the pointer and make it jitter (ticks for
        # older positions can arrive after the pointer has moved on).
        if scrubbing:
            return
        if self._video_mode:
            return
        self.timeline.set_playhead(time_seconds)
        self.preview_panel.set_time(time_seconds, self._total_duration)

    def _on_filmstrip(self, message: dict[str, Any]) -> None:
        self._stats_cache = dict(message)
        frames = int(message.get("frames", 0))
        done = bool(message.get("done", False))
        capped = bool(message.get("capped", False))
        byte_size = message.get("bytes")
        if isinstance(byte_size, (int, float)) and byte_size > 0:
            cache_text = f"cache: {frames} frames · {format_bytes(byte_size)}"
        else:
            cache_text = f"cache: {frames} frames"
        suffix = (" (capped)" if capped else "") if done else "…"
        self.preview_panel.set_cache_status(cache_text + suffix)
        self._refresh_memory_label()

    def _on_stats(self, message: dict[str, Any]) -> None:
        """Host answered a STATS poll: cache numbers double as filmstrip state."""
        self._stats_cache = dict(message)
        self._refresh_memory_label()
        if self._stats_dialog is not None:
            try:
                self._stats_dialog.set_host_stats(message)
            except Exception:  # never let the dialog break the control channel
                log.debug("stats dialog update failed", exc_info=True)

    def _refresh_memory_label(self) -> None:
        """Compact status-bar readout: cache MiB + host/editor RSS."""
        cache = self._stats_cache
        byte_size = cache.get("bytes")
        max_bytes = cache.get("max_bytes")
        frames = cache.get("frames")
        host_rss = cache.get("host_rss")
        if (
            isinstance(byte_size, (int, float))
            and isinstance(max_bytes, (int, float))
            and max_bytes > 0
        ):
            percent = usage_percent(byte_size, max_bytes)
            percent_text = f" {percent:.0f}%" if percent is not None else ""
            cache_text = f"cache {format_bytes(byte_size)}{percent_text}"
        elif isinstance(frames, int):
            cache_text = f"cache {frames} frames"
        else:
            cache_text = "cache —"
        host_text = f"host {format_bytes(host_rss)}" if host_rss is not None else "host —"
        editor_text = f"editor {format_bytes(process_rss_bytes())}"
        self._mem_label.setText(f"{cache_text} · {host_text} · {editor_text}")

    def _editor_stats_snapshot(self) -> dict[str, Any]:
        return {
            "editor_rss": process_rss_bytes(),
            "api_names": len(self._api_names),
            "blocks": self.timeline.log.num_blocks,
            "total_duration": self._total_duration,
            "preview_size": self._preview_size,
        }

    def show_app_stats(self) -> None:
        from manim_editor.editor.stats_dialog import AppStatsDialog

        if self._stats_dialog is None:
            self._stats_dialog = AppStatsDialog(self.controller, self._editor_stats_snapshot, self)
        dialog = self._stats_dialog
        if self._stats_cache:
            dialog.set_host_stats(self._stats_cache)
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()

    def _on_stopped(self, message: dict[str, Any]) -> None:
        self.timeline.set_playing(False)
        self._set_status("playback stopped" if message.get("user_stopped") else "playback finished")

    def _on_error(self, message: dict[str, Any]) -> None:
        text = str(message.get("message", "unknown error"))
        self.preview_panel.set_status("error", text)
        self._append_shell_output(f"ERROR: {text}")
        traceback_text = str(message.get("traceback", ""))
        if traceback_text:
            self._append_shell_output(traceback_text.rstrip())
        line = message.get("line")
        if isinstance(line, int):
            self.editor.set_error_marker(line, text)
        if "LaTeX" in text or "latex" in text:
            self.refresh_latex_indicator()
            self._set_status(f"preview error: {text} — open Tools > LaTeX setup and click Diagnose")
        else:
            self._set_status(f"preview error: {text}")

    def _on_frame(self, image: QImage) -> None:
        if not self._video_mode:
            self.preview_panel.set_frame(image)

    def _on_host_state(self, state: str) -> None:
        self._host_state_label.setText(f"preview: {state}")
        if state == "crashed":
            self.preview_panel.set_status("crashed", "host process died — press Reload")
            self._set_status("preview host crashed")

    def _on_scene_selected(self, name: str) -> None:
        if self._path is not None and name and self.controller.is_running:
            self.controller.load(self._path, name)

    def _on_file_changed(self, path: str) -> None:
        if self.editor.is_modified():
            self._set_status("file changed on disk — save (Ctrl+S) to overwrite, or reopen")
            return
        if _read_disk_text(Path(path)) == self.editor.text():
            # Rewritten with identical code (a touch, a no-op formatter, another
            # tool saving the same bytes): reloading would reset the cursor and
            # undo history, and re-render the scene, for nothing.
            self._set_status("file touched on disk — code unchanged")
            return
        # External change with a clean buffer: refresh the editor text, the scene
        # list (a class may have been renamed) and the preview, so all three agree.
        self._set_status("file changed on disk — reloading editor and preview")
        self.editor.load_file(path)
        self._refresh_scene_list(Path(path))
        self._warm_up_completion()
        self.reload_preview()

    # ------------------------------------------------------------------
    # Live shell
    # ------------------------------------------------------------------

    def send_shell_line(self) -> None:
        code = self.shell_input.text().strip()
        if not code:
            return
        self.shell_input.clear()
        self.shell_dock.show()
        self._append_shell_output(f">>> {code}")
        if not self.controller.exec_code(code):
            self._append_shell_output("(preview host is not running)")

    def run_shell_code(self, code: str) -> None:
        self.shell_input.setText(code)
        self.send_shell_line()

    def _on_exec_result(self, message: dict[str, Any]) -> None:
        stdout = str(message.get("stdout", ""))
        if stdout:
            self._append_shell_output(stdout.rstrip())
        result = message.get("result")
        if result is not None:
            self._append_shell_output(f"< {result}")
        error = message.get("error")
        if error:
            self._append_shell_output(f"! {error}")

    def _append_shell_output(self, text: str) -> None:
        if not text:
            return
        self.shell_output.appendPlainText(text)
        self.shell_dock.show()

    # ------------------------------------------------------------------
    # Misc
    # ------------------------------------------------------------------

    def _set_status(self, text: str) -> None:
        self.statusBar().showMessage(text)

    def _update_title(self) -> None:
        if self._path is None:
            self.setWindowTitle(APP_NAME)
            return
        marker = " •" if self.editor.is_modified() else ""
        self.setWindowTitle(f"{self._path.name}{marker} — {APP_NAME}")

    def _load_api_names(self) -> tuple[str, ...]:
        try:
            location = QStandardPaths.writableLocation(
                QStandardPaths.StandardLocation.CacheLocation
            )
            cache = Path(location) / "api_index.json"
        except Exception:  # pragma: no cover - platform fallback
            cache = Path.home() / ".manim-editor" / "api_index.json"
        symbols = load_index(cache)
        if not symbols:
            symbols = build_index()
            if symbols:
                try:
                    save_index(cache, symbols)
                except OSError:
                    log.debug("could not persist API index to %s", cache)
        return index_names(symbols)

    def _restore_geometry(self) -> None:
        geometry = self._settings.value("window/geometry")
        if geometry is not None:
            self.restoreGeometry(geometry)
        self._settings.setValue("window/embedded", self._embedded)

    def closeEvent(self, event: QCloseEvent) -> None:
        self.watcher.stop()
        if self._completion_worker is not None:
            self._completion_worker.stop()
        self.controller.shutdown()
        self.transport.stop()
        for process in (self._preview_process, self._export_process):
            if process is not None and process.state() != QProcess.ProcessState.NotRunning:
                process.kill()
                process.waitForFinished(1500)
        if self._restore_settings:
            self._settings.setValue("window/geometry", self.saveGeometry())
        event.accept()


def _normalize_newlines(text: str) -> str:
    """Normalise CRLF/CR line endings so buffer and disk text compare equal."""
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _read_disk_text(path: Path) -> str | None:
    """The file's text with LF line endings, or ``None`` when it cannot be read."""
    try:
        return _normalize_newlines(path.read_text(encoding="utf-8"))
    except OSError:
        return None


def _scene_name_from_path(path: Path) -> str:
    name = "".join(part.capitalize() for part in path.stem.replace("-", "_").split("_"))
    return name if name.isidentifier() else "MyScene"


def _snake_case(name: str) -> str:
    """``MyIntroScene`` -> ``my_intro_scene``, for a suggested file name."""
    return re.sub(r"(?<!^)(?=[A-Z])", "_", name).lower()


def _is_valid_scene_name(name: str) -> bool:
    """True when ``name`` can be used as the generated scene class name."""
    return bool(name) and name.isidentifier() and not keyword.iskeyword(name)
