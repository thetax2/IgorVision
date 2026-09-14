"""
ui/sort_tab.py
==============
Sort tab: sort images from a Reality-Capture project into component folders.
Based on .imagelist files.
"""

from __future__ import annotations

import os
import shutil
import threading
from pathlib import Path

from PyQt5.QtCore import QThread, Qt, pyqtSignal
from PyQt5.QtGui import QColor
from PyQt5.QtWidgets import (
    QCheckBox, QFileDialog, QFrame, QHBoxLayout,
    QLabel, QMessageBox, QProgressBar, QPushButton,
    QVBoxLayout, QWidget,
)

from ui.log_panel import LogPanel
from ui.path_row import PathRow


# -- Worker --

class SortWorker(QThread):
    sig_log = pyqtSignal(str)
    sig_progress = pyqtSignal(str, int, int)
    sig_done = pyqtSignal(int)
    sig_error = pyqtSignal(str)

    def __init__(self, src: str, dst_root: str, lists: list[str],
                 move: bool, parent=None):
        super().__init__(parent)
        self.src = src
        self.dst_root = dst_root
        self.lists = lists
        self.move = move
        self._cancel = threading.Event()

    def request_cancel(self) -> None:
        self._cancel.set()

    def run(self) -> None:
        try:
            total = 0
            for list_path in self.lists:
                if self._cancel.is_set():
                    self.sig_done.emit(total)
                    return

                folder_name = Path(list_path).stem
                component_dir = Path(self.dst_root) / folder_name
                component_dir.mkdir(parents=True, exist_ok=True)

                self.sig_log.emit(f"Folder: {folder_name}\n")

                with open(list_path, "r", encoding="utf-8") as f:
                    lines = [l.strip() for l in f.readlines() if l.strip()]

                count = 0
                for i, line in enumerate(lines, 1):
                    if self._cancel.is_set():
                        self.sig_done.emit(total)
                        return

                    filename = os.path.basename(line)
                    src_file = Path(self.src) / filename
                    dst_file = component_dir / filename

                    if src_file.exists():
                        if self.move:
                            shutil.move(str(src_file), str(dst_file))
                        else:
                            shutil.copy2(str(src_file), str(dst_file))
                        count += 1
                    else:
                        self.sig_log.emit(f"  ! {filename} not found\n")

                    self.sig_progress.emit(
                        f"{folder_name}: {i}/{len(lines)}", i, len(lines)
                    )

                self.sig_log.emit(f"  -> {count} images sorted\n")
                total += count

            self.sig_log.emit(f"\nDone: {total} images sorted.\n")
            self.sig_done.emit(total)

        except Exception as e:
            self.sig_error.emit(str(e))


# -- Tab --

class SortTab(QWidget):
    def __init__(self, title: str = "Sort", parent=None):
        super().__init__(parent)
        self._worker: SortWorker | None = None
        self._lists: list[str] = []
        self._build()

    def _section_header(self, text: str) -> QLabel:
        lbl = QLabel(text)
        lbl.setStyleSheet("color: #f0a35e; font-weight: bold; font-size: 13px;")
        return lbl

    def _framed(self) -> QFrame:
        frame = QFrame()
        frame.setFrameShape(QFrame.StyledPanel)
        frame.setStyleSheet(
            "QFrame { border: 1px solid rgba(128,128,128,0.25); border-radius: 6px; }"
        )
        return frame

    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 10, 12, 10)
        root.setSpacing(8)

        # ── Section: paths ──
        root.addWidget(self._section_header("Paths"))

        pfade_frame = self._framed()
        pfade_layout = QVBoxLayout(pfade_frame)
        pfade_layout.setContentsMargins(12, 10, 12, 10)
        pfade_layout.setSpacing(10)

        self._pr_src = PathRow("Source folder (images):", kind="folder")
        pfade_layout.addWidget(self._pr_src)

        self._pr_dst = PathRow("Target root (subfolders):", kind="folder")
        pfade_layout.addWidget(self._pr_dst)

        root.addWidget(pfade_frame)

        # ── Section: options ──
        root.addWidget(self._section_header("Options"))

        opt_frame = self._framed()
        opt_layout = QVBoxLayout(opt_frame)
        opt_layout.setContentsMargins(12, 10, 12, 10)
        opt_layout.setSpacing(10)

        # Imagelist row
        list_row = QHBoxLayout()
        list_row.addWidget(QLabel("Image list:"))
        self._lbl_lists = QLabel("none loaded")
        self._lbl_lists.setStyleSheet("color: gray;")
        btn_load = QPushButton("Load ...")
        btn_load.clicked.connect(self._load_lists)
        list_row.addWidget(self._lbl_lists)
        list_row.addWidget(btn_load)
        list_row.addStretch()
        opt_layout.addLayout(list_row)

        # Checkbox
        self._chk_move = QCheckBox("Move (instead of copy)")
        opt_layout.addWidget(self._chk_move)

        opt_layout.addStretch()
        root.addWidget(opt_frame)

        # ── Buttons ──
        btn_row = QHBoxLayout()
        self.btn_start = QPushButton("Sort")
        self.btn_start.setObjectName("btnPrimary")
        self.btn_start.setMinimumHeight(36)
        self.btn_start.clicked.connect(self._start)

        self.btn_cancel = QPushButton("Cancel")
        self.btn_cancel.setObjectName("btnDanger")
        self.btn_cancel.setMinimumHeight(36)
        self.btn_cancel.setEnabled(False)
        self.btn_cancel.clicked.connect(self._cancel)

        btn_row.addWidget(self.btn_start)
        btn_row.addWidget(self.btn_cancel)
        btn_row.addStretch()
        root.addLayout(btn_row)

        # ── Progress ──
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress.setTextVisible(True)
        self.progress.setFormat("")
        root.addWidget(self.progress)

        # ── Log ──
        self.log = LogPanel()
        self.log.setMaximumHeight(160)
        root.addWidget(self.log)

    # -- Helper --

    def _load_lists(self) -> None:
        files, _ = QFileDialog.getOpenFileNames(
            self, "Image list files", "",
            "Image list (*.imagelist);;Text files (*.txt);;All (*)",
        )
        if files:
            self._lists = files
            self._lbl_lists.setText(f"{len(files)} list(s) loaded")
            self._lbl_lists.setStyleSheet("color: black;")

    # -- Aktionen --

    def _start(self) -> None:
        src = self._pr_src.value
        dst = self._pr_dst.value

        if not src:
            QMessageBox.warning(self, "Error", "Please specify a source folder.")
            return
        if not dst:
            QMessageBox.warning(self, "Error", "Please specify a target root.")
            return
        if not self._lists:
            QMessageBox.warning(self, "Error", "Please load image list files.")
            return

        self.log.clear()
        self.progress.setValue(0)
        self.btn_start.setEnabled(False)
        self.btn_cancel.setEnabled(True)

        self._worker = SortWorker(
            src, dst, self._lists,
            self._chk_move.isChecked(),
            parent=self,
        )
        self._worker.sig_log.connect(self.log.append)
        self._worker.sig_progress.connect(self._set_progress)
        self._worker.sig_done.connect(self._on_done)
        self._worker.sig_error.connect(self._on_error)
        self._worker.start()

    def _cancel(self) -> None:
        if self._worker and self._worker.isRunning():
            self._worker.request_cancel()

    # -- Slots --

    def _set_progress(self, phase: str, cur: int, tot: int) -> None:
        if tot > 0:
            self.progress.setValue(int(cur / tot * 100))
        self.progress.setFormat(phase)

    def _on_done(self, total: int) -> None:
        self.log.append(f"\nFinished: {total} images.\n")
        self.btn_start.setEnabled(True)
        self.btn_cancel.setEnabled(False)

    def _on_error(self, msg: str) -> None:
        self.log.append(f"\nERROR: {msg}\n")
        self.btn_start.setEnabled(True)
        self.btn_cancel.setEnabled(False)
