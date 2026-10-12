"""Application bootstrap: QApplication, themed Fusion style, main window."""

from __future__ import annotations

import logging
import sys

from PySide6.QtWidgets import QApplication

from manim_studio import APP_NAME, ORG_NAME
from manim_studio.editor.icon import load_icon
from manim_studio.editor.styling import apply_theme
from manim_studio.theme import CHALKBOARD

__all__ = ["create_application", "run_editor"]

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


def create_application(argv: list[str] | None = None) -> QApplication:
    """Return the process-wide QApplication, configured once."""
    existing = QApplication.instance()
    if isinstance(existing, QApplication):
        return existing
    app = QApplication(argv if argv is not None else sys.argv[:1])
    app.setApplicationName(APP_NAME)
    app.setOrganizationName(ORG_NAME)
    _set_windows_app_id(WINDOWS_APP_ID)
    apply_theme(app, CHALKBOARD)
    app.setWindowIcon(load_icon())
    return app


def run_editor(
    path: str | None = None,
    *,
    embedded: bool | None = None,
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
    from manim_studio.editor.splash import create_splash

    splash = create_splash()
    if splash is not None:
        splash.show()
        app.processEvents()
    from manim_studio.editor.main_window import MainWindow

    window = MainWindow(path=path, embedded=embedded, preview_size=preview_size)
    window.resize(1500, 950)
    window.show()
    if splash is not None:
        splash.finish(window)
    logger.info("editor started (embedded=%s, file=%s)", embedded, path)
    exit_code = app.exec()
    logger.info("editor exited with code %s", exit_code)
    return exit_code
