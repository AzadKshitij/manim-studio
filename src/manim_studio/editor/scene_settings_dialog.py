"""Scene settings dialog: size, orientation, frame rate, background and palette.

The dialog only edits a :class:`SceneSettings` value; the rules (ranges, colour format,
the portrait/landscape maths) live in :mod:`manim_studio.scene_settings`, so what the
dialog accepts is exactly what the preview and exports can render.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QCheckBox,
    QColorDialog,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from manim_studio.scene_settings import (
    DEFAULT_FPS,
    MAX_FPS,
    MAX_SIDE,
    MIN_FPS,
    MIN_SIDE,
    RESOLUTION_PRESETS,
    SceneSettings,
    SettingsError,
    normalize_hex,
    resolution_preset_for,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

    from manim_studio.palettes import Palette

__all__ = ["SceneSettingsDialog"]

_CUSTOM = "custom"
_NO_PALETTE = ""


class SceneSettingsDialog(QDialog):
    """Edit one scene's settings; read the result from :meth:`settings` after ``exec()``."""

    def __init__(
        self,
        parent: QWidget | None,
        *,
        scene_name: str,
        settings: SceneSettings,
        palettes: Sequence[Palette],
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"Scene settings: {scene_name}")
        self.setMinimumWidth(440)
        self._palettes = {palette.key: palette for palette in palettes}
        self._background = settings.background_color
        self._swatches = settings.swatches
        self._palette_key = settings.palette
        self._result: SceneSettings | None = None

        self._preset_combo = QComboBox(self)
        for preset in RESOLUTION_PRESETS:
            self._preset_combo.addItem(preset.title, preset.key)
        self._preset_combo.addItem("Custom…", _CUSTOM)

        self._width = QSpinBox(self)
        self._height = QSpinBox(self)
        for spin in (self._width, self._height):
            spin.setRange(MIN_SIDE, MAX_SIDE)
            spin.setSuffix(" px")
        self._rotate = QPushButton("Rotate 90°", self)
        self._rotate.setToolTip("Swap width and height (landscape ↔ portrait)")
        size_row = QHBoxLayout()
        size_row.addWidget(self._width)
        size_row.addWidget(QLabel("x", self))
        size_row.addWidget(self._height)
        size_row.addWidget(self._rotate)

        self._fps = QSpinBox(self)
        self._fps.setRange(MIN_FPS, MAX_FPS)
        self._fps.setSuffix(" fps")
        fps_note = QLabel(
            "Exports use this frame rate. The live preview runs at manimgl's window rate.",
            self,
        )
        fps_note.setWordWrap(True)
        fps_note.setObjectName("panelMeta")

        self._background_button = QPushButton(self)
        self._background_button.setToolTip("Choose the background colour")
        self._opacity = QDoubleSpinBox(self)
        self._opacity.setRange(0.0, 1.0)
        self._opacity.setSingleStep(0.05)
        self._opacity.setDecimals(2)

        self._palette_combo = QComboBox(self)
        self._palette_combo.addItem("None", _NO_PALETTE)
        for palette in palettes:
            self._palette_combo.addItem(palette.title, palette.key)
        self._swatch_row = QWidget(self)
        swatch_layout = QHBoxLayout(self._swatch_row)
        swatch_layout.setContentsMargins(0, 0, 0, 0)
        self._swatch_layout = swatch_layout

        self._default_check = QCheckBox("Use these settings for new scenes", self)

        form = QFormLayout()
        form.addRow("Resolution:", self._preset_combo)
        form.addRow("Size:", size_row)
        form.addRow("Frame rate:", self._fps)
        form.addRow("", fps_note)
        form.addRow("Background:", self._background_button)
        form.addRow("Opacity:", self._opacity)
        form.addRow("Palette:", self._palette_combo)
        form.addRow("Swatches:", self._swatch_row)
        form.addRow("", self._default_check)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel,
            self,
        )
        buttons.accepted.connect(self._accept_checked)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(buttons)

        self._load(settings)
        self._preset_combo.currentIndexChanged.connect(self._on_preset_chosen)
        self._width.valueChanged.connect(self._on_size_edited)
        self._height.valueChanged.connect(self._on_size_edited)
        self._rotate.clicked.connect(self._on_rotate)
        self._background_button.clicked.connect(self._choose_background)
        self._palette_combo.currentIndexChanged.connect(self._on_palette_chosen)

    # ------------------------------------------------------------------
    # Result
    # ------------------------------------------------------------------

    def settings(self) -> SceneSettings:
        """The settings as edited.  Raises :class:`SettingsError` if they are not valid."""
        return SceneSettings(
            width=self._width.value(),
            height=self._height.value(),
            fps=self._fps.value(),
            background_color=self._background,
            background_opacity=self._opacity.value(),
            palette=str(self._palette_combo.currentData() or ""),
            swatches=self._swatches,
        )

    def use_as_default(self) -> bool:
        return self._default_check.isChecked()

    # ------------------------------------------------------------------
    # Loading and editing
    # ------------------------------------------------------------------

    def _load(self, settings: SceneSettings) -> None:
        for widget in (
            self._preset_combo,
            self._width,
            self._height,
            self._fps,
            self._opacity,
            self._palette_combo,
        ):
            widget.blockSignals(True)
        try:
            self._width.setValue(settings.width)
            self._height.setValue(settings.height)
            self._fps.setValue(settings.fps or DEFAULT_FPS)
            self._opacity.setValue(settings.background_opacity)
            index = self._palette_combo.findData(settings.palette or _NO_PALETTE)
            self._palette_combo.setCurrentIndex(max(index, 0))
            self._sync_preset()
        finally:
            for widget in (
                self._preset_combo,
                self._width,
                self._height,
                self._fps,
                self._opacity,
                self._palette_combo,
            ):
                widget.blockSignals(False)
        self._show_background()
        self._show_swatches()

    def _sync_preset(self) -> None:
        """Select the preset matching the current size, or Custom when none does."""
        preset = resolution_preset_for(self._width.value(), self._height.value())
        index = self._preset_combo.findData(preset.key if preset else _CUSTOM)
        self._preset_combo.setCurrentIndex(index)

    def _on_preset_chosen(self, index: int) -> None:
        key = str(self._preset_combo.itemData(index))
        if key == _CUSTOM:
            return
        preset = next(p for p in RESOLUTION_PRESETS if p.key == key)
        for spin, value in ((self._width, preset.width), (self._height, preset.height)):
            spin.blockSignals(True)
            spin.setValue(value)
            spin.blockSignals(False)

    def _on_size_edited(self, *_args: object) -> None:
        self._sync_preset()

    def _on_rotate(self) -> None:
        width, height = self._width.value(), self._height.value()
        self._width.setValue(height)
        self._height.setValue(width)
        self._sync_preset()

    def _on_palette_chosen(self, index: int) -> None:
        key = str(self._palette_combo.itemData(index) or _NO_PALETTE)
        if key == _NO_PALETTE:
            self._palette_key = _NO_PALETTE
            self._swatches = ()
            return
        palette = self._palettes[key]
        self._palette_key = palette.key
        self._background = palette.background
        self._swatches = palette.swatches
        self._opacity.setValue(1.0)
        self._show_background()
        self._show_swatches()

    def _choose_background(self) -> None:
        chosen = QColorDialog.getColor(QColor(self._background), self, "Background colour")
        if not chosen.isValid():
            return
        self._background = normalize_hex(chosen.name())
        self._show_background()

    def _show_background(self) -> None:
        self._background_button.setText(self._background)
        self._background_button.setStyleSheet(
            f"background-color: {self._background}; color: {_contrasting_text(self._background)};"
        )

    def _show_swatches(self) -> None:
        while self._swatch_layout.count():
            item = self._swatch_layout.takeAt(0)
            widget = item.widget() if item is not None else None
            if widget is not None:
                widget.deleteLater()
        for colour in self._swatches:
            chip = QFrame(self._swatch_row)
            chip.setFixedSize(22, 22)
            chip.setToolTip(colour)
            chip.setStyleSheet(f"background-color: {colour}; border: 1px solid #00000040;")
            self._swatch_layout.addWidget(chip)
        self._swatch_layout.addStretch(1)

    def _accept_checked(self) -> None:
        try:
            self._result = self.settings()
        except SettingsError as exc:
            QMessageBox.warning(self, "Scene settings", str(exc))
            return
        self.accept()


def _contrasting_text(background: str) -> str:
    """Black or white text, whichever reads better on ``background``."""
    red, green, blue = (int(background[i : i + 2], 16) for i in (1, 3, 5))
    brightness = (red * 299 + green * 587 + blue * 114) / 1000
    return "#000000" if brightness > 140 else "#FFFFFF"
