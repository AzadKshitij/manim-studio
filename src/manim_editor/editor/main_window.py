"""Editor main window: code | preview split, timeline below, live shell dock.

This class is the orchestrator: it owns the widgets, the preview host controller,
the file watcher and the rendered-preview transport, and routes every event.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from PySide6.QtCore import QProcess, QSettings, QStandardPaths, Qt
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
from manim_editor.editor.file_watcher import FileWatcher
from manim_editor.editor.preview_controller import PreviewController
from manim_editor.editor.preview_panel import PreviewPanel
from manim_editor.editor.timeline_controller import MediaTransport
from manim_editor.editor.timeline_widget import TimelineWidget
from manim_editor.export import build_export_plan
from manim_editor.scene_runner import check_scene_file
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
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(APP_NAME)
        self._embedded = embedded
        self._preview_size = preview_size
        self._path: Path | None = None
        self._auto_reload = True
        self._auto_preview = auto_preview
        self._restore_settings = restore_settings
        self._video_mode = False
        self._total_duration = 0.0
        self._preview_process: QProcess | None = None
        self._export_process: QProcess | None = None
        self._settings = QSettings("ManimEditor", "ManimEditor")
        self._api_names = self._load_api_names()

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

        help_menu = menu_bar.addMenu("&Help")
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
        self._cursor_label = QLabel("Ln 1, Col 1")
        bar.addPermanentWidget(self._cursor_label)
        bar.addPermanentWidget(self._host_state_label)

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
        self.timeline.blockActivated.connect(self.seek_to_block)
        self.timeline.scrubRequested.connect(self.scrub_to_time)
        self.controller.logReceived.connect(self._on_log)
        self.controller.statusChanged.connect(self._on_status)
        self.controller.tickReceived.connect(self._on_tick)
        self.controller.stoppedReceived.connect(self._on_stopped)
        self.controller.errorReceived.connect(self._on_error)
        self.controller.execResultReceived.connect(self._on_exec_result)
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
        self._scene_combo.blockSignals(True)
        self._scene_combo.clear()
        self._scene_combo.addItems([scene.name for scene in check.scenes])
        self._scene_combo.blockSignals(False)
        if check.scenes:
            self._scene_combo.setCurrentIndex(0)
        self._update_title()
        self._set_status(f"opened {self._path.name} ({len(check.scenes)} scene(s))")
        if self._auto_reload:
            self._start_preview()
        return True

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
        default_name = f"{template_key}.py"
        path, _ = QFileDialog.getSaveFileName(
            self, "Create scene", str(Path.cwd() / default_name), "Python files (*.py)"
        )
        if not path:
            return
        target = self.create_scene_file(path, template_key)
        if target is not None:
            self.open_path(target)

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
        self.editor.save_file(self._path)
        self.watcher.note_save()
        self._update_title()
        self._set_status(f"saved {self._path.name}")
        if self._auto_reload and self.controller.is_running:
            self.controller.reload()

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
        )

    def reload_preview(self) -> None:
        if self._path is None:
            return
        if not self.controller.is_running:
            self._start_preview()
            return
        self.editor.clear_error_marker()
        self.controller.reload()

    def request_play(self) -> None:
        if not self.controller.is_running:
            self._start_preview()
            return
        index = self.timeline.log.index_at(self.timeline.playhead_time) or 0
        self._video_mode = False
        self.controller.play(from_index=index)

    def request_stop(self) -> None:
        self.controller.stop_playback()

    def seek_to_block(self, index: int) -> None:
        self._video_mode = False
        block = self.timeline.log.block_by_index(index)
        if block is not None:
            self.timeline.set_playhead(block.end)
            self.preview_panel.set_time(block.end, self._total_duration)
        self.controller.seek(index)

    def scrub_to_time(self, time: float) -> None:
        index = self.timeline.log.index_at(time)
        if index is not None:
            self.seek_to_block(index)

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
        self.preview_panel.set_time(0.0, self._total_duration)
        self._set_status(
            f"trace: {timeline_log.num_blocks} blocks, {format_duration(self._total_duration)}"
        )

    def _on_status(self, state: str, message: dict[str, Any]) -> None:
        note = str(message.get("note", ""))
        self.preview_panel.set_status(state, note)
        self.timeline.set_playing(state == "playing")
        index = message.get("index")
        if state == "idle" and index is not None:
            block = self.timeline.log.block_by_index(int(index))
            if block is not None:
                self.timeline.set_playhead(block.start)

    def _on_tick(self, time_seconds: float, _index: int) -> None:
        if self._video_mode:
            return
        self.timeline.set_playhead(time_seconds)
        self.preview_panel.set_time(time_seconds, self._total_duration)

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

    def _on_file_changed(self, _path: str) -> None:
        if self.editor.is_modified():
            self._set_status("file changed on disk — save (Ctrl+S) to overwrite, or reopen")
            return
        self._set_status("file changed on disk — reloading preview")
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
        self.controller.shutdown()
        self.transport.stop()
        for process in (self._preview_process, self._export_process):
            if process is not None and process.state() != QProcess.ProcessState.NotRunning:
                process.kill()
                process.waitForFinished(1500)
        if self._restore_settings:
            self._settings.setValue("window/geometry", self.saveGeometry())
        event.accept()


def _scene_name_from_path(path: Path) -> str:
    name = "".join(part.capitalize() for part in path.stem.replace("-", "_").split("_"))
    return name if name.isidentifier() else "MyScene"
