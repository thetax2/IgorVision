"""
ui/preferences_dialog.py
========================
Program-wide preferences (Settings → Preferences).

Holds settings that apply to the whole application (as opposed to the
per-tab "Quality Settings" panel, which only tunes the analysis):

* **Performance** – CPU cores used by the quality analysis
  (``prefs/cores``; 0 = auto → all cores).
* **ExifTool** – path to the ExifTool executable used by the Compare
  tab for EXIF metadata (``prefs/exiftool``).
"""
from __future__ import annotations

import multiprocessing as mp

from PyQt5.QtCore import QSettings, QTimer
from PyQt5.QtWidgets import (
    QComboBox,
    QDialog,
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
)

from tools.comparison import detect_exiftool
from tools.compare_worker import ExifToolCheckWorker

_HINT_STYLE = "color: #8a8a8a; font-size: 11px;"


class PreferencesDialog(QDialog):
    """Modal dialog for program-wide settings.

    Values are loaded from QSettings on construction and written back
    when the dialog is accepted (OK).  Cancel / close discards changes.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Preferences")
        self.setMinimumWidth(600)
        self._check_worker = None
        self._build()
        self._load()

    # ── UI ───────────────────────────────────────────────────────────
    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(14, 12, 14, 12)
        root.setSpacing(12)

        # ── Performance ──
        perf = QGroupBox("Performance")
        p_lay = QVBoxLayout(perf)
        p_lay.setSpacing(6)
        row = QHBoxLayout()
        row.addWidget(QLabel("CPU cores:"))
        self._cmb_cores = QComboBox()
        n = mp.cpu_count()
        self._cmb_cores.addItem(f"Auto ({n} cores)", 0)
        for i in range(1, n + 1):
            self._cmb_cores.addItem(str(i), i)
        row.addWidget(self._cmb_cores, 1)
        p_lay.addLayout(row)
        hint = QLabel(
            "Worker threads for the quality analysis. "
            "“Auto” uses all available cores."
        )
        hint.setWordWrap(True)
        hint.setStyleSheet(_HINT_STYLE)
        p_lay.addWidget(hint)
        root.addWidget(perf)

        # ── ExifTool ──
        exif = QGroupBox("ExifTool")
        e_lay = QVBoxLayout(exif)
        e_lay.setSpacing(6)
        row2 = QHBoxLayout()
        self._ed_exif = QLineEdit()
        self._ed_exif.setPlaceholderText("Path to exiftool.exe (optional)")
        btn_browse = QPushButton("Browse …")
        btn_browse.clicked.connect(self._pick)
        btn_auto = QPushButton("Auto-detect")
        btn_auto.setToolTip("Look for ExifTool in PATH and common install locations")
        btn_auto.clicked.connect(self._auto_detect)
        row2.addWidget(self._ed_exif, 1)
        row2.addWidget(btn_browse)
        row2.addWidget(btn_auto)
        e_lay.addLayout(row2)
        self._lbl_status = QLabel("")
        e_lay.addWidget(self._lbl_status)
        hint2 = QLabel(
            "Used by the Compare tab for EXIF metadata (capture date, focal "
            "length, camera model, lens).  If not set, the file date is used "
            "as fallback.  On Windows, rename ``exiftool(-k).exe`` to "
            "``exiftool.exe`` and point here – or just put it in your PATH."
        )
        hint2.setWordWrap(True)
        hint2.setStyleSheet(_HINT_STYLE)
        e_lay.addWidget(hint2)
        root.addWidget(exif)

        # ── buttons ──
        btns = QHBoxLayout()
        btns.addStretch(1)
        btn_ok = QPushButton("OK")
        btn_ok.clicked.connect(self.accept)
        btn_cancel = QPushButton("Cancel")
        btn_cancel.clicked.connect(self.reject)
        btns.addWidget(btn_ok)
        btns.addWidget(btn_cancel)
        root.addLayout(btns)

        self._ed_exif.textChanged.connect(self._schedule_check)

    # ── ExifTool helpers ─────────────────────────────────────────────
    def _pick(self) -> None:
        p, _ = QFileDialog.getOpenFileName(
            self, "Select ExifTool", "C:/Tools",
            "ExifTool (*.exe);;All (*)",
        )
        if p:
            self._ed_exif.setText(p)

    def _auto_detect(self) -> None:
        found = detect_exiftool()
        self._ed_exif.setText(found if found else self._ed_exif.text())

    def _schedule_check(self) -> None:
        """Debounce the ExifTool availability check (300 ms)."""
        self._check_timer = getattr(self, "_check_timer", None)
        if self._check_timer is None:
            self._check_timer = QTimer(self)
            self._check_timer.setSingleShot(True)
            self._check_timer.setInterval(300)
            self._check_timer.timeout.connect(self._run_check)
        self._check_timer.start()

    def _run_check(self) -> None:
        path = self._ed_exif.text().strip()
        if not path:
            self._lbl_status.setText("")
            self._lbl_status.setStyleSheet("")
            return
        self._lbl_status.setText("… checking")
        self._lbl_status.setStyleSheet("color: #8a8a8a;")
        self._check_worker = ExifToolCheckWorker(path, parent=self)
        self._check_worker.sig_done.connect(self._on_check_done)
        self._check_worker.start()

    def _on_check_done(self, ok: bool, version: str) -> None:
        if ok:
            text = f"✓ ExifTool {version}" if version else "✓ found"
            self._lbl_status.setText(text)
            self._lbl_status.setStyleSheet("color: #6eb26a; font-weight: bold;")
        else:
            self._lbl_status.setText("✗ not found or not executable")
            self._lbl_status.setStyleSheet("color: #ef5350; font-weight: bold;")

    # ── persistence ──────────────────────────────────────────────────
    def _load(self) -> None:
        s = QSettings()
        try:
            cores = int(s.value("prefs/cores", 0) or 0)
        except (TypeError, ValueError):
            cores = 0
        idx = self._cmb_cores.findData(cores)
        self._cmb_cores.setCurrentIndex(idx if idx >= 0 else 0)

        exif = str(s.value("prefs/exiftool", "") or "").strip()
        if not exif:
            exif = detect_exiftool()
        self._ed_exif.setText(exif)

    def _save(self) -> None:
        s = QSettings()
        s.setValue("prefs/cores", int(self._cmb_cores.currentData() or 0))
        s.setValue("prefs/exiftool", self._ed_exif.text().strip())
        s.sync()

    def accept(self) -> None:  # type: ignore[override]
        self._save()
        super().accept()
