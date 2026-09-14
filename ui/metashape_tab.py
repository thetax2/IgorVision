"""
ui/metashape_tab.py
===================
Metashape tab:
  Phase 1 (optional): quality filter via .psx + threshold slider
  Phase 2 (standalone): orphaned RAWs → trash
"""

from __future__ import annotations

from pathlib import Path

from PyQt5.QtCore import Qt
from PyQt5.QtGui import QColor
from PyQt5.QtWidgets import (
    QFileDialog, QFrame, QGroupBox, QHBoxLayout, QLabel,
    QLineEdit, QMessageBox, QProgressBar, QPushButton, QRadioButton,
    QSlider, QVBoxLayout, QWidget,
)

from tools.metashape_engine import MetashapeConfig, QualityEntry
from ui.log_panel import LogPanel
from ui.path_row import PathRow
from tools.metashape_worker import MetashapeWorker


class MetashapeTab(QWidget):
    def __init__(self, title: str = "Metashape", parent=None):
        super().__init__(parent)
        self._worker: MetashapeWorker | None = None
        self._entries: list[QualityEntry] = []
        self._orphans: list[Path] = []
        self._build()

    # ── UI ─────────────────────────────────────────────────────────────

    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 10, 12, 10)
        root.setSpacing(8)

        # ═══════════════════════════════════════════════════════════
        # PHASE 1 – Quality-Filter (optional)
        # ═══════════════════════════════════════════════════════════
        p1_box = QGroupBox("Phase 1 – Quality filter (optional)")
        p1 = QVBoxLayout(p1_box)
        p1.setSpacing(6)

        # .psx file row
        psx_layout = QHBoxLayout()
        psx_layout.addWidget(QLabel(".psx:"))
        self._ed_psx = QLineEdit()
        self._ed_psx.setPlaceholderText("Path to the .psx file …")
        btn_psx = QPushButton("…")
        btn_psx.setFixedWidth(32)
        btn_psx.clicked.connect(self._pick_psx)
        psx_layout.addWidget(self._ed_psx)
        psx_layout.addWidget(btn_psx)
        p1.addLayout(psx_layout)

        # JPG (phase 1)
        self._pr_jpg1 = PathRow("JPG folder:", kind="folder")
        p1.addWidget(self._pr_jpg1)

        # Load button + status
        load_row = QHBoxLayout()
        self.btn_load = QPushButton("Load scores")
        self.btn_load.setMinimumHeight(32)
        self.btn_load.clicked.connect(self._load_scores)
        load_row.addWidget(self.btn_load)
        self._lbl_p1_status = QLabel("No data yet.")
        self._lbl_p1_status.setStyleSheet("color: gray;")
        load_row.addWidget(self._lbl_p1_status)
        load_row.addStretch()
        p1.addLayout(load_row)

        # Slider
        slider_row = QHBoxLayout()
        slider_row.addWidget(QLabel("Threshold:"))
        self._slider = QSlider(Qt.Horizontal)
        self._slider.setRange(10, 200)   # 0.10 – 2.00
        self._slider.setValue(50)
        self._slider.setEnabled(False)
        self._slider.valueChanged.connect(self._on_slider)
        slider_row.addWidget(self._slider, 1)
        self._lbl_threshold = QLabel("0.50")
        self._lbl_threshold.setStyleSheet("font-weight: bold; font-family: Consolas, monospace;")
        self._lbl_threshold.setFixedWidth(40)
        slider_row.addWidget(self._lbl_threshold)
        p1.addLayout(slider_row)

        # Preview
        self._lbl_preview = QLabel("")
        self._lbl_preview.setStyleSheet("color: #ef5350; font-weight: 600;")
        p1.addWidget(self._lbl_preview)

        # Filter mode + button
        mode_row = QHBoxLayout()
        self._rb_trash = QRadioButton("Trash")
        self._rb_trash.setChecked(True)
        self._rb_move = QRadioButton("_removed/")
        self._rb_del = QRadioButton("Delete")
        mode_row.addWidget(self._rb_trash)
        mode_row.addWidget(self._rb_move)
        mode_row.addWidget(self._rb_del)
        mode_row.addStretch()

        self.btn_filter = QPushButton("⚡ Apply filter")
        self.btn_filter.setObjectName("btnPrimary")
        self.btn_filter.setMinimumHeight(36)
        self.btn_filter.setEnabled(False)
        self.btn_filter.clicked.connect(self._apply_filter)
        mode_row.addWidget(self.btn_filter)
        p1.addLayout(mode_row)

        root.addWidget(p1_box)

        # ═══════════════════════════════════════════════════════════
        # PHASE 2 – RAW cleanup (standalone)
        # ═══════════════════════════════════════════════════════════
        p2_box = QGroupBox("Phase 2 – RAW cleanup (standalone)")
        p2 = QVBoxLayout(p2_box)
        p2.setSpacing(6)

        self._pr_jpg2 = PathRow("JPG folder (survivors):", kind="folder")
        p2.addWidget(self._pr_jpg2)

        self._pr_raw = PathRow("RAW folder:", kind="folder")
        p2.addWidget(self._pr_raw)

        # Reconcile + preview
        ab_row = QHBoxLayout()
        self.btn_check = QPushButton("Check")
        self.btn_check.setMinimumHeight(32)
        self.btn_check.clicked.connect(self._check_orphans)
        ab_row.addWidget(self.btn_check)
        self._lbl_p2_status = QLabel("")
        self._lbl_p2_status.setStyleSheet("color: gray;")
        ab_row.addWidget(self._lbl_p2_status)
        ab_row.addStretch()
        p2.addLayout(ab_row)

        self._lbl_orphans = QLabel("")
        self._lbl_orphans.setStyleSheet("color: #ef5350; font-family: Consolas, monospace; font-size: 11px;")
        self._lbl_orphans.setWordWrap(True)
        p2.addWidget(self._lbl_orphans)

        # Button
        btn_p2_row = QHBoxLayout()
        self.btn_cleanup = QPushButton("🗑  Orphaned RAWs → Trash")
        self.btn_cleanup.setObjectName("btnPrimary")
        self.btn_cleanup.setMinimumHeight(36)
        self.btn_cleanup.clicked.connect(self._apply_cleanup)
        btn_p2_row.addWidget(self.btn_cleanup)
        btn_p2_row.addStretch()
        p2.addLayout(btn_p2_row)

        root.addWidget(p2_box)

        # ═══════════════════════════════════════════════════════════
        # PROGRESS + LOG
        # ═══════════════════════════════════════════════════════════
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress.setTextVisible(True)
        self.progress.setFormat("")
        root.addWidget(self.progress)

        self.log = LogPanel()
        self.log.setMinimumHeight(120)
        root.addWidget(self.log, 1)

    # ── Helper ─────────────────────────────────────────────────────────

    def _pick_psx(self) -> None:
        p, _ = QFileDialog.getOpenFileName(
            self, "Metashape project", "",
            "Metashape (*.psx);;All (*)",
        )
        if p:
            self._ed_psx.setText(p)

    def _on_slider(self, val: int) -> None:
        t = val / 100.0
        self._lbl_threshold.setText(f"{t:.2f}")
        if self._entries:
            n_bad = sum(1 for e in self._entries if e.quality < t)
            self._lbl_preview.setText(
                f"{n_bad} JPGs < {t:.2f}  |  {len(self._entries) - n_bad} remaining"
            )
            self.btn_filter.setEnabled(n_bad > 0)

    def _get_filter_mode(self) -> str:
        if self._rb_move.isChecked():
            return "move"
        if self._rb_del.isChecked():
            return "delete"
        return "trash"

    # ── Phase 1: Load ──────────────────────────────────────────────────

    def _load_scores(self) -> None:
        psx = self._ed_psx.text().strip()
        if not psx:
            QMessageBox.warning(self, "Error", "Please specify a .psx file.")
            return

        self.btn_load.setEnabled(False)
        self._lbl_p1_status.setText("Loading …")
        self._lbl_p1_status.setStyleSheet("color: #64b5f6;")
        self.log.clear()

        cfg = MetashapeConfig(mode="load", psx_path=psx)
        self._worker = MetashapeWorker(cfg, parent=self)
        self._worker.sig_log.connect(self.log.append)
        self._worker.sig_scores_loaded.connect(self._on_scores_loaded)
        self._worker.sig_done.connect(lambda n: self._on_load_done(n))
        self._worker.sig_error.connect(self._on_error)
        self._worker.start()

    def _on_scores_loaded(self, entries: list[QualityEntry]) -> None:
        self._entries = entries
        self._slider.setEnabled(True)
        self._on_slider(self._slider.value())

    def _on_load_done(self, n: int) -> None:
        self.btn_load.setEnabled(True)
        self._lbl_p1_status.setText(f"✓ {n} scores loaded")
        self._lbl_p1_status.setStyleSheet("color: #6eb26a;")

    # ── Phase 1: Filter ────────────────────────────────────────────────

    def _apply_filter(self) -> None:
        psx = self._ed_psx.text().strip()
        jpg = self._pr_jpg1.value
        if not psx:
            QMessageBox.warning(self, "Error", "Please specify a .psx file.")
            return
        if not jpg:
            QMessageBox.warning(self, "Error", "Please specify a JPG folder.")
            return

        t = self._slider.value() / 100.0
        n_bad = sum(1 for e in self._entries if e.quality < t)
        if n_bad == 0:
            return

        mode = self._get_filter_mode()
        mode_label = {"trash": "trash", "move": "_removed/", "delete": "delete"}[mode]

        ans = QMessageBox.question(
            self, "Confirm",
            f"{n_bad} JPGs < {t:.2f} → {mode_label}?\n\n"
            "This action is not reversible (when 'delete' is chosen).",
        )
        if ans != QMessageBox.Yes:
            return

        self._set_running(True)
        self.progress.setValue(0)

        cfg = MetashapeConfig(
            mode="filter", psx_path=psx, jpg_dir=jpg,
            threshold=t, filter_mode=mode,
        )
        self._worker = MetashapeWorker(cfg, parent=self)
        self._worker.sig_log.connect(self.log.append)
        self._worker.sig_progress.connect(self._set_progress)
        self._worker.sig_done.connect(self._on_filter_done)
        self._worker.sig_error.connect(self._on_error)
        self._worker.sig_cancelled.connect(self._on_cancelled)
        self._worker.start()

    def _on_filter_done(self, n: int) -> None:
        self._set_running(False)
        self._lbl_p1_status.setText(f"✓ {n} JPGs removed")
        self._lbl_p1_status.setStyleSheet("color: #6eb26a;")
        # Pre-fill the JPG path in phase 2
        if not self._pr_jpg2.value:
            self._pr_jpg2.set_value(self._pr_jpg1.value)

    # ── Phase 2: Check ─────────────────────────────────────────────────

    def _check_orphans(self) -> None:
        jpg = self._pr_jpg2.value
        raw = self._pr_raw.value
        if not jpg:
            QMessageBox.warning(self, "Error", "Please specify a JPG folder (phase 2).")
            return
        if not raw:
            QMessageBox.warning(self, "Error", "Please specify a RAW folder.")
            return

        from tools.metashape_engine import find_orphaned_raws
        self._orphans = find_orphaned_raws(jpg, raw)

        jpg_count = len([f for f in Path(jpg).iterdir() if f.is_file()])
        raw_count = len([f for f in Path(raw).iterdir() if f.is_file()])

        if not self._orphans:
            self._lbl_p2_status.setText(
                f"✓ JPGs: {jpg_count} = RAWs: {raw_count} – all matched."
            )
            self._lbl_p2_status.setStyleSheet("color: #6eb26a;")
            self._lbl_orphans.setText("")
        else:
            self._lbl_p2_status.setText(
                f"JPGs: {jpg_count}  |  RAWs: {raw_count}  |  Orphans: {len(self._orphans)}"
            )
            self._lbl_p2_status.setStyleSheet("color: #ef5350; font-weight: bold;")
            names = "\n".join(f"  {r.name}" for r in self._orphans[:10])
            more = f"  …+{len(self._orphans) - 10}" if len(self._orphans) > 10 else ""
            self._lbl_orphans.setText(f"→ {len(self._orphans)} Orphaned RAWs:\n{names}{more}")

    # ── Phase 2: Cleanup ───────────────────────────────────────────────

    def _apply_cleanup(self) -> None:
        jpg = self._pr_jpg2.value
        raw = self._pr_raw.value
        if not jpg or not raw:
            QMessageBox.warning(self, "Error", "Please specify both folders.")
            return

        if not self._orphans:
            self._check_orphans()
            if not self._orphans:
                QMessageBox.information(self, "Phase 2", "No orphaned RAWs found.")
                return

        ans = QMessageBox.question(
            self, "Phase 2 – Confirm",
            f"{len(self._orphans)} RAWs without a matching JPG → trash?\n\n"
            + "\n".join(f"  {r.name}" for r in self._orphans[:8])
            + (f"\n  …+{len(self._orphans) - 8}" if len(self._orphans) > 8 else ""),
        )
        if ans != QMessageBox.Yes:
            return

        self._set_running(True)
        self.progress.setValue(0)
        self.log.clear()

        cfg = MetashapeConfig(mode="cleanup", jpg_dir=jpg, raw_dir=raw)
        self._worker = MetashapeWorker(cfg, parent=self)
        self._worker.sig_log.connect(self.log.append)
        self._worker.sig_progress.connect(self._set_progress)
        self._worker.sig_done.connect(self._on_cleanup_done)
        self._worker.sig_error.connect(self._on_error)
        self._worker.sig_cancelled.connect(self._on_cancelled)
        self._worker.start()

    def _on_cleanup_done(self, n: int) -> None:
        self._set_running(False)
        self._lbl_p2_status.setText(f"✓ {n} RAWs removed.")
        self._lbl_p2_status.setStyleSheet("color: #6eb26a; font-weight: bold;")
        self._lbl_orphans.setText("")
        self._orphans = []

    # ── Shared slots ─────────────────────────────────────────────────

    def _set_running(self, running: bool) -> None:
        self.btn_load.setEnabled(not running)
        self.btn_filter.setEnabled(not running and bool(self._entries))
        self.btn_check.setEnabled(not running)
        self.btn_cleanup.setEnabled(not running)
        self._slider.setEnabled(not running and bool(self._entries))

    def _set_progress(self, phase: str, cur: int, tot: int) -> None:
        if tot > 0:
            self.progress.setValue(int(cur / tot * 100))
        self.progress.setFormat(phase)

    def _on_error(self, msg: str) -> None:
        self.log.append(f"\nFEHLER: {msg}\n")
        self._set_running(False)

    def _on_cancelled(self) -> None:
        self.log.append("\nAbgebrochen.\n")
        self._set_running(False)
