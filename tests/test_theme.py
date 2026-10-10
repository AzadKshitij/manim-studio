"""Theme model, theme files, and the Qt palette/style sheet built from a theme."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from manim_studio.theme import (
    BUILTIN_THEMES,
    CHALKBOARD,
    LANE_COUNT,
    THEME_FORMAT,
    ThemeError,
    contrast_ratio,
    derive,
    discover_themes,
    ink_on,
    load_theme,
    mix,
    normalize_hex,
    save_theme,
    theme_from_dict,
    theme_to_dict,
    themes_dir,
    with_alpha,
)


class TestColourHelpers:
    def test_normalize_hex(self) -> None:
        assert normalize_hex("#ABC") == "#aabbcc"
        assert normalize_hex("58C4DD") == "#58c4dd"
        assert normalize_hex(" #58c4dd ") == "#58c4dd"
        for bad in ("#12", "#12345g", "", None, 5):
            assert normalize_hex(bad) is None

    def test_mix_and_alpha(self) -> None:
        assert mix("#000000", "#ffffff", 0.5) == "#808080"
        assert mix("#102030", "#102030", 0.7) == "#102030"
        assert with_alpha("#ff0000", 0.5) == "rgba(255, 0, 0, 128)"

    def test_contrast_ratio_extremes(self) -> None:
        assert contrast_ratio("#000000", "#ffffff") == pytest.approx(21.0)
        assert contrast_ratio("#777777", "#777777") == pytest.approx(1.0)

    def test_ink_on_picks_readable_text(self) -> None:
        assert ink_on("#ffd866") == "#0b0d10"
        assert ink_on("#101010") == "#ffffff"


class TestBuiltinThemes:
    @pytest.mark.parametrize("slug", sorted(BUILTIN_THEMES))
    def test_text_contrast_meets_wcag_aa(self, slug: str) -> None:
        theme = BUILTIN_THEMES[slug]
        c = theme.colors
        for surface in (c.bg, c.surface, c.editor, c.raised):
            assert contrast_ratio(c.text, surface) >= 4.5, surface
        assert contrast_ratio(c.muted, c.surface) >= 4.5
        assert contrast_ratio(c.on_accent, c.accent) >= 4.5
        for surface in (c.bg, c.surface):
            assert contrast_ratio(c.icon, surface) >= 4.5, "icon"
            assert contrast_ratio(c.icon_active, surface) >= 3.0, "icon_active"
            assert contrast_ratio(c.icon_disabled, surface) >= 2.0, "icon_disabled"
        for token in ("keyword", "string", "number", "function", "class_", "builtin"):
            assert contrast_ratio(getattr(theme.syntax, token), c.editor) >= 4.5, token
        assert contrast_ratio(theme.syntax.comment, c.editor) >= 3.0

    @pytest.mark.parametrize("slug", sorted(BUILTIN_THEMES))
    def test_has_six_lane_colours(self, slug: str) -> None:
        assert len(BUILTIN_THEMES[slug].lanes) == LANE_COUNT

    def test_derived_colours_exist(self) -> None:
        derived = derive(CHALKBOARD)
        assert derived.selection.startswith("#")
        assert derived.accent_soft.startswith("rgba(")


class TestThemeFiles:
    def test_round_trip(self, tmp_path: Path) -> None:
        path = tmp_path / "chalk.json"
        save_theme(CHALKBOARD, path)
        assert load_theme(path) == CHALKBOARD

    def test_file_uses_camel_case_keys(self) -> None:
        data = theme_to_dict(CHALKBOARD)
        assert data["format"] == THEME_FORMAT
        assert "borderStrong" in data["colors"]
        assert "onAccent" in data["colors"]
        assert data["syntax"]["class"] == CHALKBOARD.syntax.class_
        assert data["metrics"]["controlHeight"] == CHALKBOARD.metrics.control_height

    def test_partial_theme_falls_back_to_chalkboard(self) -> None:
        theme = theme_from_dict(
            {"format": THEME_FORMAT, "name": "Mine", "colors": {"accent": "#ff00aa"}}
        )
        assert theme.name == "Mine"
        assert theme.colors.accent == "#ff00aa"
        assert theme.colors.bg == CHALKBOARD.colors.bg

    def test_invalid_values_are_ignored_and_metrics_clamped(self) -> None:
        theme = theme_from_dict(
            {
                "format": THEME_FORMAT,
                "colors": {"accent": "blue", "bg": "#ABC"},
                "lanes": ["#111111", "nope"],
                "metrics": {"radius": 99, "density": "wide", "fontSize": 13},
            }
        )
        assert theme.colors.accent == CHALKBOARD.colors.accent
        assert theme.colors.bg == "#aabbcc"
        assert theme.lanes[0] == "#111111"
        assert theme.lanes[1] == CHALKBOARD.lanes[1]
        assert theme.metrics.radius == 12
        assert theme.metrics.density == CHALKBOARD.metrics.density

    def test_icon_colours_round_trip_as_camel_case(self) -> None:
        data = theme_to_dict(CHALKBOARD)
        assert data["colors"]["iconActive"] == CHALKBOARD.colors.icon_active
        assert data["colors"]["iconDisabled"] == CHALKBOARD.colors.icon_disabled

    def test_file_without_icon_colours_gets_readable_ones(self) -> None:
        light = {
            "format": THEME_FORMAT,
            "mode": "light",
            "colors": {
                "bg": "#ffffff",
                "surface": "#f5f5f5",
                "text": "#111111",
                "accent": "#0a6f8c",
            },
        }
        theme = theme_from_dict(light)
        assert contrast_ratio(theme.colors.icon, theme.colors.bg) >= 4.5
        assert theme.colors.icon_active == "#0a6f8c"
        given = theme_from_dict({**light, "colors": {**light["colors"], "icon": "#123456"}})
        assert given.colors.icon == "#123456"

    def test_foreign_json_is_rejected(self, tmp_path: Path) -> None:
        path = tmp_path / "other.json"
        path.write_text(json.dumps({"colors": {}}), encoding="utf-8")
        with pytest.raises(ThemeError, match="not a Manim Studio theme"):
            load_theme(path)

    def test_bad_json_and_missing_file(self, tmp_path: Path) -> None:
        broken = tmp_path / "broken.json"
        broken.write_text("{", encoding="utf-8")
        with pytest.raises(ThemeError, match="not valid JSON"):
            load_theme(broken)
        with pytest.raises(ThemeError, match="cannot read"):
            load_theme(tmp_path / "missing.json")

    def test_discover_themes(self, tmp_path: Path) -> None:
        save_theme(CHALKBOARD, tmp_path / "b.json")
        save_theme(CHALKBOARD, tmp_path / "a.json")
        (tmp_path / "notes.txt").write_text("x", encoding="utf-8")
        assert list(discover_themes(tmp_path)) == ["a", "b"]
        assert discover_themes(tmp_path / "nope") == {}

    def test_themes_dir_override(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.setenv("MANIM_STUDIO_THEMES", str(tmp_path))
        assert themes_dir() == tmp_path


class TestQtStyling:
    def test_palette_follows_theme(self, qtbot) -> None:
        from PySide6.QtGui import QPalette

        from manim_studio.editor.styling import build_palette

        palette = build_palette(CHALKBOARD)
        assert palette.color(QPalette.ColorRole.Window).name() == CHALKBOARD.colors.bg
        assert palette.color(QPalette.ColorRole.Base).name() == CHALKBOARD.colors.editor
        assert palette.color(QPalette.ColorRole.Link).name() == CHALKBOARD.colors.accent

    def test_stylesheet_uses_theme_values(self, qtbot) -> None:
        from manim_studio.editor.styling import build_stylesheet

        sheet = build_stylesheet(CHALKBOARD)
        assert CHALKBOARD.colors.accent in sheet
        assert f"border-radius: {CHALKBOARD.metrics.radius}px" in sheet
        assert "{" in sheet and sheet.count("{") == sheet.count("}")

    def test_block_colors_come_from_lane_palette(self, qtbot) -> None:
        from manim_studio.editor.timeline_widget import block_color

        lanes = {c.lower() for c in CHALKBOARD.lanes}
        assert block_color("ShowCreation").name() in lanes
        assert block_color("Wait").name() in lanes


class TestWindowThemeSwitch:
    @pytest.fixture(autouse=True)
    def _restore_theme(self, qtbot):
        from manim_studio.theme import current_theme, set_current_theme

        before = current_theme()
        yield
        set_current_theme(before)

    def test_set_theme_restyles_the_window(self, qtbot) -> None:
        from PySide6.QtWidgets import QApplication

        from manim_studio.editor.main_window import MainWindow
        from manim_studio.theme import PAPER, current_theme

        window = MainWindow(restore_settings=False, auto_preview=False, completion=False)
        qtbot.addWidget(window)
        window.set_theme(PAPER, "builtin:paper")
        assert current_theme() is PAPER
        assert QApplication.palette().color(QApplication.palette().ColorRole.Window).name() == (
            PAPER.colors.bg
        )
        assert window._theme_key == "builtin:paper"

    def test_theme_menu_lists_builtins_and_user_files(
        self, qtbot, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        from manim_studio.editor.main_window import MainWindow

        save_theme(CHALKBOARD, tmp_path / "mine.json")
        monkeypatch.setenv("MANIM_STUDIO_THEMES", str(tmp_path))
        window = MainWindow(restore_settings=False, auto_preview=False, completion=False)
        qtbot.addWidget(window)
        window._populate_theme_menu()
        labels = [a.text() for a in window._theme_menu.actions() if a.text()]
        assert "Chalkboard" in labels and "Paper" in labels and "mine" in labels
        assert "Load theme file…" in labels


class TestGlyphColours:
    def test_glyph_uses_theme_icon_colours(self, qtbot) -> None:
        from PySide6.QtCore import QSize
        from PySide6.QtGui import QIcon

        from manim_studio.editor.glyphs import glyph

        icon = glyph("play")
        size = QSize(18, 18)

        def centre(mode: QIcon.Mode) -> str:
            return icon.pixmap(size, mode).toImage().pixelColor(9, 9).name()

        assert centre(QIcon.Mode.Normal) == CHALKBOARD.colors.icon
        assert centre(QIcon.Mode.Active) == CHALKBOARD.colors.icon_active
        assert centre(QIcon.Mode.Disabled) == CHALKBOARD.colors.icon_disabled

    def test_refresh_glyphs_recolours_buttons(self, qtbot) -> None:
        from PySide6.QtCore import QSize
        from PySide6.QtGui import QIcon

        from manim_studio.editor.glyphs import icon_button, refresh_glyphs
        from manim_studio.theme import PAPER, current_theme, set_current_theme

        before = current_theme()
        try:
            from PySide6.QtWidgets import QWidget

            host = QWidget()
            qtbot.addWidget(host)
            button = icon_button("play", "Play", host)
            set_current_theme(PAPER)
            refresh_glyphs(host)
            pixmap = button.icon().pixmap(QSize(18, 18), QIcon.Mode.Normal)
            assert pixmap.toImage().pixelColor(9, 9).name() == PAPER.colors.icon
        finally:
            set_current_theme(before)


class TestThemePersistence:
    @pytest.fixture(autouse=True)
    def _restore_theme(self, qtbot):
        from manim_studio.theme import current_theme, set_current_theme

        before = current_theme()
        yield
        set_current_theme(before)

    @staticmethod
    def _settings(tmp_path: Path):
        from PySide6.QtCore import QSettings

        return QSettings(str(tmp_path / "prefs.ini"), QSettings.Format.IniFormat)

    def test_choice_is_saved_and_restored(self, qtbot, tmp_path: Path) -> None:
        from manim_studio.editor.main_window import MainWindow
        from manim_studio.theme import PAPER, current_theme, set_current_theme

        settings = self._settings(tmp_path)
        first = MainWindow(settings=settings, auto_preview=False, completion=False)
        qtbot.addWidget(first)
        first.set_theme(PAPER, "builtin:paper")
        assert self._settings(tmp_path).value("theme") == "builtin:paper"

        set_current_theme(CHALKBOARD)  # a fresh process starts on the default
        second = MainWindow(settings=self._settings(tmp_path), auto_preview=False, completion=False)
        qtbot.addWidget(second)
        assert current_theme() == PAPER
        assert second._theme_key == "builtin:paper"

    def test_theme_file_choice_is_restored(self, qtbot, tmp_path: Path) -> None:
        from manim_studio.editor.main_window import MainWindow
        from manim_studio.theme import current_theme, set_current_theme

        path = tmp_path / "mine.json"
        save_theme(replace_name(CHALKBOARD, "Mine"), path)
        settings = self._settings(tmp_path)
        settings.setValue("theme", f"file:{path}")
        settings.sync()
        set_current_theme(CHALKBOARD)
        window = MainWindow(settings=self._settings(tmp_path), auto_preview=False, completion=False)
        qtbot.addWidget(window)
        assert current_theme().name == "Mine"

    def test_unreadable_saved_theme_falls_back(self, qtbot, tmp_path: Path) -> None:
        from manim_studio.editor.main_window import MainWindow
        from manim_studio.theme import current_theme, set_current_theme

        settings = self._settings(tmp_path)
        settings.setValue("theme", f"file:{tmp_path / 'gone.json'}")
        settings.sync()
        set_current_theme(CHALKBOARD)
        window = MainWindow(settings=self._settings(tmp_path), auto_preview=False, completion=False)
        qtbot.addWidget(window)
        assert current_theme() == CHALKBOARD

    def test_scripted_window_does_not_touch_saved_choice(self, qtbot, tmp_path: Path) -> None:
        from manim_studio.editor.main_window import MainWindow
        from manim_studio.theme import PAPER

        settings = self._settings(tmp_path)
        window = MainWindow(
            settings=settings, restore_settings=False, auto_preview=False, completion=False
        )
        qtbot.addWidget(window)
        window.set_theme(PAPER, "builtin:paper")
        assert self._settings(tmp_path).value("theme") is None


def replace_name(theme, name):
    from dataclasses import replace

    return replace(theme, name=name)


class TestRoundedMenus:
    def test_menu_gets_translucent_frameless_window(self, qtbot) -> None:
        from PySide6.QtCore import Qt
        from PySide6.QtWidgets import QApplication, QMenu

        from manim_studio.editor.styling import apply_theme

        app = QApplication.instance()
        assert isinstance(app, QApplication)
        apply_theme(app, CHALKBOARD)
        menu = QMenu()
        qtbot.addWidget(menu)
        menu.addAction("One")
        menu.ensurePolished()
        assert menu.testAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        assert menu.windowFlags() & Qt.WindowType.NoDropShadowWindowHint

    def test_stylesheet_rounds_menus(self, qtbot) -> None:
        from manim_studio.editor.styling import build_stylesheet

        radius = round(CHALKBOARD.metrics.radius * 1.6)
        assert f"border-radius: {radius}px; padding: 4px;" in build_stylesheet(CHALKBOARD)
