"""Qt editor shell.

Importing this package requires PySide6 but must never import ``manimlib`` or
``moderngl`` — the editor talks to the preview host over IPC only (see AGENTS.md).
"""
