"""Application bootstrap: QApplication, dark Fusion theme, main window."""

from __future__ import annotations

import logging
import sys

from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication

from manim_studio import APP_NAME, ORG_NAME
from manim_studio.editor.icon import load_icon

__all__ = ["create_application", "dark_palette", "run_editor"]

log = logging.getLogger(__name__)

# Stable Windows shell identity; without it a taskbar launch through python.exe
# is grouped under the Python launcher and shows its icon.
WINDOWS_APP_ID = f"{ORG_NAME}.ManimStudio"


def _set_windows_app_id(app_id: str) -> None:
    """Tell Windows which AppUserModelID to group our windows under.

    Best effort: the taskbar still works without it, it may just show the
    launcher's icon instead of ours. Non-Windows platforms do nothing.
    """
    if sys.platform != "win32":
        return
    try:
        import ctypes

        windll = getattr(ctypes, "windll", None)
        if windll is not None:
            windll.shell32.SetCurrentProcessExplicitAppUserModelID(app_id)
    except OSError:  # pragma: no cover - shell integration is best effort
        log.warning("could not set the Windows AppUserModelID", exc_info=True)


def dark_palette() -> QPalette:
    """A neutral dark palette for the whole application."""
    palette = QPalette()
    window = QColor(0x1E, 0x1E, 0x1E)
    base = QColor(0x25, 0x25, 0x26)
    text = QColor(0xD4, 0xD4, 0xD4)
    palette.setColor(QPalette.ColorRole.Window, window)
    palette.setColor(QPalette.ColorRole.WindowText, text)
    palette.setColor(QPalette.ColorRole.Base, base)
    palette.setColor(QPalette.ColorRole.AlternateBase, window)
    palette.setColor(QPalette.ColorRole.ToolTipBase, base)
    palette.setColor(QPalette.ColorRole.ToolTipText, text)
    palette.setColor(QPalette.ColorRole.Text, text)
    palette.setColor(QPalette.ColorRole.Button, QColor(0x2D, 0x2D, 0x30))
    palette.setColor(QPalette.ColorRole.ButtonText, text)
    palette.setColor(QPalette.ColorRole.BrightText, QColor(0xFF, 0x80, 0x80))
    palette.setColor(QPalette.ColorRole.Link, QColor(0x7F, 0xC6, 0xFF))
    palette.setColor(QPalette.ColorRole.Highlight, QColor(0x09, 0x47, 0x71))
    palette.setColor(QPalette.ColorRole.HighlightedText, QColor(0xFF, 0xFF, 0xFF))
    disabled = QColor(0x7F, 0x7F, 0x7F)
    for role in (
        QPalette.ColorRole.WindowText,
        QPalette.ColorRole.Text,
        QPalette.ColorRole.ButtonText,
    ):
        palette.setColor(QPalette.ColorGroup.Disabled, role, disabled)
    return palette


def create_application(argv: list[str] | None = None) -> QApplication:
    """Return the process-wide QApplication, configured once."""
    existing = QApplication.instance()
    if isinstance(existing, QApplication):
        return existing
    app = QApplication(argv if argv is not None else sys.argv[:1])
    app.setApplicationName(APP_NAME)
    app.setOrganizationName(ORG_NAME)
    _set_windows_app_id(WINDOWS_APP_ID)
    app.setStyle("Fusion")
    app.setPalette(dark_palette())
    app.setWindowIcon(load_icon())
    return app


def run_editor(
    path: str | None = None,
    *,
    embedded: bool = True,
    preview_size: tuple[int, int] = (1280, 720),
) -> int:
    """Create the editor window and run the Qt event loop."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,
    )
    logger = logging.getLogger("manim_studio.editor")
    from manim_studio.latex import apply_runtime_path

    apply_runtime_path()
    app = create_application()
    from manim_studio.editor.main_window import MainWindow

    window = MainWindow(path=path, embedded=embedded, preview_size=preview_size)
    window.resize(1500, 950)
    window.show()
    logger.info("editor started (embedded=%s, file=%s)", embedded, path)
    exit_code = app.exec()
    logger.info("editor exited with code %s", exit_code)
    return exit_code
