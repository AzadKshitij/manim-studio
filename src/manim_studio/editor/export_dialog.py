"""Export dialog: format, resolution, fps, animation range, output path.

Pure front-end: the dialog never spawns processes itself.  All long-running
work belongs to :class:`manim_studio.editor.export_controller.ExportController`
(the single owner of export ``QProcess`` instances); this dialog builds an
:class:`~manim_studio.export.ExportPlan` from its fields, hands it to the
controller, and mirrors the controller's ``outputLine`` / ``exportFinished``
signals into a log tail, a busy progress bar and — on success — buttons to open
the produced file or its folder.  Output lines are also forwarded to the main
window's Live shell through the controller's existing ``outputLine`` wiring.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from pathlib import Path

from PySide6.QtCore import QSettings, Qt, QUrl
from PySide6.QtGui import QCloseEvent, QDesktopServices
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from manim_studio.editor.export_controller import ExportController
from manim_studio.export import ExportError, ExportPlan, build_export_plan

__all__ = ["ExportDialog"]

#: (label, mode key, extension) — mode key selects the manimgl flags' meaning.
_FORMATS: tuple[tuple[str, str, str], ...] = (
    ("MP4 video", "mp4", ".mp4"),
    ("GIF animation", "gif", ".gif"),
    ("PNG image (still frame)", "png", ".png"),
)

#: (label, quality key or "custom") for the resolution combo.
_RESOLUTIONS: tuple[tuple[str, str], ...] = (
    ("Low (854x480)", "low"),
    ("Medium (1280x720)", "medium"),
    ("High (1920x1080)", "high"),
    ("4K (3840x2160)", "4k"),
    ("Custom…", "custom"),
)

_KNOWN_SUFFIXES = {".mp4", ".gif", ".png"}
_LAST_DIR_KEY = "export/last_dir"


class ExportDialog(QDialog):
    """One dialog for every export need: choose, run, watch, reveal."""

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        scene_path: str | Path,
        scene_names: Sequence[str] = (),
        current_scene: str | None = None,
        block_count: int = 0,
        settings: QSettings | None = None,
        initial_still: bool = False,
        controller: ExportController | None = None,
        plan_builder: Callable[..., ExportPlan] = build_export_plan,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Export scene")
        self.resize(580, 520)
        self._scene_path = Path(scene_path)
        self._scene_names = list(scene_names)
        self._current_scene = current_scene
        self._block_count = max(0, int(block_count))
        self._settings = settings
        self._controller = controller
        self._plan_builder = plan_builder
        self._plan: ExportPlan | None = None
        self._last_output: Path | None = None
        self._state = "idle"
        self._succeeded = False
        self._cancelled = False

        self._build_ui(initial_still=initial_still)
        self._connect_signals()
        self._refresh()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self, *, initial_still: bool) -> None:
        layout = QVBoxLayout(self)

        form = QFormLayout()
        self._scene_combo = QComboBox(self)
        self._scene_combo.addItems(self._scene_names)
        if self._scene_names:
            index = 0
            if self._current_scene in self._scene_names:
                index = self._scene_names.index(self._current_scene)
            self._scene_combo.setCurrentIndex(index)
        form.addRow("Scene:", self._scene_combo)

        self._format_combo = QComboBox(self)
        for label, mode, _extension in _FORMATS:
            self._format_combo.addItem(label, mode)
        if initial_still:
            self._format_combo.setCurrentIndex(
                next(i for i, (_label, mode, _ext) in enumerate(_FORMATS) if mode == "png")
            )
        form.addRow("Format:", self._format_combo)

        resolution_wrap = QVBoxLayout()
        self._resolution_combo = QComboBox(self)
        for label, key in _RESOLUTIONS:
            self._resolution_combo.addItem(label, key)
        self._resolution_combo.setCurrentIndex(
            next(i for i, (_label, key) in enumerate(_RESOLUTIONS) if key == "high")
        )
        resolution_wrap.addWidget(self._resolution_combo)
        custom_row = QHBoxLayout()
        self._custom_width = QSpinBox(self)
        self._custom_width.setRange(1, 9999)
        self._custom_width.setValue(1920)
        custom_row.addWidget(QLabel("Width:", self))
        custom_row.addWidget(self._custom_width)
        self._custom_height = QSpinBox(self)
        self._custom_height.setRange(1, 9999)
        self._custom_height.setValue(1080)
        custom_row.addWidget(QLabel("Height:", self))
        custom_row.addWidget(self._custom_height)
        custom_row.addStretch(1)
        self._custom_widget = QWidget(self)
        self._custom_widget.setLayout(custom_row)
        self._custom_widget.setVisible(False)
        resolution_wrap.addWidget(self._custom_widget)
        form.addRow("Resolution:", resolution_wrap)

        self._fps_spin = QSpinBox(self)
        self._fps_spin.setRange(0, 240)
        self._fps_spin.setSpecialValueText("scene default")
        self._fps_spin.setToolTip("Frame rate (1..240); 0 uses the scene's default")
        form.addRow("Frame rate:", self._fps_spin)

        range_row = QHBoxLayout()
        self._whole_scene_check = QCheckBox("Use entire animation", self)
        self._whole_scene_check.setChecked(True)
        self._from_spin = QSpinBox(self)
        self._to_spin = QSpinBox(self)
        if self._block_count > 1:
            self._from_spin.setRange(0, self._block_count - 1)
            self._to_spin.setRange(0, self._block_count - 1)
            self._to_spin.setValue(self._block_count - 1)
        range_row.addWidget(self._whole_scene_check)
        range_row.addWidget(QLabel("from block", self))
        range_row.addWidget(self._from_spin)
        range_row.addWidget(QLabel("to block", self))
        range_row.addWidget(self._to_spin)
        range_row.addStretch(1)
        self._range_widget = QWidget(self)
        self._range_widget.setLayout(range_row)
        self._range_widget.setEnabled(self._block_count > 1)
        form.addRow("Animation range:", self._range_widget)

        path_row = QHBoxLayout()
        self._path_edit = QLineEdit(self)
        self._path_edit.setPlaceholderText("Output file path")
        self._browse_button = QPushButton("Browse…", self)
        path_row.addWidget(self._path_edit, 1)
        path_row.addWidget(self._browse_button)
        form.addRow("Output path:", path_row)
        layout.addLayout(form)

        self._open_folder_check = QCheckBox("Open the output folder when the export finishes", self)
        layout.addWidget(self._open_folder_check)

        self._summary_label = QLabel(self)
        self._summary_label.setWordWrap(True)
        self._summary_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self._summary_label)

        status_row = QHBoxLayout()
        self._progress = QProgressBar(self)
        self._progress.setRange(0, 1)
        self._progress.setValue(0)
        self._progress.setTextVisible(False)
        self._status_label = QLabel("Ready", self)
        status_row.addWidget(self._progress, 1)
        status_row.addWidget(self._status_label)
        layout.addLayout(status_row)

        self._log_view = QPlainTextEdit(self)
        self._log_view.setReadOnly(True)
        self._log_view.setMaximumBlockCount(500)
        self._log_view.setPlaceholderText("Export output will appear here…")
        layout.addWidget(self._log_view, 1)

        result_row = QHBoxLayout()
        self._open_file_button = QPushButton("Open file", self)
        self._open_folder_button = QPushButton("Open folder", self)
        result_row.addWidget(self._open_file_button)
        result_row.addWidget(self._open_folder_button)
        result_row.addStretch(1)
        self._result_row = QWidget(self)
        self._result_row.setLayout(result_row)
        self._result_row.setVisible(False)
        layout.addWidget(self._result_row)

        button_row = QHBoxLayout()
        self._export_button = QPushButton("Export", self)
        self._export_button.setDefault(True)
        self._cancel_button = QPushButton("Cancel export", self)
        self._cancel_button.setVisible(False)
        self._close_button = QPushButton("Close", self)
        self._close_button.clicked.connect(self.close)
        button_row.addStretch(1)
        button_row.addWidget(self._export_button)
        button_row.addWidget(self._cancel_button)
        button_row.addWidget(self._close_button)
        layout.addLayout(button_row)

        # Start with the default output path in the remembered directory.
        self._path_edit.setText(str(self._default_output_path(self._format_extension())))

    def _connect_signals(self) -> None:
        self._format_combo.currentIndexChanged.connect(self._on_format_changed)
        self._resolution_combo.currentIndexChanged.connect(self._on_resolution_changed)
        self._fps_spin.valueChanged.connect(self._refresh)
        self._whole_scene_check.toggled.connect(self._refresh)
        self._from_spin.valueChanged.connect(self._refresh)
        self._to_spin.valueChanged.connect(self._refresh)
        self._custom_width.valueChanged.connect(self._refresh)
        self._custom_height.valueChanged.connect(self._refresh)
        self._path_edit.textChanged.connect(self._refresh)
        self._browse_button.clicked.connect(self._browse)
        self._open_file_button.clicked.connect(self._open_file)
        self._open_folder_button.clicked.connect(self._open_folder)
        self._export_button.clicked.connect(self._start_export)
        self._cancel_button.clicked.connect(self._cancel_export)
        if self._controller is not None:
            self._controller.outputLine.connect(self._append_log)
            self._controller.exportFinished.connect(self._on_export_finished)

    # ------------------------------------------------------------------
    # Field accessors
    # ------------------------------------------------------------------

    def _scene_name(self) -> str | None:
        name = self._scene_combo.currentText().strip()
        return name or None

    def _format_mode(self) -> str:
        return str(self._format_combo.currentData())

    def _format_extension(self) -> str:
        index = self._format_combo.currentIndex()
        return _FORMATS[index][2] if 0 <= index < len(_FORMATS) else ".mp4"

    def _is_still(self) -> bool:
        return self._format_mode() == "png"

    def _is_gif(self) -> bool:
        return self._format_mode() == "gif"

    def _quality(self) -> str:
        key = str(self._resolution_combo.currentData())
        return "high" if key == "custom" else key

    def _resolution(self) -> tuple[int, int] | None:
        if str(self._resolution_combo.currentData()) != "custom":
            return None
        return self._custom_width.value(), self._custom_height.value()

    def _fps(self) -> int | None:
        if self._is_still() or self._fps_spin.value() == 0:
            return None
        return self._fps_spin.value()

    def _animation_range(self) -> tuple[int, int] | None:
        if self._is_still() or self._whole_scene_check.isChecked() or self._block_count < 2:
            return None
        return self._from_spin.value(), self._to_spin.value()

    # ------------------------------------------------------------------
    # Plan building + validation
    # ------------------------------------------------------------------

    def _build_plan(self) -> ExportPlan:
        output_text = self._path_edit.text().strip()
        if not output_text:
            raise ExportError("choose an output path")
        output = Path(output_text)
        suffix = output.suffix.lower()
        expected = self._format_extension()
        if suffix and suffix != expected:
            raise ExportError(
                f"output extension {suffix!r} does not match the format (expected {expected})"
            )
        return self._plan_builder(
            self._scene_path,
            self._scene_name(),
            output=output,
            quality=self._quality(),
            fps=self._fps(),
            still=self._is_still(),
            gif=self._is_gif(),
            resolution=self._resolution(),
            animation_range=self._animation_range(),
        )

    def _refresh(self, *_args: object) -> None:
        """Recompute the plan summary and (de)activate the Export button."""
        still = self._is_still()
        self._fps_spin.setEnabled(not still)
        self._range_widget.setEnabled(not still and self._block_count > 1)
        self._custom_widget.setVisible(str(self._resolution_combo.currentData()) == "custom")
        if still:
            self._fps_spin.setValue(0)
            self._whole_scene_check.setChecked(True)

        error: str | None = None
        try:
            self._plan = self._build_plan()
        except ExportError as exc:
            self._plan = None
            error = str(exc)
        if self._plan is not None:
            self._summary_label.setText(self._plan.command_line())
        else:
            self._summary_label.setText(error or "choose an output path")
        self._export_button.setEnabled(self._plan is not None and self._state == "idle")

    # ------------------------------------------------------------------
    # Path handling
    # ------------------------------------------------------------------

    def _default_output_path(self, extension: str) -> Path:
        directory = self._last_dir() or self._scene_path.parent
        return directory / f"{self._scene_path.stem}{extension}"

    def _browse(self) -> None:
        current = self._path_edit.text().strip()
        directory = str(Path(current).parent) if current else str(self._scene_path.parent)
        path, _ = QFileDialog.getSaveFileName(self, "Export to", directory, self._file_filter())
        if path:
            self._path_edit.setText(path)
            self._remember_dir(Path(path).parent)

    def _file_filter(self) -> str:
        return (
            "Video and image files (*.mp4 *.gif *.png);;"
            "MP4 video (*.mp4);;GIF animation (*.gif);;PNG image (*.png)"
        )

    def _on_format_changed(self, *_args: object) -> None:
        current = self._path_edit.text().strip()
        if Path(current).suffix.lower() in _KNOWN_SUFFIXES:
            self._path_edit.setText(str(Path(current).with_suffix(self._format_extension())))
        self._refresh()

    def _on_resolution_changed(self, *_args: object) -> None:
        self._refresh()

    def _remember_dir(self, directory: Path) -> None:
        if self._settings is not None:
            self._settings.setValue(_LAST_DIR_KEY, str(directory))

    def _last_dir(self) -> Path | None:
        if self._settings is None:
            return None
        value = self._settings.value(_LAST_DIR_KEY)
        return Path(str(value)) if value else None

    # ------------------------------------------------------------------
    # Running the export (through the controller)
    # ------------------------------------------------------------------

    def _start_export(self) -> None:
        if self._state == "running":
            return
        if self._plan is None:
            self._refresh()
        plan = self._plan
        if plan is None:
            return
        if self._controller is None:
            self._set_status("export is not available")
            return
        self._log_view.clear()
        self._remember_dir(plan.output_path.parent)
        if not self._controller.export_plan(plan):
            self._set_status("an export is already running")
            return
        self._last_output = plan.output_path
        self._succeeded = False
        self._cancelled = False
        self._set_state("running")
        self._set_status(f"exporting to {plan.output_path} …")

    def _cancel_export(self) -> None:
        self._cancelled = True
        if self._controller is not None:
            self._controller.cancel_export()
        self._set_status("cancelling…")

    def _on_export_finished(self, exit_code: int) -> None:
        if self._cancelled:
            self._cancelled = False
            self._last_output = None
            self._set_status("export cancelled")
            self._set_state("idle")
            return
        self._succeeded = exit_code == 0
        self._set_status(
            "export finished" if self._succeeded else f"export failed (exit {exit_code})"
        )
        self._set_state("done")
        if self._succeeded and self._open_folder_check.isChecked():
            self._open_folder()

    def _append_log(self, line: str) -> None:
        if line.strip():
            self._log_view.appendPlainText(line)

    def _set_state(self, state: str) -> None:
        self._state = state
        running = state == "running"
        done = state == "done"
        self._export_button.setEnabled(state == "idle" and self._plan is not None)
        self._cancel_button.setVisible(running)
        self._close_button.setEnabled(not running)
        if running:
            self._progress.setRange(0, 0)
        else:
            self._progress.setRange(0, 1)
            self._progress.setValue(1 if done and self._succeeded else 0)
        self._result_row.setVisible(done and self._succeeded)

    def _set_status(self, text: str) -> None:
        self._status_label.setText(text)

    # ------------------------------------------------------------------
    # Revealing the result
    # ------------------------------------------------------------------

    def _open_file(self) -> None:
        output = self._last_output
        if output is not None and output.is_file():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(output)))

    def _open_folder(self) -> None:
        output = self._last_output
        if output is not None:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(output.parent)))

    def closeEvent(self, event: QCloseEvent) -> None:
        if self._state == "running":
            self._cancel_export()
        super().closeEvent(event)
