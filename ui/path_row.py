"""
ui/path_row.py
==============
Reusable row: label + input + [Browse…]
"""

from PyQt5.QtCore import pyqtSignal
from PyQt5.QtWidgets import (
    QComboBox, QFileDialog, QFrame, QHBoxLayout,
    QLabel, QLineEdit, QPushButton, QSizePolicy,
)


class PathRow(QFrame):
    sig_path_changed = pyqtSignal(str)

    def __init__(self, label: str, kind: str = "folder", parent=None):
        super().__init__(parent)
        self._kind = kind
        # Row stays at its natural height – do not absorb excess
        # vertical space (causes huge gaps).
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)

        lbl = QLabel(label)
        lbl.setMinimumWidth(200)
        lay.addWidget(lbl)

        if kind == "combo":
            self._input: QComboBox = QComboBox()
            self._input.setEditable(True)
        else:
            self._input: QLineEdit = QLineEdit()

        self._input.setPlaceholderText("Choose a path …")

        # Signal-Verdrahtung (unterschiedlich je Typ!)
        if self._kind == "combo":
            self._input.currentTextChanged.connect(self.sig_path_changed.emit)
            self._input.editTextChanged.connect(self.sig_path_changed.emit)
        else:
            self._input.textChanged.connect(self.sig_path_changed.emit)

        lay.addWidget(self._input, 1)

        btn = QPushButton("Browse …")
        btn.setFixedWidth(120)
        btn.clicked.connect(self._browse)
        lay.addWidget(btn)

    # ── Accessor ──────────────────────────────────────────────────────

    @property
    def value(self) -> str:
        if self._kind == "combo":
            return self._input.currentText()
        return self._input.text()

    def set_value(self, text: str) -> None:
        if self._kind == "combo":
            self._input.setEditText(text)
        else:
            self._input.setText(text)

    def set_combo_items(self, items: list[str], default: str | None = None) -> None:
        if self._kind != "combo":
            return
        self._input.blockSignals(True)
        self._input.clear()
        self._input.addItems(items)
        if default:
            idx = self._input.findText(default)
            if idx >= 0:
                self._input.setCurrentIndex(idx)
        self._input.blockSignals(False)

    # ── Intern ────────────────────────────────────────────────────────

    def _browse(self) -> None:
        if self._kind == "folder":
            path = QFileDialog.getExistingDirectory(self, "Choose folder")
            if path:
                self._input.setText(path)
        elif self._kind == "file":
            path, _ = QFileDialog.getOpenFileName(self, "Choose file")
            if path:
                self._input.setText(path)
