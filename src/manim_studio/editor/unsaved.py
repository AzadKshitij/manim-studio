"""The prompts that stand between the user and lost work.

``ask_discard`` runs before unsaved edits are replaced or closed; ``ask_recovery``
offers edits a crash left behind.  Both are plain modal boxes so callers can test them
through a monkeypatched module attribute.
"""

from __future__ import annotations

from PySide6.QtWidgets import QMessageBox, QWidget

__all__ = [
    "CANCEL",
    "DISCARD",
    "DISCARD_RECOVERY",
    "RECOVER",
    "SAVE",
    "ask_discard",
    "ask_recovery",
]

SAVE = "save"
DISCARD = "discard"
CANCEL = "cancel"
RECOVER = "recover"
DISCARD_RECOVERY = "discard_recovery"


def ask_discard(parent: QWidget, name: str, action: str) -> str:
    """Ask what to do with unsaved edits before ``action``; returns SAVE, DISCARD or CANCEL."""
    box = QMessageBox(parent)
    box.setIcon(QMessageBox.Icon.Question)
    box.setWindowTitle("Unsaved changes")
    box.setText(f"Save changes to {name}?")
    box.setInformativeText(f"Your changes will be lost if you {action} without saving.")
    save = box.addButton("Save", QMessageBox.ButtonRole.AcceptRole)
    discard = box.addButton("Discard", QMessageBox.ButtonRole.DestructiveRole)
    cancel = box.addButton("Cancel", QMessageBox.ButtonRole.RejectRole)
    box.setDefaultButton(save)
    box.setEscapeButton(cancel)
    box.exec()
    clicked = box.clickedButton()
    if clicked is save:
        return SAVE
    if clicked is discard:
        return DISCARD
    return CANCEL


def ask_recovery(parent: QWidget, name: str) -> str:
    """Offer edits a crash left unsaved; returns RECOVER or DISCARD_RECOVERY.

    Dismissing the prompt recovers: that is the choice that loses nothing.
    """
    box = QMessageBox(parent)
    box.setIcon(QMessageBox.Icon.Question)
    box.setWindowTitle("Recover unsaved changes")
    box.setText(f"Recover unsaved changes to {name}?")
    box.setInformativeText(
        "Manim Studio kept a copy of edits that were not saved when the last session ended."
    )
    recover = box.addButton("Recover", QMessageBox.ButtonRole.AcceptRole)
    discard = box.addButton("Discard", QMessageBox.ButtonRole.DestructiveRole)
    box.setDefaultButton(recover)
    box.setEscapeButton(recover)
    box.exec()
    return DISCARD_RECOVERY if box.clickedButton() is discard else RECOVER
