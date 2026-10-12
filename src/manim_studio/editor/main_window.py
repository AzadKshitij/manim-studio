"""Editor main window: code | preview split, timeline below, live shell dock.

This class is the orchestrator: it owns the widgets, the preview host controller,
the file watcher and the rendered-preview transport, and routes every event.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any

from PySide6.QtCore import QSettings, QSize, Qt
from PySide6.QtGui import QAction, QActionGroup, QCloseEvent, QKeyEvent, QKeySequence, QShowEvent
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QFileDialog,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMenu,
    QMessageBox,
    QSizePolicy,
    QSplitter,
    QStackedWidget,
    QStatusBar,
    QToolBar,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from manim_studio import APP_NAME, recent
from manim_studio.editor import api_cache, scene_files
from manim_studio.editor.autosave_controller import AutosaveController
from manim_studio.editor.block_inspector import BlockInfo, BlockInspector, describe_clips
from manim_studio.editor.code_editor import CodeEditor
from manim_studio.editor.completion_controller import CompletionController
from manim_studio.editor.editor_pane import EditorPane
from manim_studio.editor.edits_diff_dialog import EditsDiffDialog, unified_diff
from manim_studio.editor.export_controller import ExportController
from manim_studio.editor.export_dialog import ExportDialog
from manim_studio.editor.file_watcher import FileWatcher
from manim_studio.editor.glyphs import glyph, refresh_glyphs
from manim_studio.editor.icon import load_icon
from manim_studio.editor.palette_dialog import CommandPalette
from manim_studio.editor.palette_sources import build_items as palette_items
from manim_studio.editor.patch_controller import MIN_DURATION, PatchController
from manim_studio.editor.preview_controller import PreviewController
from manim_studio.editor.preview_events import PreviewEvents
from manim_studio.editor.preview_panel import PreviewPanel
from manim_studio.editor.session_state import SessionState
from manim_studio.editor.shell_panel import ShellPanel
from manim_studio.editor.stats_controller import StatsController
from manim_studio.editor.status_chip import StatusChip
from manim_studio.editor.styling import apply_theme
from manim_studio.editor.timeline_controller import MediaTransport
from manim_studio.editor.timeline_ops import TimelineOpActions
from manim_studio.editor.timeline_transport import TimelineTransport
from manim_studio.editor.timeline_widget import TimelineWidget
from manim_studio.editor.unsaved import (
    CANCEL,
    DISCARD_RECOVERY,
    RECOVER,
    SAVE,
    ask_discard,
    ask_recovery,
)
from manim_studio.editor.video_mode import VideoModeController
from manim_studio.editor.welcome_panel import WelcomePanel
from manim_studio.preview_status import CRASHED, OFF, RUNNING, PreviewStatus, summarize
from manim_studio.scene_runner import check_scene_file
from manim_studio.site_index import block_line_range
from manim_studio.stats import TimingStats, process_rss_bytes
from manim_studio.templates import SCENE_TEMPLATES, SNIPPET_TEMPLATES, find_template
from manim_studio.theme import (
    BUILTIN_THEMES,
    Theme,
    ThemeError,
    current_theme,
    discover_themes,
    load_theme,
    save_theme,
    set_current_theme,
    themes_dir,
)
from manim_studio.timeline_model import TimelineLog, format_duration
from manim_studio.timeline_ops import TimelineFacts, op_states

__all__ = ["MainWindow"]

_HOST_BY_STATE = {"starting": RUNNING, "stopped": OFF, "crashed": CRASHED}


class MainWindow(QMainWindow):
    """The application shell."""

    def __init__(
        self,
        path: str | Path | None = None,
        *,
        embedded: bool | None = None,
        preview_size: tuple[int, int] = (1280, 720),
        restore_settings: bool = True,
        settings: QSettings | None = None,
        auto_preview: bool = True,
        initial_frame: str = "end",
        completion: bool = True,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(APP_NAME)
        self.setWindowIcon(load_icon())
        self._preview_size = preview_size
        self._initial_frame = initial_frame
        self._path: Path | None = None
        self._auto_reload = True
        self._auto_preview = auto_preview
        self._restore_settings = restore_settings
        self._settings = settings or QSettings("ManimStudio", "ManimStudio")
        self._session = SessionState(self._settings, enabled=restore_settings)
        self._layout_restored = False
        if embedded is None:  # not chosen on the command line: keep the last mode
            embedded = self._session.embedded() is not False
        self._embedded = embedded
        self._theme_key = "builtin:chalkboard"
        if restore_settings:
            self._restore_theme()
        self._api_names = api_cache.load_api_names()
        # Toolbar actions by label (used by the control-surface tests).
        self._toolbar_actions: dict[str, QAction] = {}
        # Timeline edits (retimes, deletes) are anchored patches stored in a sidecar
        # next to the scene; they have their own undo stack (the timeline's Ctrl+Z).
        self.patches = PatchController(self)
        self._loop = False
        self._loop_paused = False
        self._was_playing = False
        self._playback_active = False
        self._speed = 1.0

        self.editor = CodeEditor(self)
        self._autosave = AutosaveController(self.editor, self)
        self.timings = TimingStats()
        self.preview_panel = PreviewPanel(self, timings=self.timings)
        self.preview_panel.set_mode(embedded=self._embedded)
        self.welcome_panel = WelcomePanel(self)
        self.timeline = TimelineWidget(self)
        self.controller = PreviewController(self, timings=self.timings)
        self.exports = ExportController(self)
        self.stats = StatsController(
            self.controller, self._editor_stats_snapshot, dialog_parent=self, parent=self
        )
        self.transport = MediaTransport(self)
        self.watcher = FileWatcher(parent=self)

        self._build_layout()
        self._build_actions()
        self._build_shell_dock()
        self._build_status_bar()
        self._update_scene_actions()
        self.video = VideoModeController(
            transport=self.transport,
            preview_panel=self.preview_panel,
            timeline=self.timeline,
            stop_playback=self.controller.stop_playback,
            status_line=self._set_status,
            parent=self,
        )
        self.events = PreviewEvents(
            editor=self.editor,
            timeline=self.timeline,
            preview_panel=self.preview_panel,
            shell=self.shell,
            controller=self.controller,
            initial_frame=self._initial_frame,
            status_line=self._set_status,
            refresh_latex=self.refresh_latex_indicator,
            video_mode_active=lambda: self.video.active,
            parent=self,
        )
        self._connect_signals()
        self._palette = CommandPalette(self, lambda: palette_items(self))

        self.editor.set_completions(self._api_names)
        self.completions = CompletionController(self.editor, enabled=completion, parent=self)
        if restore_settings:
            self._restore_geometry()
        if path is None and self._session.reopen_last_file:
            last = self._session.last_file()
            path = last if last is not None and last.is_file() else None
        if path is not None:
            self.open_path(path)
        # Filled in whichever page starts up, so the recents are ready on the welcome page.
        self.welcome_panel.set_recent(self.recent_files())

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_layout(self) -> None:
        horizontal = QSplitter(Qt.Orientation.Horizontal, self)
        self.editor_pane = EditorPane(self.editor, self)
        horizontal.addWidget(self.editor_pane)
        self._right_stack = QStackedWidget(self)
        self._right_stack.addWidget(self.preview_panel)
        self._right_stack.addWidget(self.welcome_panel)
        self._right_stack.setCurrentWidget(self.welcome_panel)
        horizontal.addWidget(self._right_stack)
        horizontal.setStretchFactor(0, 3)
        horizontal.setStretchFactor(1, 2)

        bottom = QWidget(self)
        bottom_layout = QVBoxLayout(bottom)
        bottom_layout.setContentsMargins(0, 0, 0, 0)
        self.transport_bar = TimelineTransport(self)
        self.transport_bar.setFocusProxy(self.timeline)  # toolbar clicks hand the keys over
        self._edits_label = self.transport_bar.edits_label
        bottom_layout.setSpacing(0)
        bottom_layout.addWidget(self.transport_bar)
        bottom_layout.addWidget(self.timeline, 1)  # the timeline takes the extra height
        self.inspector = BlockInspector(self)
        bottom_layout.addWidget(self.inspector)

        vertical = QSplitter(Qt.Orientation.Vertical, self)
        vertical.addWidget(horizontal)
        vertical.addWidget(bottom)
        vertical.setStretchFactor(0, 4)
        vertical.setStretchFactor(1, 1)
        self._horizontal, self._vertical = horizontal, vertical
        self.setCentralWidget(vertical)

    def _build_actions(self) -> None:
        menu_bar = self.menuBar()
        # One set of timeline operations: the menus, shortcuts, palette and timeline
        # menu all use these actions, so their names, shortcuts and states agree.
        self._timeline_ops = TimelineOpActions(self)
        self._timeline_ops.attach_to_window()
        self._timeline_ops.attach_to_timeline(self.transport_bar, self.timeline)
        self.timeline.set_operation_actions(self._timeline_ops.actions)

        file_menu = menu_bar.addMenu("&File")
        self._add_action(
            file_menu, "New scene…", self.new_scene_dialog, QKeySequence.StandardKey.New
        )
        self._add_action(file_menu, "Open…", self.open_dialog, QKeySequence.StandardKey.Open)
        self._recent_menu = file_menu.addMenu("Open recent")
        self._recent_menu.aboutToShow.connect(self._populate_recent_menu)
        self._add_action(file_menu, "Save", self.save_buffer, QKeySequence.StandardKey.Save)
        self._add_action(
            file_menu, "Save As…", self.save_buffer_as, QKeySequence.StandardKey.SaveAs
        )
        file_menu.addSeparator()
        self._scene_actions: list[QAction] = []
        self._scene_actions.append(
            self._add_action(file_menu, "Export…", self.export_dialog, "Ctrl+E")
        )
        self._scene_actions.append(
            self._add_action(
                file_menu,
                "Export still…",
                lambda: self.export_dialog(still=True),
                "Ctrl+Shift+E",
            )
        )
        file_menu.addSeparator()
        self._add_action(file_menu, "Quit", self.close, QKeySequence.StandardKey.Quit)

        edit_menu = menu_bar.addMenu("&Edit")
        # One undo stack for the code and the timeline: these reach timeline edits too.
        self._add_action(edit_menu, "Undo", self._undo_edit, QKeySequence.StandardKey.Undo)
        self._add_action(edit_menu, "Redo", self._redo_edit, "Ctrl+Y")
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
        self._scene_actions += [
            self._add_action(run_menu, "Reload preview", self.reload_preview, "F5"),
            self._add_action(
                run_menu, "Reload preview (discard cache)", self.reload_preview_fresh, "Ctrl+F5"
            ),
            self._add_action(run_menu, "Play from playhead", self.request_play, "F6"),
            self._add_action(run_menu, "Stop playback", self.request_stop, "F7"),
        ]
        run_menu.addSeparator()
        self._scene_actions.append(
            self._add_action(run_menu, "Render preview video", self.render_preview_video, "Ctrl+R")
        )
        self._add_action(run_menu, "Focus live shell", self.focus_shell, "Ctrl+`")

        timeline_menu = menu_bar.addMenu("&Timeline")
        operations = self._timeline_ops
        groups = (
            ("undo", "redo"),
            ("delete", "reset", "jump"),
            ("hold", "apply", "diff"),
            ("orphans", "discard_orphans", "discard_all"),
        )
        for number, group in enumerate(groups):
            if number:
                timeline_menu.addSeparator()
            timeline_menu.addActions([operations.action(key) for key in group])
        view_menu = menu_bar.addMenu("&View")
        self._add_action(view_menu, "Command palette…", self.open_command_palette, "Ctrl+Shift+P")
        view_menu.addSeparator()
        self._embedded_action = QAction("Embedded preview", self, checkable=True)
        self._embedded_action.setChecked(self._embedded)
        self._embedded_action.triggered.connect(self.set_embedded)
        view_menu.addAction(self._embedded_action)
        self._reopen_action = QAction("Reopen last file on launch", self, checkable=True)
        self._reopen_action.setChecked(self._session.reopen_last_file)
        self._reopen_action.toggled.connect(self._session.set_reopen_last_file)
        view_menu.addAction(self._reopen_action)
        self._add_action(view_menu, "Fit timeline", self.timeline.fit, "Ctrl+0")
        self._snap_action = QAction("Snap timeline", self, checkable=True)
        self._snap_action.setChecked(self.timeline.snapping)
        self._snap_action.setToolTip(
            "Snap scrubbing and retiming to frames, 0.1s and block boundaries"
        )
        self._snap_action.toggled.connect(self.timeline.set_snapping)
        view_menu.addAction(self._snap_action)
        self.transport_bar.bind_snap_action(self._snap_action)
        view_menu.addSeparator()
        self._theme_menu = view_menu.addMenu("Theme")
        self._theme_menu.aboutToShow.connect(self._populate_theme_menu)

        tools_menu = menu_bar.addMenu("&Tools")
        self._add_action(tools_menu, "LaTeX setup…", self.open_latex_setup)

        help_menu = menu_bar.addMenu("&Help")
        self._add_action(help_menu, "App stats…", self.stats.show_dialog)
        self._add_action(help_menu, "Keyboard shortcuts…", lambda: self.open_command_palette("?"))
        self._add_action(help_menu, "About", self.show_about)

        toolbar = QToolBar("Main", self)
        toolbar.setMovable(False)
        toolbar.setIconSize(QSize(18, 18))
        toolbar.setFloatable(False)
        # A fixed-height bar: growing the window must never stretch it.
        toolbar.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.addToolBar(toolbar)
        toolbar.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
        play = self._add_toolbar_action(toolbar, "Play", self.request_play, "play")
        reload_ = self._add_toolbar_action(toolbar, "Reload", self.reload_preview, "reload")
        stop = self._add_toolbar_action(toolbar, "Stop", self.request_stop, "stop")
        self._label_toolbar_button(toolbar, play, primary=True)
        self._label_toolbar_button(toolbar, reload_)
        self._label_toolbar_button(toolbar, stop)
        toolbar.addSeparator()
        toolbar.addWidget(QLabel("Scene"))
        self._scene_combo = QComboBox(self)
        self._scene_combo.setMinimumWidth(160)
        toolbar.addWidget(self._scene_combo)
        toolbar.addSeparator()
        render = self._add_toolbar_action(
            toolbar, "Render preview", self.render_preview_video, "render"
        )
        spacer = QWidget(self)
        spacer.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        toolbar.addWidget(spacer)
        export = self._add_toolbar_action(toolbar, "Export…", self.export_dialog, "export")
        self._label_toolbar_button(toolbar, export)
        self._scene_actions += [play, reload_, stop, render, export]

    def _build_shell_dock(self) -> None:
        self.shell = ShellPanel(self)
        self.shell.lineSubmitted.connect(self._on_shell_line)
        self.addDockWidget(Qt.DockWidgetArea.BottomDockWidgetArea, self.shell)

    def _build_status_bar(self) -> None:
        bar = QStatusBar(self)
        self.setStatusBar(bar)
        self._preview_state = PreviewStatus()
        self._status_chip = StatusChip(self)
        self._mem_label = QLabel("cache: —")
        self._mem_label.setToolTip("Filmstrip cache RAM and process memory (Help > App stats…)")
        self._latex_label = QLabel("")
        self._cursor_label = QLabel("Ln 1, Col 1")
        self._shell_button = QToolButton(self)
        self._shell_button.setAutoRaise(True)
        self._shell_button.setText("Live shell")
        self._shell_button.setToolTip("Show the live shell (Ctrl+`)")
        self._shell_button.clicked.connect(self.focus_shell)
        self.shell.unreadChanged.connect(self._on_shell_unread)
        bar.addPermanentWidget(self._shell_button)
        bar.addPermanentWidget(self._cursor_label)
        bar.addPermanentWidget(self._latex_label)
        bar.addPermanentWidget(self._mem_label)
        bar.addPermanentWidget(self._status_chip)
        self.refresh_latex_indicator()

    def _set_preview_state(self, **changes: Any) -> None:
        """Feed one fact to the status chip; the chip is the only place the state shows."""
        self._preview_state = replace(self._preview_state, **changes)
        self._status_chip.show_summary(summarize(self._preview_state))

    def _on_shell_unread(self, count: int) -> None:
        self._shell_button.setText(f"Live shell · {count}" if count else "Live shell")

    def refresh_latex_indicator(self) -> None:
        from manim_studio.latex import latex_status

        try:
            status = latex_status()
        except Exception:  # never break window construction for a PATH probe
            self._latex_label.setText("")
            return
        self._latex_label.setText(f"LaTeX: {'ok' if status.ok else 'missing'}")

    def open_latex_setup(self) -> None:
        from manim_studio.editor.latex_dialog import LatexSetupDialog
        from manim_studio.latex import latex_status

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
        self.welcome_panel.openRequested.connect(self.open_dialog)
        self.welcome_panel.newRequested.connect(self.new_scene_dialog)
        self.welcome_panel.recentRequested.connect(self.open_path)
        self.preview_panel.errorGotoLineRequested.connect(self.editor.go_to_line)
        self.preview_panel.errorReloadRequested.connect(self.reload_preview)
        self.preview_panel.playToggleRequested.connect(self._toggle_play)
        self.preview_panel.frameStepRequested.connect(self._on_transport_step)
        self.editor.saveRequested.connect(self.save_buffer)
        self.editor.fileDropped.connect(self.open_path)
        self.editor.cursorMoved.connect(self._on_cursor_moved)
        self.editor.modificationChanged.connect(self._update_title)
        self.timeline.scrubRequested.connect(self.scrub_to_time)
        self.timeline.playToggleRequested.connect(self._toggle_play)
        self.timeline.blockResized.connect(self._on_block_resized)
        self.timeline.blockActivated.connect(self._on_block_activated)
        self.timeline.blockDurationEditRequested.connect(self._edit_block_duration)
        self.timeline.rangeChanged.connect(self._on_range_changed)
        self.timeline.blockPlayRequested.connect(self._play_block)
        self.timeline.blockLoopRequested.connect(self._loop_block)
        self.timeline.blockExportRangeRequested.connect(self._export_block_range)
        self.timeline.blockResetTimingRequested.connect(self._reset_block_timing)
        self.timeline.blockStillRequested.connect(self._render_block_still)
        self.timeline.blockDeleteRequested.connect(self._delete_block)
        self.timeline.blockIterationEditRequested.connect(self._edit_block_iteration)
        self.timeline.undoRequested.connect(self._undo_edit)
        self.timeline.redoRequested.connect(self._redo_edit)
        self.transport_bar.playPauseRequested.connect(self._toggle_play)
        self.transport_bar.editRequested.connect(self._on_timeline_op)
        self._timeline_ops.requested.connect(self._on_timeline_op)
        self._timeline_ops.unavailable.connect(self._set_status)
        self.timeline.blockSelected.connect(self._on_block_selected)
        self.inspector.durationCommitted.connect(self._on_inspector_duration)
        self.transport_bar.stepRequested.connect(self._on_transport_step)
        self.transport_bar.jumpRequested.connect(self._on_transport_jump)
        self.transport_bar.loopToggled.connect(self._on_transport_loop)
        self.transport_bar.speedChanged.connect(self._on_transport_speed)
        self.transport_bar.zoomRequested.connect(self._on_zoom_requested)
        self.controller.statusChanged.connect(self._on_host_status)
        self.controller.stateChanged.connect(
            lambda state: self._set_preview_state(host=_HOST_BY_STATE.get(state, OFF))
        )
        self.controller.statusChanged.connect(
            lambda state, _message: self._set_preview_state(
                activity=state if state in ("playing", "seeking") else "idle"
            )
        )
        self.events.loadingStageChanged.connect(
            lambda stage: self._set_preview_state(loading=stage)
        )
        self.events.errorChanged.connect(lambda text: self._set_preview_state(error=text))
        self.controller.stateChanged.connect(self._on_host_state_changed)
        self.controller.stoppedReceived.connect(self._on_stopped_loop)
        self.controller.logReceived.connect(self._on_trace_log)
        self.patches.patchesChanged.connect(self._on_patches_changed)
        self.patches.stateChanged.connect(self._refresh_edits_ui)
        self.patches.recorded.connect(self._record_timeline_step)
        self.editor.containerUndone.connect(self._on_timeline_step_undone)
        self.editor.containerRedone.connect(self._on_timeline_step_redone)
        self.patches.message.connect(self._set_status)
        self._scene_combo.currentTextChanged.connect(self._on_scene_selected)
        self.controller.execResultReceived.connect(self.shell.append_exec_result)
        self.controller.filmstripReceived.connect(self.stats.on_filmstrip)
        self.controller.statsReceived.connect(self.stats.on_stats)
        self.stats.memoryLabelChanged.connect(self._mem_label.setText)
        self.stats.cacheStatusChanged.connect(self.preview_panel.set_cache_status)
        self.controller.hostLogReceived.connect(self.shell.append)
        self.watcher.file_changed.connect(self._on_file_changed)
        self.watcher.file_missing.connect(lambda path: self._set_status(f"file missing: {path}"))
        self.transport.failed.connect(lambda message: self._set_status(f"preview video: {message}"))
        self.exports.statusChanged.connect(self._set_status)
        self.exports.outputLine.connect(self.shell.append)
        self.exports.previewVideoReady.connect(self._on_preview_video_ready)
        self.exports.previewVideoFailed.connect(self._on_preview_video_failed)
        self.exports.exportFinished.connect(self._on_export_finished)
        self.editor.completionsRequested.connect(self._on_completions_requested)
        self.editor.signaturesRequested.connect(self._on_signatures_requested)
        self._refresh_edit_buttons()  # the timeline operations start in their true state

    # ------------------------------------------------------------------
    # Completion (Jedi worker)
    # ------------------------------------------------------------------

    def _on_cursor_moved(self, line: int, column: int) -> None:
        """Mirror the caret in the status bar; a caret that enters a call follows it."""
        self._cursor_label.setText(f"Ln {line}, Col {column}")
        index = self.events.block_at_line(line)
        if index is not None and index != self.timeline.active_block:
            self._follow_caret_block(index)
        self._refresh_caret_block(line)

    def _follow_caret_block(self, index: int) -> None:
        """A caret that enters a call selects its block and moves the playhead to it."""
        block = self.timeline.log.block_by_index(index)
        if block is None:
            return
        self.timeline.set_selected_block(index)
        if self._playback_active:
            return  # never interrupt playback because the caret moved
        if self._path is not None and self.controller.is_running and not self.video.active:
            self.scrub_to_time(block.start)
        else:
            self.timeline.set_playhead(block.start)

    def _refresh_caret_block(self, line: int) -> None:
        """Highlight the block under the caret (the playhead does not move)."""
        self.timeline.set_active_block(self.events.block_at_line(line))
        self._refresh_inspector()

    def _completion_path(self) -> str | None:
        return str(self._path) if self._path is not None else None

    def _on_completions_requested(self, source: str, offset: int) -> None:
        self.completions.request_completions(source, offset, self._completion_path())

    def _on_signatures_requested(self, source: str, offset: int) -> None:
        self.completions.request_signatures(source, offset, self._completion_path())

    def _warm_up_completion(self) -> None:
        """Prime Jedi's cache for the freshly loaded buffer in the background."""
        self.completions.warm_up(self.editor.text(), self._completion_path())

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

    @staticmethod
    def _label_toolbar_button(toolbar: QToolBar, action: QAction, *, primary: bool = False) -> None:
        """Show ``action`` as a bordered button with its text beside the icon."""
        button = toolbar.widgetForAction(action)
        if not isinstance(button, QToolButton):
            return
        button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        button.setProperty("labeled", True)
        if primary:
            button.setProperty("primary", True)
            action.setProperty("glyphOnAccent", True)
            action.setIcon(glyph(str(action.property("glyph")), on_accent=True))

    def _add_toolbar_action(
        self, toolbar: QToolBar, text: str, slot: Any, glyph_name: str | None = None
    ) -> QAction:
        action = QAction(text, self)
        if glyph_name is not None:
            action.setIcon(glyph(glyph_name))
            action.setProperty("glyph", glyph_name)
        action.setToolTip(text)
        action.triggered.connect(lambda checked=False: slot())
        toolbar.addAction(action)
        self._toolbar_actions[text] = action
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
        self.shell.focus_prompt()

    # Palette hooks: small read/act methods the command palette calls.

    def open_command_palette(self, prefix: str = "") -> None:
        self._palette.open_palette(prefix)

    def has_scene(self) -> bool:
        return self._path is not None

    def scene_names(self) -> tuple[str, ...]:
        return tuple(self._scene_combo.itemText(i) for i in range(self._scene_combo.count()))

    def current_scene_name(self) -> str:
        return self._scene_combo.currentText()

    def select_scene(self, name: str) -> None:
        index = self._scene_combo.findText(name)
        if index >= 0:
            self._scene_combo.setCurrentIndex(index)

    def jump_to_block(self, index: int) -> None:
        block = self.timeline.log.block_by_index(index)
        if block is None:
            return
        self.timeline.set_selected_block(index)
        self.scrub_to_time(block.start)
        self._on_block_activated(index)

    def set_embedded(self, checked: bool) -> None:
        self._embedded = bool(checked)
        self._session.remember_embedded(self._embedded)
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

    def open_path(self, path: str | Path, *, edits_settled: bool = False) -> bool:
        """Load a scene file into the editor and (re)start the preview.

        ``edits_settled`` says the caller has already asked about the unsaved edits.
        """
        target = Path(path)
        check = check_scene_file(target)
        if not check.ok:
            self._set_status(f"cannot open: {check.error}")
            self.shell.append(f"open failed: {target}: {check.error}", reveal=True)
            self._forget_recent(target)
            return False
        if not edits_settled and not self._confirm_discard("open another scene"):
            return False
        self.editor.load_file(target)
        self._path = target.resolve()
        # Edits made on the timeline travel with the scene: the host loads its first
        # trace from argv, so the controller must know them before it starts.
        self.controller.patches = self.patches.open_scene(self._path)
        self.watcher.watch(self._path)
        self._refresh_scene_list(self._path)
        self._restore_scene_choice(self._path)
        self._update_title()
        self._autosave.set_path(self._path)
        self._offer_recovery()
        self._remember_recent(self._path)
        self._session.remember_last_file(self._path)
        self._right_stack.setCurrentWidget(self.preview_panel)
        self._update_scene_actions()
        self._set_status(f"opened {self._path.name} ({len(check.scenes)} scene(s))")
        self._warm_up_completion()
        if self._auto_reload:
            self._start_preview()
        return True

    # Recent files ------------------------------------------------------

    def recent_files(self) -> list[str]:
        """Remembered scenes, newest first, minus files that no longer exist."""
        if not self._restore_settings:
            return []
        raw: object = self._settings.value("recent/files", [])
        if isinstance(raw, str):
            entries = [raw]
        elif isinstance(raw, list):
            entries = [str(entry) for entry in raw]
        else:
            entries = []
        return recent.prune_recent(entries)

    def _remember_recent(self, path: Path) -> None:
        if not self._restore_settings:
            return
        self._settings.setValue("recent/files", recent.push_recent(self.recent_files(), path))

    def _forget_recent(self, path: Path) -> None:
        if not self._restore_settings:
            return
        key = str(path.resolve()).casefold()
        kept = [e for e in self.recent_files() if str(Path(e).resolve()).casefold() != key]
        self._settings.setValue("recent/files", kept)

    def _populate_recent_menu(self) -> None:
        self._recent_menu.clear()
        entries = self.recent_files()
        for entry in entries:
            action = self._recent_menu.addAction(Path(entry).name)
            action.setToolTip(entry)
            action.triggered.connect(lambda checked=False, p=entry: self.open_path(p))
        if not entries:
            self._recent_menu.addAction("(none)").setEnabled(False)

    def _update_scene_actions(self) -> None:
        """Enable scene-dependent actions only while a scene is open."""
        has_scene = self._path is not None
        self._set_preview_state(scene_open=has_scene)
        for action in self._scene_actions:
            action.setEnabled(has_scene)
            tip = action.text().replace("&", "")
            action.setToolTip(tip if has_scene else f"{tip} — open a scene first")

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
        suggested = scene_files.scene_name_from_path(Path(f"{template_key}.py"))
        scene_name = self._prompt_scene_name(suggested)
        if scene_name is None:
            return
        default_name = f"{scene_files.snake_case(scene_name)}.py"
        path, _ = QFileDialog.getSaveFileName(
            self, "Create scene", str(Path.cwd() / default_name), "Python files (*.py)"
        )
        if not path:
            return
        # Settle unsaved edits before writing anything, so a cancel leaves no file behind.
        if not self._confirm_discard("create a new scene"):
            return
        target = self.create_scene_file(path, template_key, scene_name)
        self.open_path(target, edits_settled=True)

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
            if scene_files.is_valid_scene_name(name):
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
    ) -> Path:
        """Write a scene template to ``path``, deriving the class name when omitted."""
        return scene_files.create_scene_file(path, template_key, scene_name)

    def _restore_scene_choice(self, path: Path) -> None:
        """Select the scene this file last used; not a user choice, so it saves nothing."""
        saved = self._session.scene_for(path)
        index = self._scene_combo.findText(saved) if saved else -1
        if index >= 0 and index != self._scene_combo.currentIndex():
            self._scene_combo.blockSignals(True)
            try:
                self._scene_combo.setCurrentIndex(index)
            finally:
                self._scene_combo.blockSignals(False)

    def _offer_recovery(self) -> None:
        """Offer the snapshot a crash left behind, when it holds code the disk does not."""
        recovered = self._autosave.pending_recovery()
        if recovered is None or self._path is None:
            return
        choice = ask_recovery(self, self._path.name)
        self._autosave.release()
        if choice == RECOVER:
            self.editor.replace_text(recovered)
            self._set_status("recovered unsaved changes — save to keep them")
        elif choice == DISCARD_RECOVERY:
            self._autosave.discard()

    def _confirm_discard(self, action: str) -> bool:
        """Before ``action`` replaces or closes the buffer: True when it may go ahead."""
        if not scene_files.has_unsaved_changes(
            self._path, self.editor.text(), self.editor.is_modified()
        ):
            return True
        name = self._path.name if self._path is not None else "untitled scene"
        choice = ask_discard(self, name, action)
        if choice == CANCEL:
            return False
        if choice == SAVE:
            self.save_buffer()
            # Save As can be cancelled, which leaves the work unsaved: stay put then.
            return not scene_files.has_unsaved_changes(
                self._path, self.editor.text(), self.editor.is_modified()
            )
        self._autosave.discard()  # the edits are being thrown away
        return True

    def save_buffer(self) -> None:
        if self._path is None:
            self.save_buffer_as()
            return
        # Compare the buffer to the file *before* writing.  The preview host reads
        # from disk, so a save only warrants a re-render when the code actually
        # changed.  ``is_modified()`` is Scintilla's save-point flag; it stays set
        # after retyping identical text, so content equality is the real check.
        changed = not scene_files.buffer_matches_disk(self._path, self.editor.text())
        if not self.editor.is_modified() and not changed:
            self._set_status(f"no changes — {self._path.name} is up to date")
            return
        self.editor.save_file(self._path)
        self._autosave.discard()
        self.watcher.note_save()
        self._update_title()
        self._set_status(f"saved {self._path.name}")
        if changed and self._auto_reload and self.controller.is_running:
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
        self.controller.start(
            self._path,
            self._scene_name(),
            embedded=self._embedded,
            size=self._preview_size,
            initial_frame=self._initial_frame,
        )

    def reload_preview_fresh(self) -> None:
        """Reload and throw away every cached frame (e.g. after editing an image asset)."""
        self.reload_preview(fresh=True)

    def reload_preview(self, *, fresh: bool = False) -> None:
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
        if fresh:
            self.controller.load(self._path, self._scene_name(), fresh=True)
        else:
            self.controller.load(self._path, self._scene_name())

    def request_play(self) -> None:
        if not self.controller.is_running:
            self._start_preview()
            return
        self._loop_paused = False
        self.video.exit()
        index = self._play_start_index()
        to_index: int | None = None
        if self._loop:
            bounds = self._loop_bounds()
            if bounds is not None:
                index, to_index = bounds
        self._announce_play_start(index)
        self.controller.play(from_index=index, to_index=to_index, rate=self._speed)
        self._set_transport_playing(True)  # optimistic; the host status confirms

    # ------------------------------------------------------------------
    # Transport toolbar
    # ------------------------------------------------------------------

    def _on_transport_step(self, frames: int) -> None:
        """Step the playhead by ``frames`` (negative = back): the buttons step one at a time."""
        if self._path is None or not self.controller.is_running:
            return
        self.scrub_to_time(self.timeline.playhead_time + frames * self.timeline.frame_step)

    def _on_transport_jump(self, where: str) -> None:
        if self._path is None or not self.controller.is_running:
            return
        self.scrub_to_time(0.0 if where == "start" else self.events.total_duration)

    def _on_host_status(self, state: str, _message: dict[str, Any]) -> None:
        """Keep the play/pause label honest across loop cycles.

        A loop replays in the stopped handler, so the host reports ``idle`` for a
        moment at each cycle boundary before the next cycle's ``playing``.  The
        button should stay on Pause through that gap - but only when a cycle was
        actually rolling into the next one.  An idle host with the loop merely
        enabled (never playing) must still read Play.
        """
        if state == "playing":
            self._was_playing = True
            self._set_transport_playing(True)
            return
        if state == "idle" and self._was_playing and self._loop and not self._loop_paused:
            return  # a loop cycle ended; the replay is about to resume
        self._was_playing = False
        self._set_transport_playing(False)

    def _on_host_state_changed(self, state: str) -> None:
        """Reset playback intent when the host dies or shuts down."""
        if state in ("crashed", "stopped"):
            self._loop_paused = True  # never auto-replay after a crash/stop
            self._was_playing = False
            self._set_transport_playing(False)
        self._refresh_edit_buttons()  # running or not decides which operations apply

    def _on_transport_loop(self, enabled: bool) -> None:
        self._loop = bool(enabled)
        self.timeline.set_looping(self._loop)
        self.transport.set_looping(self._loop)
        # Enabling the loop clears any earlier pause intent; disabling it is an
        # explicit "stop repeating", so it cancels a pending replay too.
        self._loop_paused = not self._loop
        self._set_status("loop on" if self._loop else "loop off")

    def _on_transport_speed(self, speed: float) -> None:
        self._speed = float(speed) if float(speed) > 0 else 1.0
        self.transport.set_rate(self._speed)
        if self._playback_active:
            # The host honours the rate at play time; restart so it applies now.
            self.request_stop()
            self.request_play()
        else:
            self._set_status(f"playback speed {self._speed:g}x")

    def _on_range_changed(self, start: float, end: float) -> None:
        self._set_status(f"range {format_duration(start)} - {format_duration(end)}")

    def _on_zoom_requested(self, action: str) -> None:
        """Zoom buttons: fit / fit-selection / zoom in / zoom out."""
        if action == "fit":
            self.timeline.fit()
        elif action == "selection":
            self.timeline.fit_selection()
        elif action == "in":
            self.timeline.zoom_by(1.25)
        elif action == "out":
            self.timeline.zoom_by(0.8)

    def insert_hold_at_playhead(self, duration: float | None = None) -> None:
        """Insert a ``self.wait(...)`` at the playhead's block boundary.

        manimgl can only pause between whole ``play()``/``wait()`` calls, so the
        hold is placed at the first block boundary at or after the playhead (or
        after the last block when the playhead is at the end).  The wait becomes
        real source code, so the trace, preview and export all agree.
        """
        if self._path is None or not self.controller.is_running:
            self._set_status("open and preview a scene first")
            return
        log = self.timeline.log
        if log.is_empty:
            self._set_status("nothing to hold: the trace is empty")
            return
        if duration is None:
            duration, accepted = QInputDialog.getDouble(
                self,
                "Insert hold",
                "Hold duration in seconds:",
                0.5,
                1.0 / 30.0,
                3600.0,
                3,
            )
            if not accepted:
                return
        boundary = next(
            (block for block in log.blocks if block.start >= self.timeline.playhead_time - 1e-6),
            None,
        )
        if boundary is not None:
            source_line, before = self.events.call_line(boundary.index), True
        else:
            source_line, before = self.events.call_line(log.blocks[-1].index), False
        if source_line is None:
            self._set_status("cannot place the hold: no known source line")
            return
        line, text = scene_files.wait_insertion(
            self.editor.text(), source_line, duration, before=before
        )
        self.editor.insert_line_at(line, text)
        self.save_buffer()
        self._set_status(
            f"inserted self.wait({scene_files.format_seconds(duration)}) at the playhead"
        )

    def _play_block(self, index: int) -> None:
        """Context menu: replay from this block."""
        if self._path is None or not self.controller.is_running:
            return
        self.events.play_from_index = index
        self.request_play()

    def _loop_block(self, index: int) -> None:
        """Context menu: toggle looping this block (clicking again undoes it)."""
        block = self.timeline.log.block_by_index(index)
        if block is None:
            return
        if self.timeline.block_is_looped(index):
            # The block is already the loop region: undo the loop.
            self.timeline.clear_range()
            self.transport_bar.set_loop(False)  # emits loopToggled -> disables looping
            return
        self.timeline.set_range(block.start, block.end)
        self.events.play_from_index = index
        self.transport_bar.set_loop(True)  # emits loopToggled -> enables looping
        if self.controller.is_running:
            self.request_play()

    def _export_block_range(self, index: int) -> None:
        """Context menu: preselect this block as the export range."""
        block = self.timeline.log.block_by_index(index)
        if block is None:
            return
        self.timeline.set_range(block.start, block.end)
        self.export_dialog(initial_range=(index, index))

    def _reset_block_timing(self, index: int) -> None:
        """Context menu: drop every timeline edit of this block's call."""
        if self.patches.reset_block(index):
            self._set_status(f"block {index}: timeline edits reset")
        else:
            self._set_status(f"block {index} has no timeline edits")

    def _render_block_still(self, index: int) -> None:
        """Context menu: render the frame at the end of this block."""
        if self._path is None:
            self._set_status("open a scene first")
            return
        output = self._path.parent / ".manim-studio" / f"{self._path.stem}_block{index}.png"
        self.exports.export_still(
            self._path,
            self._scene_name(),
            output,
            animation_range=(0, index + 1),
        )

    def _on_stopped_loop(self, message: dict[str, Any]) -> None:
        """Repeat the loop range when playback ends on its own."""
        if not self._loop or self._loop_paused or message.get("user_stopped"):
            return
        if self.controller.is_running:
            self.request_play()

    def _loop_bounds(self) -> tuple[int, int] | None:
        """Inclusive ``(first, last)`` block indexes to loop, or ``None``."""
        log = self.timeline.log
        if log.is_empty:
            return None
        selection = self.timeline.range()
        if selection is None:
            return (0, log.num_blocks - 1)
        start, end = selection
        first = log.index_at(start)
        last = log.index_at(max(0.0, end - 1e-6))
        if first is None or last is None:
            return (0, log.num_blocks - 1)
        if last < first:
            first, last = last, first
        return (first, last)

    def _play_start_index(self) -> int:
        """Block that playback resumes from.

        manimgl starts a replay at a whole animation block
        (``start_at_animation_number``), so a block is the finest precision a
        live replay can begin at.  The stored block — kept in step with the
        playhead by scrub and seek — is the start; a fresh trace (no selection)
        replays from the beginning.
        """
        if self.events.play_from_index is None:
            return 0
        return self.events.play_from_index

    def _announce_play_start(self, index: int) -> None:
        """Move the playhead to the block's true start so the timeline agrees.

        Playback always begins at a block boundary, so a mid-block playhead is
        snapped forward/back to that boundary rather than implying a sub-block
        resume the host cannot perform.
        """
        block = self.timeline.log.block_by_index(index)
        if block is None:
            return
        self.events.play_from_index = index
        self.timeline.set_playhead(block.start)
        self.preview_panel.set_time(block.start, self.events.total_duration)
        self._set_status(f"playing from block {index} · {format_duration(block.start)}")

    def request_stop(self) -> None:
        # A user stop/pause wins over an in-flight loop replay.
        self._loop_paused = True
        self.controller.stop_playback()
        self._set_transport_playing(False)

    def _set_transport_playing(self, playing: bool) -> None:
        """Single source of truth for the play/pause button and its click action.

        Kept separate from ``events.playing`` because a loop boundary briefly
        reports ``idle`` while the button must stay usable as Pause.
        """
        self._playback_active = bool(playing)
        self.transport_bar.set_playing(self._playback_active)

    def _on_scene_selected(self, name: str) -> None:
        if self._path is not None and name:
            self._session.remember_scene(self._path, name)
        if self._path is not None and name and self.controller.is_running:
            self.controller.load(self._path, name)

    def _toggle_play(self) -> None:
        if self._playback_active:
            self.request_stop()
        else:
            self.request_play()

    def seek_to_block(self, index: int) -> None:
        """Jump to the end state of a block (used by menus/tests; drag uses scrub)."""
        self.video.exit()
        self.events.play_from_index = index
        block = self.timeline.log.block_by_index(index)
        if block is not None:
            self.timeline.set_playhead(block.end)
            self.preview_panel.set_time(block.end, self.events.total_duration)
        self.controller.seek(index)

    def scrub_to_time(self, time: float) -> None:
        """Continuous scrub: pose the live scene at exactly this time."""
        if self._path is None or not self.controller.is_running:
            return
        self.video.exit()
        limit = max(self.events.total_duration, 0.0)
        position = min(max(float(time), 0.0), limit)
        self.events.play_from_index = self.timeline.log.index_at(position)
        self.timeline.set_playhead(position)
        self._refresh_inspector()
        self.preview_panel.set_time(position, self.events.total_duration)
        self.controller.scrub(position)

    # ------------------------------------------------------------------
    # Timeline retiming
    # ------------------------------------------------------------------

    def _on_trace_log(self, message: dict[str, Any]) -> None:
        """A fresh trace tells us which call each block came from and which edits failed."""
        self.patches.on_log(message)
        # A fresh trace means new call sites: re-highlight the caret's block.
        self._refresh_caret_block(self.editor.current_line())

    def _on_block_resized(
        self, index: int, duration: float, *, iteration_only: bool = False
    ) -> None:
        if self._path is None or not self.controller.is_running:
            return
        targets = self.patches.affected_blocks(index, iteration_only=iteration_only)
        sibling = self.patches.sibling_duration_source(index)
        sibling_block = None if sibling is None else self.timeline.log.block_by_index(sibling)
        if not self.patches.set_block_duration(
            index,
            duration,
            iteration_only=iteration_only,
            sibling_duration=None if sibling_block is None else sibling_block.duration,
        ):
            return
        # Show the stretch at once; the host's reload (cause "patches") then confirms it.
        log = self.timeline.log
        for target in targets:
            log = log.scaled_block_duration(target, duration)
        self._show_projected_log(log)
        count = len(targets)
        suffix = f" (all {count} iterations)" if count > 1 else ""
        self._set_status(f"block {index} → {format_duration(duration)}{suffix}")

    def _show_projected_log(self, log: TimelineLog) -> None:
        """Adopt a locally computed timeline until the host's trace replaces it."""
        self.timeline.set_log(log, keep_view=True)
        self.events.total_duration = log.total_duration
        position = min(self.timeline.playhead_time, log.total_duration)
        self.timeline.set_playhead(position)
        self.preview_panel.set_time(position, log.total_duration)
        self._refresh_edits_ui()

    def _prompt_duration(self, index: int, title: str) -> float | None:
        block = self.timeline.log.block_by_index(index)
        if block is None:
            return None
        duration, accepted = QInputDialog.getDouble(
            self,
            title,
            f"Block {index} · {block.name}\nduration in seconds:",
            block.duration,
            MIN_DURATION,
            3600.0,
            3,
        )
        return duration if accepted else None

    def _edit_block_duration(self, index: int) -> None:
        """Double-click a block: prompt for an exact duration and retime it."""
        if self._path is None or not self.controller.is_running:
            return
        duration = self._prompt_duration(index, "Block duration")
        if duration is not None:
            self._on_block_resized(index, duration)

    def _edit_block_iteration(self, index: int) -> None:
        """Context menu: retime just this iteration of a loop body."""
        if self._path is None or not self.controller.is_running:
            return
        duration = self._prompt_duration(index, "Duration of this iteration")
        if duration is not None:
            self._on_block_resized(index, duration, iteration_only=True)

    def _delete_block(self, index: int) -> None:
        """Context menu: remove this block's call from the scene (a timeline edit)."""
        if self._path is None or not self.controller.is_running:
            return
        if self.patches.delete_block(index):
            self._set_status(f"block {index}: call deleted (timeline edit, not yet in the source)")

    def _on_block_activated(self, index: int) -> None:
        """Ctrl+double-click on a block: jump the editor to its call site."""
        line = self.events.call_line(index)
        if line is not None:
            self.timeline.set_active_block(index)
            self.editor.go_to_line(line)
            first, last = block_line_range(self.editor.text(), line)
            self.editor.highlight_lines(first, last)
            self._set_status(f"block {index} → line {line}")
        else:
            self._set_status(f"block {index} has no known source line")

    def _undo_edit(self) -> None:
        """Undo the latest change, to the code or to the timeline: there is one stack."""
        self.editor.undo()
        self.editor.settle_saved_state()
        self._refresh_edit_buttons()

    def _redo_edit(self) -> None:
        self.editor.redo()
        self.editor.settle_saved_state()
        self._refresh_edit_buttons()

    def _record_timeline_step(self) -> None:
        """A timeline change joins the editor's undo stack, so one Ctrl+Z can reach it."""
        self.editor.addUndoAction(0, 0)
        self.editor.settle_saved_state()  # no text changed, so the file is still saved

    def _on_timeline_step_undone(self) -> None:
        if self.patches.undo():
            self._set_status("timeline edit undone")
        else:
            self._set_status("timeline history out of step: nothing was undone")

    def _on_timeline_step_redone(self) -> None:
        if self.patches.redo():
            self._set_status("timeline edit redone")
        else:
            self._set_status("timeline history out of step: nothing was redone")

    # ------------------------------------------------------------------
    # Timeline edits: persistence, host sync and writing them into the source
    # ------------------------------------------------------------------

    def _on_patches_changed(self, payload: Any) -> None:
        """The edit set changed: run the scene with it (the controller also remembers it)."""
        self.controller.set_patches(list(payload), self.timeline.playhead_time)

    def _refresh_edits_ui(self) -> None:
        """Status chip, menu states and the per-block markers follow the edit state."""
        summary = self.patches.summary()
        self._edits_label.setText(summary)
        self._edits_label.setVisible(bool(summary))
        count = self.timeline.log.num_blocks
        self.timeline.set_block_markers(
            {i: self.patches.iteration_count(i) for i in range(count)},
            [i for i in range(count) if self.patches.block_is_edited(i)],
        )
        self._refresh_inspector()  # also enables the timeline operations that apply now

    def selected_block_index(self) -> int | None:
        """The block toolbar operations act on: clicked, else caret, else playhead."""
        index = self.timeline.selected_block
        if index is None:
            index = self.timeline.active_block
        if index is None:
            index = self.timeline.log.index_at(self.timeline.playhead_time)
        return index

    def _refresh_edit_buttons(self) -> None:
        """Enable only the timeline operations that apply right now."""
        index = self.selected_block_index()
        running = self._path is not None and self.controller.is_running
        editable = running and index is not None and self.patches.block_ref(index) is not None
        states = op_states(
            TimelineFacts(
                running=running,
                scene_open=self._path is not None,
                has_log=not self.timeline.log.is_empty,
                block=index,
                block_editable=editable,
                block_has_line=index is not None and self.events.call_line(index) is not None,
                block_edited=index is not None and self.patches.block_is_edited(index),
                can_undo=self.editor.canUndo(),
                can_redo=self.editor.canRedo(),
                edit_count=self.patches.edit_count,
                orphan_count=self.patches.orphan_count,
            )
        )
        self._timeline_ops.apply(states)
        self.transport_bar.set_operation_states(states)

    def _on_timeline_op(self, action_id: str) -> None:
        """Run a timeline operation (from its button, a menu, a shortcut or the palette)."""
        index = self.selected_block_index()
        if action_id == "undo":
            self._undo_edit()
        elif action_id == "redo":
            self._redo_edit()
        elif action_id == "delete" and index is not None:
            self._delete_block(index)
        elif action_id == "reset" and index is not None:
            self._reset_block_timing(index)
        elif action_id == "jump" and index is not None:
            self._on_block_activated(index)
        elif action_id == "hold":
            self.insert_hold_at_playhead()
        elif action_id == "apply":
            self.apply_edits_to_source()
        elif action_id == "diff":
            self.show_edits_diff()
        elif action_id == "orphans":
            self.show_orphan_menu()
        elif action_id == "discard_orphans":
            self.discard_orphaned_edits()
        elif action_id == "discard_all":
            self.discard_all_edits()

    def _on_block_selected(self, index: object) -> None:
        """A block was clicked on the timeline (``None`` = clicked empty space)."""
        self._refresh_inspector()

    def inspector_text(self) -> str:
        """Details of the selected block: caret block, else the one under the playhead."""
        log = self.timeline.log
        index = self.selected_block_index()
        block = None if index is None else log.block_by_index(index)
        if block is None:
            return ""
        parts = [f"#{block.index} {block.name}", format_duration(block.duration)]
        line = self.events.call_line(block.index)
        if line is not None:
            parts.append(f"line {line}")
        clips = self.events.clips(block.index)
        if clips:
            parts.append(", ".join(f"{c.animation}({c.target}) {c.duration:.2f}s" for c in clips))
        loops = self.patches.iteration_count(block.index)
        if loops > 1:
            parts.append(f"loop \u00d7{loops}")
        if self.patches.block_is_edited(block.index):
            parts.append("edited (not in the source)")
        return "  ·  ".join(parts)

    def _refresh_inspector(self) -> None:
        self.inspector.show_block(self._block_info())
        self._refresh_edit_buttons()

    def _block_info(self) -> BlockInfo | None:
        """What the inspector shows for the selected block."""
        index = self.selected_block_index()
        block = None if index is None else self.timeline.log.block_by_index(index)
        if index is None or block is None:
            return None
        running = self._path is not None and self.controller.is_running
        return BlockInfo(
            index=index,
            name=block.name,
            duration=block.duration,
            line=self.events.call_line(index),
            animations=describe_clips(self.events.clips(index)),
            loops=self.patches.iteration_count(index),
            edited=self.patches.block_is_edited(index),
            duration_kind=self.patches.duration_kind(index),
            editable=running and self.patches.block_ref(index) is not None,
        )

    def _on_inspector_duration(self, index: int, seconds: float, iteration_only: bool) -> None:
        """The inspector's duration field was committed."""
        self._on_block_resized(index, seconds, iteration_only=iteration_only)

    def apply_edits_to_source(self) -> None:
        """Write the timeline edits into the scene file (one undoable editor edit)."""
        if self._path is None:
            return
        if self.editor.is_modified():
            self._set_status("save the file first (Ctrl+S): edits are applied to the saved code")
            return
        plan = self.patches.plan_apply(self.editor.text())
        if plan is None or not plan.changed:
            self._set_status("no timeline edit can be written to the source")
            return
        left_over = self.patches.edit_count - len(plan.applied)
        # One undo step: the text, the saved file and the timeline edits come back together.
        self.editor.beginUndoAction()
        try:
            self.editor.replace_text(plan.text)
            self.editor.save_file(self._path)
            self.watcher.note_save()
            self._update_title()
            # The edits are now code: drop them from the sidecar; the host reloads once
            # (through patchesChanged) with the new source and whatever could not be baked.
            self.patches.finish_apply(plan)
        finally:
            self.editor.endUndoAction()
        note = f"applied {len(plan.applied)} timeline edit(s) to {self._path.name}"
        if left_over:
            note += f"; {left_over} could not be written and stay as timeline edits"
        self._set_status(note)

    def show_edits_diff(self) -> None:
        """Preview what 'Apply timeline edits to source' would change."""
        if self._path is None:
            return
        source = self.editor.text()
        plan = self.patches.plan_apply(source)
        if plan is None:
            self._set_status("no timeline edits to show")
            return
        notes = [orphan.reason for orphan in plan.orphaned]
        dialog = EditsDiffDialog(unified_diff(source, plan.text, self._path.name), notes, self)
        dialog.exec()

    def orphan_menu(self) -> QMenu:
        """The orphan tray: every edit whose call is gone, each with its reason."""
        menu = QMenu(self)
        for orphan in self.patches.orphans:
            action = menu.addAction(
                f"{self.patches.describe(orphan.patch)}  \u2014  {orphan.reason}"
            )
            action.triggered.connect(
                lambda _checked=False, patch=orphan.patch: self.patches.discard_edit(patch)
            )
        if self.patches.orphans:
            menu.addSeparator()
            menu.addAction("Discard all orphaned edits").triggered.connect(
                self.discard_orphaned_edits
            )
        else:
            menu.addAction("No orphaned edits").setEnabled(False)
        return menu

    def show_orphan_menu(self) -> None:
        button = self.transport_bar.edit_button("orphans")
        self.orphan_menu().exec(button.mapToGlobal(button.rect().bottomLeft()))

    def discard_orphaned_edits(self) -> None:
        if self.patches.discard_orphans():
            self._set_status("orphaned timeline edits discarded")

    def discard_all_edits(self) -> None:
        if self.patches.clear():
            self._set_status("all timeline edits discarded (Timeline > Undo brings them back)")

    # ------------------------------------------------------------------
    # Export / preview video
    # ------------------------------------------------------------------

    def preview_video_path(self) -> Path | None:
        if self._path is None:
            return None
        return scene_files.preview_video_path(self._path)

    def render_preview_video(self) -> None:
        if self._path is None:
            self._set_status("open a scene first")
            return
        self.exports.render_preview_video(
            self._path,
            self._scene_name(),
            scene_files.preview_video_path(self._path),
        )

    def export_dialog(
        self,
        *,
        still: bool = False,
        initial_range: tuple[int, int] | None = None,
    ) -> None:
        if self._path is None:
            self._set_status("open a scene first")
            return
        scene_names = tuple(self._scene_combo.itemText(i) for i in range(self._scene_combo.count()))
        dialog = ExportDialog(
            self,
            scene_path=self._path,
            scene_names=scene_names,
            current_scene=self._scene_name(),
            block_count=self.timeline.log.num_blocks,
            settings=self._settings,
            initial_still=still,
            initial_range=initial_range,
            controller=self.exports,
        )
        dialog.exec()

    def export_video_to(
        self, output: str | Path, *, quality: str = "high", fps: int | None = None
    ) -> None:
        if self._path is None:
            self._set_status("open a scene first")
            return
        self.exports.export_video(
            self._path,
            self._scene_name(),
            output,
            quality=quality,
            fps=fps,
        )

    def _on_preview_video_ready(self, output: Path) -> None:
        self.transport.load(output)
        self._set_status(f"preview video ready: {output}")

    def _on_preview_video_failed(self, exit_code: int) -> None:
        self._report_failure(f"preview render failed (exit {exit_code})")

    def _on_export_finished(self, exit_code: int) -> None:
        if exit_code == 0:
            self._set_status("export finished")
        else:
            self._report_failure(f"export failed (exit {exit_code})")

    def _report_failure(self, text: str) -> None:
        """A failed render or export must not be missed: name it and open the live shell."""
        self._set_status(text)
        self.shell.append(f"{text}; the lines above say why", reveal=True)

    # ------------------------------------------------------------------
    # Stats snapshot
    # ------------------------------------------------------------------

    def _editor_stats_snapshot(self) -> dict[str, Any]:
        return {
            "editor_rss": process_rss_bytes(),
            "api_names": len(self._api_names),
            "blocks": self.timeline.log.num_blocks,
            "total_duration": self.events.total_duration,
            "preview_size": self._preview_size,
            "frames_received": self.controller.frames_received,
            "timings": self.timings.snapshot(),
        }

    # ------------------------------------------------------------------
    # File watching
    # ------------------------------------------------------------------

    def _on_file_changed(self, path: str) -> None:
        if self.editor.is_modified():
            self._set_status("file changed on disk — save (Ctrl+S) to overwrite, or reopen")
            return
        if scene_files.read_disk_text(Path(path)) == self.editor.text():
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

    def _on_shell_line(self, code: str) -> None:
        if not self.controller.exec_code(code):
            self.shell.append("(preview host is not running)")

    # ------------------------------------------------------------------
    # Misc
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # Themes
    # ------------------------------------------------------------------

    def _theme_for_key(self, key: str) -> Theme | None:
        kind, _, value = key.partition(":")
        if kind == "builtin":
            return BUILTIN_THEMES.get(value)
        if kind == "file":
            try:
                return load_theme(value)
            except ThemeError:
                return None
        return None

    def _restore_theme(self) -> None:
        """Apply the theme saved by the last session (before any widget is built)."""
        key = str(self._settings.value("theme", self._theme_key))
        theme = self._theme_for_key(key)
        if theme is None:
            return
        self._theme_key = key
        app = QApplication.instance()
        if isinstance(app, QApplication):
            apply_theme(app, theme)
        else:
            set_current_theme(theme)

    def set_theme(self, theme: Theme, key: str) -> None:
        """Switch the whole editor to ``theme`` and remember it for the next start."""
        self._theme_key = key
        app = QApplication.instance()
        if isinstance(app, QApplication):
            apply_theme(app, theme)
        else:
            set_current_theme(theme)
        self.editor.apply_theme(theme)
        self.preview_panel.apply_theme(theme)
        self.inspector.apply_theme()
        refresh_glyphs(self)
        self.timeline.update()
        if self._restore_settings:
            # Only a real session remembers the theme; tests and scripted windows must not
            # overwrite the user's saved choice.
            self._settings.setValue("theme", key)
            self._settings.sync()
        self._set_status(f"Theme: {theme.name}")

    def _populate_theme_menu(self) -> None:
        menu = self._theme_menu
        menu.clear()
        group = QActionGroup(menu)
        group.setExclusive(True)

        def add_choice(label: str, key: str) -> None:
            action = QAction(label, menu, checkable=True)
            action.setChecked(key == self._theme_key)
            action.triggered.connect(lambda checked=False, k=key: self._choose_theme(k))
            group.addAction(action)
            menu.addAction(action)

        for slug, theme in BUILTIN_THEMES.items():
            add_choice(theme.name, f"builtin:{slug}")
        user_themes = discover_themes()
        if user_themes:
            menu.addSeparator()
            for stem, path in user_themes.items():
                add_choice(stem, f"file:{path}")
        menu.addSeparator()
        self._add_action(menu, "Load theme file…", self.load_theme_file)
        self._add_action(menu, "Save current theme as…", self.save_current_theme)

    def _choose_theme(self, key: str) -> None:
        theme = self._theme_for_key(key)
        if theme is None:
            self._set_status("Could not load that theme")
            return
        self.set_theme(theme, key)

    def load_theme_file(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Load theme", str(themes_dir()), "Theme files (*.json)"
        )
        if not path:
            return
        try:
            theme = load_theme(path)
        except ThemeError as exc:
            QMessageBox.warning(self, "Load theme", str(exc))
            return
        self.set_theme(theme, f"file:{path}")

    def save_current_theme(self) -> None:
        theme = current_theme()
        folder = themes_dir()
        suggested = str(folder / f"{theme.name.lower().replace(' ', '-')}.json")
        path, _ = QFileDialog.getSaveFileName(self, "Save theme", suggested, "Theme files (*.json)")
        if not path:
            return
        try:
            Path(path).parent.mkdir(parents=True, exist_ok=True)
            save_theme(theme, path)
        except OSError as exc:
            QMessageBox.warning(self, "Save theme", f"Could not save the theme: {exc}")
            return
        self._set_status(f"Saved theme to {path}")

    def _set_status(self, text: str) -> None:
        self.statusBar().showMessage(text)

    def keyPressEvent(self, event: QKeyEvent) -> None:
        # Only keys that no widget took arrive here: a shortcut that is off says why.
        self._timeline_ops.explain_unavailable(QApplication.focusWidget(), event)
        super().keyPressEvent(event)

    def _update_title(self) -> None:
        self._set_preview_state(dirty=self.editor.is_modified())
        if self._path is None:
            self.setWindowTitle(APP_NAME)
            self.editor_pane.set_file(None)
            return
        self.editor_pane.set_file(self._path.name, modified=self.editor.is_modified())
        marker = " •" if self.editor.is_modified() else ""
        self.setWindowTitle(f"{self._path.name}{marker} — {APP_NAME}")

    def _restore_geometry(self) -> None:
        geometry = self._settings.value("window/geometry")
        if geometry is not None:
            self.restoreGeometry(geometry)

    def showEvent(self, event: QShowEvent) -> None:
        super().showEvent(event)
        if not self._layout_restored:
            # Splitters only take saved sizes once the window has its real size.
            self._layout_restored = True
            if self._session.restore_layout(horizontal=self._horizontal, vertical=self._vertical):
                self.shell.show()

    def closeEvent(self, event: QCloseEvent) -> None:
        if not self._confirm_discard("close the editor"):
            event.ignore()
            return
        self._autosave.discard()
        self._session.remember_layout(
            horizontal=self._horizontal,
            vertical=self._vertical,
            shell_visible=not self.shell.isHidden(),
        )
        self._session.remember_embedded(self._embedded)
        self.watcher.stop()
        self.completions.stop()
        self.controller.shutdown()
        self.exports.shutdown()
        self.transport.stop()
        if self._restore_settings:
            self._settings.setValue("window/geometry", self.saveGeometry())
        event.accept()
