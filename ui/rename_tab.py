"""
ui/rename_tab.py
================
Rename tab: rename RAW files based on their JPG references.
Uses RenameWorker (analyze + execute) with RenameConfig.
"""

from __future__ import annotations

from pathlib import Path

from PyQt5.QtCore import Qt
from PyQt5.QtGui import QColor
from PyQt5.QtWidgets import (
    QCheckBox, QGroupBox, QHBoxLayout, QHeaderView,
    QProgressBar, QPushButton, QSplitter, QTableWidget,
    QTableWidgetItem, QVBoxLayout, QWidget,
)

from tools.rename_engine import RenameConfig, RenameMapping
from ui.log_panel import LogPanel
from ui.path_row import PathRow
from tools.rename_worker import RenameWorker


class RenameTab(QWidget):
    def __init__(self, title: str = "Rename", parent=None):
        super().__init__(parent)
        self._worker: RenameWorker | None = None
        self._build()

    # -- UI --

    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 10, 12, 10)
        root.setSpacing(8)

        # -- Paths --
        path_box = QGroupBox("Paths")
        pg = QVBoxLayout(path_box)
        pg.setSpacing(6)

        self._pr_raw = PathRow("RAW folder:", kind="folder")
        pg.addWidget(self._pr_raw)

        self._pr_jpg = PathRow("JPG folder:", kind="folder")
        pg.addWidget(self._pr_jpg)

        self._pr_out = PathRow("Target folder:", kind="folder")
        pg.addWidget(self._pr_out)

        root.addWidget(path_box)

        # -- Options --
        opt_box = QGroupBox("Options")
        opt_lay = QHBoxLayout(opt_box)
        opt_lay.setSpacing(16)

        self._chk_dry = QCheckBox("Dry run (preview only)")
        self._chk_dry.setChecked(True)
        opt_lay.addWidget(self._chk_dry)

        self._chk_overwrite = QCheckBox("Overwrite")
        self._chk_overwrite.setChecked(False)
        opt_lay.addWidget(self._chk_overwrite)

        opt_lay.addStretch()
        root.addWidget(opt_box)

        # -- Buttons --
        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)

        self.btn_analyze = QPushButton("Analyze")
        self.btn_analyze.setMinimumHeight(36)
        self.btn_analyze.clicked.connect(self._analyze)

        self.btn_execute = QPushButton("Execute")
        self.btn_execute.setObjectName("btnPrimary")
        self.btn_execute.setMinimumHeight(36)
        self.btn_execute.setEnabled(False)
        self.btn_execute.clicked.connect(self._execute)

        self.btn_cancel = QPushButton("Cancel")
        self.btn_cancel.setObjectName("btnDanger")
        self.btn_cancel.setMinimumHeight(36)
        self.btn_cancel.setEnabled(False)
        self.btn_cancel.clicked.connect(self._cancel)

        btn_row.addWidget(self.btn_analyze)
        btn_row.addWidget(self.btn_execute)
        btn_row.addWidget(self.btn_cancel)
        btn_row.addStretch()
        root.addLayout(btn_row)

        # -- Progress --
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress.setTextVisible(True)
        self.progress.setFormat("")
        root.addWidget(self.progress)

        # -- Tabelle + Log --
        splitter = QSplitter(Qt.Horizontal)

        self._table = QTableWidget(0, 4)
        self._table.setHorizontalHeaderLabels(
            ["Base ID", "RAW (old)", "New name", "Status"]
        )
        hh = self._table.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(1, QHeaderView.Stretch)
        hh.setSectionResizeMode(2, QHeaderView.Stretch)
        hh.setSectionResizeMode(3, QHeaderView.ResizeToContents)
        self._table.setEditTriggers(QTableWidget.NoEditTriggers)
        self._table.setSelectionBehavior(QTableWidget.SelectRows)
        self._table.setAlternatingRowColors(True)

        self.log = LogPanel()
        self.log.setMaximumHeight(140)

        splitter.addWidget(self._table)
        splitter.addWidget(self.log)
        splitter.setSizes([750, 250])
        root.addWidget(splitter, 1)

        # -- Connections --
        self._pr_raw.sig_path_changed.connect(self._on_path_changed)
        self._pr_jpg.sig_path_changed.connect(self._on_path_changed)
        self._pr_out.sig_path_changed.connect(self._on_path_changed)

    # -- Helper --

    def _on_path_changed(self, _path: str) -> None:
        self._table.setRowCount(0)
        self.btn_execute.setEnabled(False)

    def _paths_valid(self) -> bool:
        from PyQt5.QtWidgets import QMessageBox

        raw = self._pr_raw.value
        jpg = self._pr_jpg.value
        out = self._pr_out.value

        missing = []
        if not raw:
            missing.append("RAW folder")
        if not jpg:
            missing.append("JPG folder")
        if not out:
            missing.append("Target folder")
        if missing:
            QMessageBox.warning(
                self, "Missing paths",
                "Please provide:\n" + "\n".join(missing)
            )
            return False
        return True

    def _make_cfg(self, mode: str) -> RenameConfig:
        return RenameConfig(
            mode=mode,
            raw_dirs=[Path(self._pr_raw.value)],
            jpg_dirs=[Path(self._pr_jpg.value)],
            raw_extensions=[".cr3", ".cr2", ".arw", ".nef", ".dng", ".raf"],
            jpg_extensions=[".jpg", ".jpeg"],
            target_dir=Path(self._pr_out.value),
            dry_run=self._chk_dry.isChecked(),
            create_backup=True,
            overwrite=self._chk_overwrite.isChecked(),
        )

    # -- Aktionen --

    def _analyze(self) -> None:
        if not self._paths_valid():
            return

        self._table.setRowCount(0)
        self.log.clear()
        self.progress.setValue(0)
        self.btn_analyze.setEnabled(False)
        self.btn_execute.setEnabled(False)
        self.btn_cancel.setEnabled(True)

        cfg = self._make_cfg("analyze")
        self._worker = RenameWorker(cfg, parent=self)
        self._worker.sig_log.connect(self.log.append)
        self._worker.sig_progress.connect(self._set_progress)
        self._worker.sig_analyzed.connect(self._on_analyzed)
        self._worker.sig_error.connect(self._on_error)
        self._worker.sig_cancelled.connect(self._on_cancelled)
        self._worker.start()

    def _execute(self) -> None:
        if not self._paths_valid():
            return

        self._table.setRowCount(0)
        self.log.clear()
        self.progress.setValue(0)
        self.btn_analyze.setEnabled(False)
        self.btn_execute.setEnabled(False)
        self.btn_cancel.setEnabled(True)

        cfg = self._make_cfg("execute")
        self._worker = RenameWorker(cfg, parent=self)
        self._worker.sig_log.connect(self.log.append)
        self._worker.sig_progress.connect(self._set_progress)
        self._worker.sig_done.connect(self._on_done)
        self._worker.sig_error.connect(self._on_error)
        self._worker.sig_cancelled.connect(self._on_cancelled)
        self._worker.start()

    def _cancel(self) -> None:
        if self._worker and self._worker.isRunning():
            self._worker.request_cancel()

    # -- Slot-Handler --

    def _set_progress(self, phase: str, cur: int, tot: int) -> None:
        if tot > 0:
            self.progress.setValue(int(cur / tot * 100))
        self.progress.setFormat(phase)

    def _on_analyzed(self, mappings: list) -> None:
        """Tabelle mit RenameMapping-Liste fuellen."""
        for m in mappings:
            r = self._table.rowCount()
            self._table.insertRow(r)
            self._table.setItem(r, 0, QTableWidgetItem(m.base_name))
            self._table.setItem(r, 1, QTableWidgetItem(m.raw_filename))
            self._table.setItem(r, 2, QTableWidgetItem(m.new_filename))
            st = QTableWidgetItem("match")
            st.setForeground(QColor("#6eb26a"))
            self._table.setItem(r, 3, st)

        self.log.append(f"\n{len(mappings)} matches found.\n")
        self.btn_analyze.setEnabled(True)
        self.btn_execute.setEnabled(len(mappings) > 0)
        self.btn_cancel.setEnabled(False)

    def _on_done(self, mappings: list) -> None:
        """Tabelle nach Execute mit Status fuellen."""
        for m in mappings:
            r = self._table.rowCount()
            self._table.insertRow(r)
            self._table.setItem(r, 0, QTableWidgetItem(m.base_name))
            self._table.setItem(r, 1, QTableWidgetItem(m.raw_filename))
            self._table.setItem(r, 2, QTableWidgetItem(m.new_filename))

            si = QTableWidgetItem(m.status)
            if m.status == "done":
                si.setForeground(QColor("#6eb26a"))
            elif m.status == "skipped":
                si.setForeground(QColor("#f5a623"))
            elif m.status == "error":
                si.setForeground(QColor("#ef5350"))
            self._table.setItem(r, 3, si)

        self.btn_analyze.setEnabled(True)
        self.btn_execute.setEnabled(False)
        self.btn_cancel.setEnabled(False)

    def _on_error(self, msg: str) -> None:
        self.log.append(f"\nERROR: {msg}\n")
        self.btn_analyze.setEnabled(True)
        self.btn_execute.setEnabled(False)
        self.btn_cancel.setEnabled(False)

    def _on_cancelled(self) -> None:
        self.log.append("\nAborted.\n")
        self.btn_analyze.setEnabled(True)
        self.btn_execute.setEnabled(False)
        self.btn_cancel.setEnabled(False)
