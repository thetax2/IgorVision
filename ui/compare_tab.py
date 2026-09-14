"""
ui/compare_tab.py
=================
Compare tab: reconcile Ingest_Backup vs. shooting days via CompareWorker.
"""

from __future__ import annotations

from pathlib import Path

from PyQt5.QtCore import Qt, QSettings, QStandardPaths
from PyQt5.QtGui import QColor
from PyQt5.QtWidgets import (
    QCheckBox, QFileDialog, QGroupBox, QHBoxLayout, QHeaderView,
    QLabel, QLineEdit, QMessageBox, QProgressBar, QPushButton,
    QSizePolicy, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from tools.comparison import (
    CompareConfig, DEFAULT_EXTENSIONS, NamePartConfig, SubfolderConfig,
    detect_exiftool,
    is_within,
)
from tools.db import db_file_name
from ui.log_panel import LogPanel
from ui.path_row import PathRow
from tools.compare_worker import (
    CompareWorker, ReplanWorker, TransferWorker,
)


# ── Info text (also used in the "Help" menu) ────────────────────────────────

COMPARE_INFO = (
    "This tool finds all media (RAW, JPG, video) that exist in the Ingest_Backup "
    "but are missing from the shooting days – and lists them with their target path. "
    "Only afterwards does the user manually decide whether they are <b>copied</b> or <b>moved</b>.<br><br>"
    "<b>Workflow:</b><br>"
    "1. <b>Scan</b> – all media in the shooting days and the Ingest_Backup are collected.<br>"
    "2. <b>Compare</b> – files are matched by name + size (optionally a SHA-256 hash).<br>"
    "3. <b>Missing files</b> – everything present in the backup but not in the shooting days.<br>"
    "4. <b>Metadata</b> – capture date, focal length, camera model and lens are read from the "
    "EXIF data (ExifTool); the date falls back to the file date otherwise.<br>"
    "5. <b>Plan target</b> – depending on the settings, the directory and file name are determined:<br>"
    "   • <b>Directories</b> – subfolders by capture date, focal length, camera model, "
    "lens or photo/video (in this order); missing values go into \"unknown\".<br>"
    "   • <b>Rename</b> – a prefix before and a suffix after the original name; optionally "
    "<b>name parts</b> from the EXIF data at a fixed position (capture date <i>before</i> the name; "
    "camera model, lens, focal length <i>after</i> the suffix). The separator from the "
    "<b>Separator</b> field is inserted between <b>all</b> name parts automatically – separators "
    "typed manually at the prefix/suffix edges are removed so nothing is doubled.<br>"
    "6. <b>Refresh</b> – after changing the rename, directories or target folder, the "
    "<b>Refresh</b> button re-plans the target paths from the last scan – without rescanning "
    "(EXIF data stay cached). If the shooting-days or backup path changes, a new "
    "<b>Scan</b> is required.<br>"
    "7. <b>Manual transfer</b> – with the <b>Copy</b> or <b>Move</b> buttons the missing files are "
    "transferred to the target folder; missing subfolders are created on the fly. "
    "The active options (directories, rename, paths) are read at transfer time – "
    "even if they were changed after the scan. "
    "After the transfer a CSV report (differenzen_report.csv) is written.<br><br>"
    "<b>Notes:</b><br>"
    "• The comparison itself writes <b>nothing</b> – it only produces the list of missing files.<br>"
    "• <b>Copy</b> keeps the originals; <b>Move</b> removes them from the backup.<br>"
    "• Identical files (same name, same size/same hash) are skipped."
)


def show_compare_info(parent=None) -> None:
    """Info dialog: what the Compare worker does (short form + workflow)."""
    box = QMessageBox(parent)
    box.setWindowTitle("How it works – Compare")
    box.setIcon(QMessageBox.Information)
    box.setTextFormat(Qt.RichText)
    box.setText(COMPARE_INFO)
    box.setStandardButtons(QMessageBox.Ok)
    box.setTextInteractionFlags(Qt.TextSelectableByMouse)
    box.exec()


# ── Tab ──────────────────────────────────────────────────────────────────────

class CompareTab(QWidget):
    def __init__(self, title: str = "Compare", parent=None):
        super().__init__(parent)
        self._worker: CompareWorker | ReplanWorker | TransferWorker | None = None
        self._results: list = []
        self._cfg: CompareConfig | None = None
        self._scan_src: tuple[Path, Path] | None = None
        self._action: str = "compare"
        self._build()

    # -- UI --

    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 10, 12, 10)
        root.setSpacing(8)

        # -- Buttons (top) --
        btn_row = QHBoxLayout()
        self.btn_run = QPushButton("▶  Scan")
        self.btn_run.setObjectName("btnPrimary")
        self.btn_run.setMinimumHeight(36)
        self.btn_run.clicked.connect(self._run)

        self.btn_replan = QPushButton("↻  Refresh")
        self.btn_replan.setMinimumHeight(36)
        self.btn_replan.setEnabled(False)
        self.btn_replan.setToolTip(
            "Re-plan the target paths from the last scan – without rescanning.\n"
            "Only possible while the shooting-days and backup paths stay unchanged."
        )
        self.btn_replan.clicked.connect(self._replan)

        self.btn_cancel = QPushButton("Cancel")
        self.btn_cancel.setObjectName("btnDanger")
        self.btn_cancel.setMinimumHeight(36)
        self.btn_cancel.setEnabled(False)
        self.btn_cancel.clicked.connect(self._cancel)

        self.btn_copy = QPushButton("Copy")
        self.btn_copy.setMinimumHeight(36)
        self.btn_copy.setEnabled(False)
        self.btn_copy.setToolTip(
            "Copy the missing files to the target folder (originals are kept)"
        )
        self.btn_copy.clicked.connect(lambda: self._transfer("copy"))

        self.btn_move = QPushButton("Move")
        self.btn_move.setMinimumHeight(36)
        self.btn_move.setEnabled(False)
        self.btn_move.setToolTip(
            "Move the missing files to the target folder (originals are removed)"
        )
        self.btn_move.clicked.connect(lambda: self._transfer("move"))

        self.btn_info = QPushButton("ℹ  How it works")
        self.btn_info.setMinimumHeight(36)
        self.btn_info.setToolTip("Explanation of what the comparison does")
        self.btn_info.clicked.connect(lambda: show_compare_info(self))

        btn_row.addWidget(self.btn_run)
        btn_row.addWidget(self.btn_replan)
        btn_row.addWidget(self.btn_cancel)
        btn_row.addStretch(1)
        btn_row.addWidget(self.btn_copy)
        btn_row.addWidget(self.btn_move)
        btn_row.addWidget(self.btn_info)
        root.addLayout(btn_row)

        # -- Left: paths + rename | Right: directories --
        mid = QHBoxLayout()

        left_col = QWidget()
        left_lay = QVBoxLayout(left_col)
        left_lay.setContentsMargins(0, 0, 0, 0)
        left_lay.setSpacing(8)

        # Paths (top-left): three paths + hash matching
        paths_box = QGroupBox("Paths")
        paths_lay = QVBoxLayout(paths_box)
        paths_lay.setContentsMargins(10, 0, 10, 10)  # tighter to the title, no double margin
        paths_lay.setSpacing(6)

        self._pr_root = PathRow("Shooting days (Root):", kind="folder")
        self._pr_backup = PathRow("Ingest_Backup:", kind="folder")
        self._pr_out = PathRow("Target folder:", kind="folder")
        for pr in (self._pr_root, self._pr_backup, self._pr_out):
            paths_lay.addWidget(pr)

        # Hash matching (was in "Options", now part of the path settings)
        self._chk_hash = QCheckBox("SHA-256 matching (slower, 100 % safe)")
        self._chk_hash.setChecked(True)
        paths_lay.addWidget(self._chk_hash)
        paths_lay.addStretch(1)  # keep content at the top, no row stretching

        # Keep the box at its natural height – the rest belongs to the table.
        paths_box.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Maximum)
        left_lay.addWidget(paths_box)

        # Rename (bottom-left): its own section under "Paths"
        rename_box = QGroupBox("Rename")
        rename = QVBoxLayout(rename_box)
        rename.setContentsMargins(10, 0, 10, 10)
        rename.setSpacing(6)

        self._chk_rename = QCheckBox("Enable renaming")
        self._chk_rename.setToolTip(
            "On = the file name is rebuilt during transfer:\n"
            "prefix before, suffix after the original name, optional EXIF name parts.\n"
            "The separator is inserted between all parts automatically – no\n"
            "manual \"_\" needed in the prefix/suffix.\n"
            "Off = the original name is kept unchanged."
        )
        rename.addWidget(self._chk_rename)

        row_ps = QHBoxLayout()
        row_ps.setContentsMargins(0, 0, 0, 0)
        row_ps.addWidget(QLabel("Prefix:"))
        self._ed_prefix = QLineEdit()
        self._ed_prefix.setPlaceholderText("e.g. RAW")
        self._ed_prefix.setToolTip(
            "Comes before the file name (extension is kept).\n"
            "The separator is inserted automatically – no manual \"_\" needed.\n"
            "Example: RAW → 2024-05-01_RAW_image0001.cr3 (with capture date)"
        )
        self._ed_prefix.setEnabled(False)
        row_ps.addWidget(self._ed_prefix, 1)
        row_ps.addWidget(QLabel("Suffix:"))
        self._ed_suffix = QLineEdit()
        self._ed_suffix.setPlaceholderText("e.g. backup")
        self._ed_suffix.setToolTip(
            "Comes after the original name (extension is kept).\n"
            "The separator is inserted automatically; separators typed at the\n"
            "edges are removed so nothing is doubled\n"
            "(\"_backup\" + separator \"_\" → \"_backup\").\n"
            "Example: backup → image0001_backup.cr3"
        )
        self._ed_suffix.setEnabled(False)
        row_ps.addWidget(self._ed_suffix, 1)
        row_ps.addWidget(QLabel("Separator:"))
        self._ed_sep = QLineEdit()
        self._ed_sep.setText("_")
        self._ed_sep.setFixedWidth(48)
        self._ed_sep.setAlignment(Qt.AlignCenter)
        self._ed_sep.setToolTip(
            "Separator between all name parts – inserted automatically,\n"
            "including around the prefix/suffix (e.g. \"_\" or \"-\").\n"
            "Example \"_\":  2024-05-01_RAW_image0001_backup_Canon EOS R5_35mm.cr3"
        )
        self._ed_sep.setEnabled(False)
        row_ps.addWidget(self._ed_sep)
        rename.addLayout(row_ps)

        # Name parts (optional): EXIF values in the file name, fixed position
        row_np = QHBoxLayout()
        row_np.setContentsMargins(0, 0, 0, 0)
        lbl_np = QLabel("Name parts:")
        lbl_np.setToolTip(
            "Optional: EXIF values are added to the file name.\n"
            "Capture date comes BEFORE the name; camera model, lens,\n"
            "focal length come AFTER the suffix. Missing values are\n"
            "skipped. Off = only the subfolders (directories) are created."
        )
        row_np.addWidget(lbl_np)
        self._np_date = QCheckBox("Capture date")
        self._np_model = QCheckBox("Camera model")
        self._np_lens = QCheckBox("Lens")
        self._np_focal = QCheckBox("Focal length")
        for cb in (self._np_date, self._np_model, self._np_lens, self._np_focal):
            cb.setEnabled(False)
            row_np.addWidget(cb)
        row_np.addStretch()
        rename.addLayout(row_np)
        self._chk_rename.toggled.connect(self._on_rename_toggled)

        # ExifTool is a program-wide setting (Settings → Preferences).
        rename.addStretch(1)  # keep content at the top, no row stretching

        # Keep the box at its natural height – the rest belongs to the table.
        rename_box.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Maximum)
        left_lay.addWidget(rename_box)

        left_lay.addStretch(1)
        mid.addWidget(left_col, 2)

        # Directories (right): subfolder checkboxes
        verz_box = QGroupBox("Directories")
        verz = QVBoxLayout(verz_box)
        verz.setContentsMargins(10, 0, 10, 10)
        verz.setSpacing(6)

        lbl_verz = QLabel("Create subfolders:")
        lbl_verz.setToolTip(
            "Missing files are placed in subfolders under the target folder,\n"
            "in this order. Missing values go into \"unknown\"."
        )
        verz.addWidget(lbl_verz)
        self._sf_date = QCheckBox("by capture date")
        self._sf_focal = QCheckBox("by focal length")
        self._sf_model = QCheckBox("by camera model")
        self._sf_lens = QCheckBox("by lens")
        self._sf_media = QCheckBox("by photo/video")
        for cb in (self._sf_date, self._sf_focal, self._sf_model, self._sf_lens, self._sf_media):
            verz.addWidget(cb)

        verz.addStretch()
        verz_box.setMinimumWidth(240)
        # Like Paths: keep the natural height – the rest belongs to the table.
        verz_box.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Maximum)
        # Top-align: the "Directories" title sits at the same height as "Paths".
        mid.addWidget(verz_box, 1, Qt.AlignTop)

        root.addLayout(mid)

        # -- Progress --
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress.setTextVisible(True)
        self.progress.setFormat("")
        root.addWidget(self.progress)

        # -- Table (full width) + log (narrow strip at the bottom) --
        self._table = QTableWidget(0, 4)
        self._table.setHorizontalHeaderLabels(
            ["File", "Date", "Target", "Status"]
        )
        hh = self._table.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.Stretch)
        hh.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(2, QHeaderView.Stretch)
        hh.setSectionResizeMode(3, QHeaderView.ResizeToContents)
        self._table.setEditTriggers(QTableWidget.NoEditTriggers)
        self._table.setSelectionBehavior(QTableWidget.SelectRows)
        self._table.setAlternatingRowColors(True)

        self.log = LogPanel()
        # No splitter anymore: the table gets the full width, the log
        # (operation log, errors) stays as a narrow strip below.
        self.log.setFixedHeight(90)

        root.addWidget(self._table, 1)
        root.addWidget(self.log)

    # -- Helper --

    def _on_rename_toggled(self, on: bool) -> None:
        self._ed_prefix.setEnabled(on)
        self._ed_suffix.setEnabled(on)
        self._ed_sep.setEnabled(on)
        for cb in (self._np_date, self._np_model, self._np_lens, self._np_focal):
            cb.setEnabled(on)

    def _exiftool_bin(self) -> str:
        """Global ExifTool setting (Settings → Preferences).

        Falls back to auto-detection (PATH + common install locations)
        when no path is configured.
        """
        p = str(QSettings().value("prefs/exiftool", "") or "").strip()
        return p or detect_exiftool()

    # -- Actions --

    def _build_cfg(self) -> CompareConfig | None:
        """Build a CompareConfig from the current UI state (paths, rename, directories).

        Called both for the scan and before the transfer, so that changes to
        the options (e.g. "by capture date" enabled only after the scan) take
        effect during copy/move.
        """
        root_s = self._pr_root.value
        backup_s = self._pr_backup.value
        out_s = self._pr_out.value

        # Collect missing paths
        missing = [
            name for name, val in (
                ("Shooting days (Root)", root_s),
                ("Ingest_Backup", backup_s),
                ("Target folder", out_s),
            ) if not val
        ]
        if missing:
            QMessageBox.warning(
                self, "Missing paths",
                "Please provide:\n• " + "\n• ".join(missing),
            )
            return None

        import_root = Path(root_s)
        backup_dir = Path(backup_s)

        if not import_root.is_dir():
            QMessageBox.warning(self, "Error",
                                f"Shooting days folder not found:\n{import_root}")
            return None
        if not backup_dir.is_dir():
            QMessageBox.warning(self, "Error",
                                f"Ingest_Backup folder not found:\n{backup_dir}")
            return None
        if is_within(import_root, backup_dir):
            ret = QMessageBox.question(
                self, "Warning",
                "The shooting days folder is inside the Ingest_Backup.\n"
                "This would make the entire backup appear as missing.\n\n"
                "Continue anyway?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if ret != QMessageBox.Yes:
                return None

        rename_on = self._chk_rename.isChecked()

        # SQLite index in the app data (%APPDATA%\DAM\Backup Compare),
        # falling back to the home folder if AppDataLocation is empty.
        app_data = QStandardPaths.writableLocation(
            QStandardPaths.AppDataLocation
        )
        db_dir = Path(app_data) if app_data else Path.home() / ".backup-compare"

        return CompareConfig(
            import_root=import_root,
            backup_dir=backup_dir,
            output_dir=Path(out_s),
            extensions=DEFAULT_EXTENSIONS,
            use_hash=self._chk_hash.isChecked(),
            exiftool_bin=self._exiftool_bin(),
            rename_prefix=self._ed_prefix.text().strip() if rename_on else "",
            rename_suffix=self._ed_suffix.text().strip() if rename_on else "",
            subfolders=SubfolderConfig(
                date=self._sf_date.isChecked(),
                focal=self._sf_focal.isChecked(),
                model=self._sf_model.isChecked(),
                lens=self._sf_lens.isChecked(),
                media=self._sf_media.isChecked(),
            ),
            name_parts=NamePartConfig(
                date=self._np_date.isChecked() and rename_on,
                model=self._np_model.isChecked() and rename_on,
                lens=self._np_lens.isChecked() and rename_on,
                focal=self._np_focal.isChecked() and rename_on,
                separator=self._ed_sep.text().strip() if rename_on else "",
            ),
            db_path=db_dir / db_file_name(),
        )

    def _run(self) -> None:
        cfg = self._build_cfg()
        if cfg is None:
            return
        self._scan_src = (cfg.import_root, cfg.backup_dir)

        self._table.setRowCount(0)
        self.log.clear()
        self.progress.setValue(0)
        self.btn_run.setEnabled(False)
        self.btn_cancel.setEnabled(True)
        self.btn_copy.setEnabled(False)
        self.btn_move.setEnabled(False)
        self.btn_replan.setEnabled(False)
        self._action = "compare"
        self._results = []

        self._cfg = cfg

        self._worker = CompareWorker(cfg, parent=self)
        self._worker.sig_log.connect(self.log.append)
        self._worker.sig_progress.connect(self._set_progress)
        self._worker.sig_done.connect(self._on_done)
        self._worker.sig_error.connect(self._on_error)
        self._worker.sig_cancelled.connect(self._on_cancelled)
        self._worker.start()

    def _replan(self) -> None:
        """Re-plan the target paths from the last scan – without rescanning.

        Uses the cached EXIF metadata of the result list; only the
        name/subfolder is recomputed and it is checked whether the target
        already exists.
        """
        if self._worker is not None and self._worker.isRunning():
            return
        if not self._results:
            QMessageBox.information(
                self, "Compare", "Run a comparison first (▶ Scan)."
            )
            return
        cfg = self._build_cfg()
        if cfg is None:
            return
        src = (cfg.import_root, cfg.backup_dir)
        if self._scan_src is not None and src != self._scan_src:
            QMessageBox.warning(
                self, "Source changed",
                "The shooting-days or backup path has changed since the last scan.\n"
                "Please rescan with ▶ Scan.",
            )
            return
        self._cfg = cfg

        # Planning (stat/exists) now runs in the background → no GUI freeze.
        self.btn_run.setEnabled(False)
        self.btn_replan.setEnabled(False)
        self.btn_copy.setEnabled(False)
        self.btn_move.setEnabled(False)
        self.btn_cancel.setEnabled(True)
        self.progress.setValue(0)
        self.progress.setFormat("Refreshing …")

        self._worker = ReplanWorker(self._results, cfg, parent=self)
        self._worker.sig_progress.connect(self._set_progress)
        self._worker.sig_done.connect(self._on_replan_done)
        self._worker.sig_error.connect(self._on_error)
        self._worker.sig_cancelled.connect(self._on_replan_cancelled)
        self._worker.start()

    def _cancel(self) -> None:
        if self._worker and self._worker.isRunning():
            self._worker.request_cancel()

    def _transfer(self, mode: str) -> None:
        """Manually transfer all entries marked as \"missing\"."""
        if self._worker is not None and self._worker.isRunning():
            return
        if self._cfg is None:
            QMessageBox.information(self, "Compare",
                                    "Run a comparison first.")
            return
        if not any(r.status == "missing" for r in self._results):
            QMessageBox.information(self, "Compare",
                                    "No missing files to transfer.")
            return

        cfg = self._build_cfg()
        if cfg is None:
            return
        self._cfg = cfg

        if mode == "move":
            ret = QMessageBox.question(
                self, "Move",
                "The missing files will be moved to the target folder –\n"
                "the originals in the backup will be REMOVED.\n\n"
                "Continue?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if ret != QMessageBox.Yes:
                return

        self._action = "transfer"
        self.btn_run.setEnabled(False)
        self.btn_copy.setEnabled(False)
        self.btn_move.setEnabled(False)
        self.btn_replan.setEnabled(False)
        self.btn_cancel.setEnabled(True)
        self.progress.setValue(0)

        # Hand directly to the background worker – no main-thread blocking!
        self._worker = TransferWorker(self._results, self._cfg, mode, parent=self)
        self._worker.sig_log.connect(self.log.append)
        self._worker.sig_progress.connect(self._set_progress)
        self._worker.sig_done.connect(self._on_done)
        self._worker.sig_error.connect(self._on_error)
        self._worker.sig_cancelled.connect(self._on_cancelled)
        self._worker.start()

    # -- Slot handlers --

    def _set_progress(self, phase: str, cur: int, tot: int) -> None:
        if tot > 0:
            self.progress.setValue(int(cur / tot * 100))
        self.progress.setFormat(phase)

    # -- Table & status --

    def _status_item(self, status: str) -> QTableWidgetItem:
        si = QTableWidgetItem(status)
        si.setForeground(self._status_color(status))
        return si

    @staticmethod
    def _status_color(status: str) -> QColor:
        colors = {
            "missing": "#f5a623",
            "skipped": "#f5a623",
            "present": "#6eb26a",
            "copied": "#6eb26a",
            "moved": "#64b5f6",
        }
        return QColor(colors.get(status, "#000000"))

    def _fill_table(self, results: list) -> None:
        """Fill the table from the result list (parallel to self._results)."""
        out = self._cfg.output_dir if self._cfg else None
        # Repaint only at the end, not after every row → faster.
        self._table.setUpdatesEnabled(False)
        try:
            self._table.setRowCount(len(results))
            for row, r in enumerate(results):
                self._table.setItem(row, 0, QTableWidgetItem(r.entry.path.name))
                self._table.setItem(row, 1, QTableWidgetItem(r.meta.date_str))
                try:
                    disp = r.dest_path.relative_to(out).as_posix() if out else str(r.dest_path)
                except (ValueError, TypeError):
                    disp = r.dest_path.name
                self._table.setItem(row, 2, QTableWidgetItem(disp))
                self._table.setItem(row, 3, self._status_item(r.status))
        finally:
            self._table.setUpdatesEnabled(True)

    def _refresh_statuses(self) -> None:
        """Update the status and target columns in place (rows stay).

        After the transfer `r.dest_path` holds the final path (incl. the
        _1/_2 collision suffix) – the target column is updated accordingly.
        """
        out = self._cfg.output_dir if self._cfg else None
        self._table.setUpdatesEnabled(False)
        try:
            for row, r in enumerate(self._results):
                try:
                    disp = r.dest_path.relative_to(out).as_posix() if out else str(r.dest_path)
                except (ValueError, TypeError):
                    disp = r.dest_path.name
                self._table.setItem(row, 2, QTableWidgetItem(disp))
                self._table.setItem(row, 3, self._status_item(r.status))
        finally:
            self._table.setUpdatesEnabled(True)

    def _set_transfer_buttons(self) -> None:
        has_missing = any(r.status == "missing" for r in self._results)
        self.btn_copy.setEnabled(has_missing)
        self.btn_move.setEnabled(has_missing)

    def _on_done(self, results: list) -> None:
        self._results = results
        if self._action == "transfer":
            self._refresh_statuses()
        else:
            self._fill_table(results)
        self._set_transfer_buttons()
        self.log.append(f"\nDone: {len(results)} entries.\n")
        self.btn_run.setEnabled(True)
        self.btn_cancel.setEnabled(False)
        self.btn_replan.setEnabled(bool(results))

    def _on_error(self, msg: str) -> None:
        self.log.append(f"\nERROR: {msg}\n")
        self.btn_run.setEnabled(True)
        self.btn_cancel.setEnabled(False)
        self.btn_replan.setEnabled(bool(self._results))
        self._set_transfer_buttons()

    def _on_cancelled(self) -> None:
        self.log.append("\nAborted.\n")
        self.btn_run.setEnabled(True)
        self.btn_cancel.setEnabled(False)
        self.btn_replan.setEnabled(bool(self._results))
        self._set_transfer_buttons()

    def _on_replan_done(self, results: list, changed: int) -> None:
        self._results = results
        self._refresh_statuses()
        self._set_transfer_buttons()
        self.log.append(
            f"\nRefreshed: {changed} target(s) changed, "
            f"{len(self._results)} entries checked (no rescan).\n"
        )
        self.btn_run.setEnabled(True)
        self.btn_cancel.setEnabled(False)
        self.btn_replan.setEnabled(bool(results))

    def _on_replan_cancelled(self) -> None:
        # Partially updated entries stay effective in place.
        self.log.append("\nRefresh aborted (partial result kept).\n")
        self._refresh_statuses()
        self._set_transfer_buttons()
        self.btn_run.setEnabled(True)
        self.btn_cancel.setEnabled(False)
        self.btn_replan.setEnabled(bool(self._results))
