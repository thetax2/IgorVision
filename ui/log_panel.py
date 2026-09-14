"""
ui/log_panel.py
===============
Scrollbares Log-Textfeld (QPlainTextEdit).
"""

from PyQt5.QtWidgets import QPlainTextEdit, QFrame, QVBoxLayout


class LogPanel(QFrame):
    def __init__(self, parent=None):
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)

        self._view = QPlainTextEdit()
        self._view.setObjectName("logView")
        self._view.setReadOnly(True)
        self._view.setMaximumBlockCount(5000)  # Speicher begrenzen
        lay.addWidget(self._view)

    def append(self, msg: str) -> None:
        self._view.appendPlainText(msg)

    def clear(self) -> None:
        self._view.clear()

    def set_readonly(self, ro: bool) -> None:
        self._view.setReadOnly(ro)
