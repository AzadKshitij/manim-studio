"""LaTeX setup dialog + background install/probe workers.

Long work (download/extract/tlmgr, or a probe compile) runs in a ``QThread``;
the GUI thread only handles signals.  Both workers are cancellable and the
dialog stays responsive.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from PySide6.QtCore import QObject, QThread, Signal
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from manim_studio import latex_install
from manim_studio.latex import install_dir, latex_status, probe_compile

__all__ = ["LatexInstallWorker", "LatexProbeWorker", "LatexSetupDialog"]

log = logging.getLogger(__name__)


class LatexInstallWorker(QObject):
    """Runs :func:`install_latex` off the GUI thread with progress signals."""

    progress = Signal(object)
    finished = Signal(bool, str)

    def __init__(self, target: str | Path | None = None, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._target = Path(target) if target is not None else None
        self._cancelled = False

    def cancel(self) -> None:
        self._cancelled = True

    def _is_cancelled(self) -> bool:
        return self._cancelled

    def run(self) -> None:
        try:
            latex_install.install_latex(
                self._target,
                on_progress=lambda update: self.progress.emit(update),
                cancel=self._is_cancelled,
            )
        except latex_install.InstallError as exc:
            self.finished.emit(False, str(exc))
        except Exception as exc:  # keep the dialog alive on anything unexpected
            log.exception("LaTeX install failed")
            self.finished.emit(False, f"{type(exc).__name__}: {exc}")
        else:
            self.finished.emit(True, f"installed to {install_dir(self._target)}")


class LatexProbeWorker(QObject):
    """Runs :func:`probe_compile` off the GUI thread."""

    finished = Signal(object)

    def run(self) -> None:
        result: Any
        try:
            result = probe_compile()
        except Exception as exc:  # probe must never take the dialog down
            log.exception("LaTeX probe failed")
            result = exc
        self.finished.emit(result)


#: Retired-but-still-running (thread, worker) pairs.  Keeps Python references
#: alive so Qt never destroys a QThread while its worker is mid-call.
_ACTIVE_JOBS: set[tuple[QThread, QObject]] = set()


def _retire_job(thread: QThread | None, worker: QObject | None) -> None:
    if thread is None:
        return
    if thread.isRunning():
        job = (thread, worker) if worker is not None else None
        if job is not None:
            _ACTIVE_JOBS.add(job)

            def _cleanup() -> None:
                _ACTIVE_JOBS.discard(job)
                worker.deleteLater()  # type: ignore[union-attr]
                thread.deleteLater()

            thread.finished.connect(_cleanup)
        thread.quit()
    else:
        thread.quit()
        thread.wait(2000)
        if worker is not None:
            worker.deleteLater()
        thread.deleteLater()


class LatexSetupDialog(QDialog):
    """Status, Diagnose, Install and Remove for the LaTeX distribution."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("LaTeX setup")
        self.setMinimumWidth(480)
        self._thread: QThread | None = None
        self._worker: LatexInstallWorker | None = None
        self._probe_thread: QThread | None = None
        self._probe_worker: LatexProbeWorker | None = None

        layout = QVBoxLayout(self)
        self._status_label = QLabel(self)
        self._status_label.setWordWrap(True)
        layout.addWidget(self._status_label)

        self._detail_label = QLabel(self)
        self._detail_label.setWordWrap(True)
        layout.addWidget(self._detail_label)

        self._progress = QProgressBar(self)
        self._progress.setRange(0, 100)
        self._progress.setVisible(False)
        layout.addWidget(self._progress)

        buttons_row = QHBoxLayout()
        self._install_button = QPushButton("Install", self)
        self._install_button.clicked.connect(self.start_install)
        self._remove_button = QPushButton("Remove", self)
        self._remove_button.clicked.connect(self.remove_install)
        self._diagnose_button = QPushButton("Diagnose", self)
        self._diagnose_button.clicked.connect(self.start_diagnose)
        self._cancel_button = QPushButton("Cancel install", self)
        self._cancel_button.setEnabled(False)
        self._cancel_button.clicked.connect(self.cancel_install)
        buttons_row.addWidget(self._install_button)
        buttons_row.addWidget(self._remove_button)
        buttons_row.addWidget(self._diagnose_button)
        buttons_row.addWidget(self._cancel_button)
        layout.addLayout(buttons_row)

        close_box = QDialogButtonBox(QDialogButtonBox.StandardButton.Close, self)
        close_box.rejected.connect(self.reject)
        layout.addWidget(close_box)
        self.refresh()

    def refresh(self) -> None:
        status = latex_status()
        if status.ok:
            origin = "bundled install" if status.bundled else "system install"
            if status.miktex:
                origin += ", MiKTeX"
            self._status_label.setText(f"LaTeX is ready ({origin}).")
        else:
            self._status_label.setText(
                "LaTeX is missing: "
                f"{', '.join(status.missing)}. "
                "Tex/Brace scenes need it (~100-200 MB download)."
            )
        details = [f"Location: {status.install_dir}"]
        if status.latex_path:
            details.append(f"latex: {status.latex_path}")
        if status.dvisvgm_path:
            details.append(f"dvisvgm: {status.dvisvgm_path}")
        self._detail_label.setText("\n".join(details))
        self._remove_button.setEnabled(latex_install.is_installed())

    # ------------------------------------------------------------------
    # Install / remove
    # ------------------------------------------------------------------

    def start_install(self) -> None:
        if self._thread is not None:
            return
        reply = QMessageBox.question(
            self,
            "Install LaTeX",
            "Download portable TinyTeX (~100-200 MB) to your user folder?\n"
            "No admin rights needed. Requires internet.",
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        self._progress.setVisible(True)
        self._progress.setValue(0)
        self._install_button.setEnabled(False)
        self._cancel_button.setEnabled(True)
        self._worker = LatexInstallWorker()
        self._thread = QThread()
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.progress.connect(self._on_progress)
        self._worker.finished.connect(self._on_finished)
        self._thread.start()

    def _on_progress(self, update: Any) -> None:
        stage = str(getattr(update, "stage", "working"))
        fraction = getattr(update, "fraction", None)
        if fraction is not None:
            self._progress.setValue(int(float(fraction) * 100))
        self._status_label.setText(f"Installing LaTeX… ({stage})")

    def _on_finished(self, ok: bool, message: str) -> None:
        self._teardown_worker()
        self._progress.setVisible(False)
        self._install_button.setEnabled(True)
        self._cancel_button.setEnabled(False)
        self._status_label.setText(message)
        self.refresh()
        if not ok:
            QMessageBox.warning(self, "LaTeX setup", message)
        else:
            # Verify the fresh install end-to-end right away.
            self.start_diagnose()

    def cancel_install(self) -> None:
        if self._worker is not None:
            self._worker.cancel()
            self._status_label.setText("Cancelling…")

    def remove_install(self) -> None:
        if not latex_install.is_installed():
            return
        reply = QMessageBox.question(
            self, "Remove LaTeX", "Delete the downloaded LaTeX distribution?"
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        try:
            latex_install.uninstall_latex()
        except OSError as exc:
            QMessageBox.warning(self, "LaTeX setup", f"remove failed: {exc}")
            return
        self.refresh()

    # ------------------------------------------------------------------
    # Diagnose
    # ------------------------------------------------------------------

    def start_diagnose(self) -> None:
        if self._probe_thread is not None:
            return
        self._diagnose_button.setEnabled(False)
        self._status_label.setText(
            "Diagnosing… (compiling manimgl's template; the first run can take minutes)"
        )
        self._probe_worker = LatexProbeWorker()
        self._probe_thread = QThread()
        self._probe_worker.moveToThread(self._probe_thread)
        self._probe_thread.started.connect(self._probe_worker.run)
        self._probe_worker.finished.connect(self._on_probe_finished)
        self._probe_thread.start()

    def _on_probe_finished(self, result: Any) -> None:
        self._teardown_probe()
        self._diagnose_button.setEnabled(True)
        if result is None or not hasattr(result, "ok"):
            self._status_label.setText("Diagnose failed unexpectedly; see the log.")
            return
        self._status_label.setText(
            "LaTeX works end-to-end." if result.ok else f"LaTeX problem: {result.summary}"
        )
        if not result.ok:
            box = QMessageBox(self)
            box.setIcon(QMessageBox.Icon.Warning)
            box.setWindowTitle("LaTeX diagnose")
            box.setText(result.summary)
            box.setDetailedText(result.detail or "No details available.")
            box.exec()

    # ------------------------------------------------------------------
    # Teardown
    # ------------------------------------------------------------------

    def _teardown_worker(self) -> None:
        thread, worker = self._thread, self._worker
        self._thread, self._worker = None, None
        _retire_job(thread, worker)

    def _teardown_probe(self) -> None:
        thread, worker = self._probe_thread, self._probe_worker
        self._probe_thread, self._probe_worker = None, None
        _retire_job(thread, worker)

    def closeEvent(self, event: Any) -> None:
        self.cancel_install()
        self._teardown_worker()
        self._teardown_probe()
        super().closeEvent(event)
