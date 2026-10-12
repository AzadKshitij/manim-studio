"""The prompt shown before unsaved edits are thrown away."""

from __future__ import annotations

from PySide6.QtWidgets import QMessageBox, QWidget

__all__ = ["CANCEL", "DISCARD", "SAVE", "ask_discard"]

SAVE = "save"
DISCARD = "discard"
CANCEL = "cancel"


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
