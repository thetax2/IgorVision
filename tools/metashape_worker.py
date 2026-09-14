"""
workers/metashape_worker.py
===========================
QThread worker for Metashape operations (load / filter / cleanup).
"""

from __future__ import annotations

import threading
from pathlib import Path

from PyQt5.QtCore import QThread, pyqtSignal

from tools.metashape_engine import (
    MetashapeConfig,
    QualityEntry,
    find_orphaned_raws,
    read_quality_scores,
    remove_file,
)


class MetashapeWorker(QThread):
    """
    Modes:
      "load"    – reads quality scores from .psx
      "filter"  – removes JPGs below threshold (phase 1)
      "cleanup" – removes orphaned RAWs (phase 2)
    """

    sig_log = pyqtSignal(str)
    sig_progress = pyqtSignal(str, int, int)
    sig_scores_loaded = pyqtSignal(list)   # list[QualityEntry]
    sig_orphans_found = pyqtSignal(list)  # list[Path]
    sig_done = pyqtSignal(int)            # number of processed files
    sig_error = pyqtSignal(str)
    sig_cancelled = pyqtSignal()

    def __init__(self, cfg: MetashapeConfig, parent=None):
        super().__init__(parent)
        self.cfg = cfg
        self._cancel = threading.Event()

    def request_cancel(self) -> None:
        self._cancel.set()

    # ── RUN ────────────────────────────────────────────────────────────

    def run(self) -> None:
        try:
            if self.cfg.mode == "load":
                self._run_load()
            elif self.cfg.mode == "filter":
                self._run_filter()
            elif self.cfg.mode == "cleanup":
                self._run_cleanup()
        except Exception as e:
            self.sig_error.emit(str(e))

    # ── LOAD ───────────────────────────────────────────────────────────

    def _run_load(self) -> None:
        self.sig_log.emit(f"Reading: {self.cfg.psx_path}\n")
        entries = read_quality_scores(self.cfg.psx_path)
        self.sig_log.emit(f"✓ {len(entries)} photos with a quality score found.\n")

        if entries:
            qs = [e.quality for e in entries]
            self.sig_log.emit(
                f"   min={min(qs):.4f}  max={max(qs):.4f}  mean={sum(qs)/len(qs):.4f}\n"
            )

        self.sig_scores_loaded.emit(entries)
        self.sig_done.emit(len(entries))

    # ── FILTER (Phase 1) ───────────────────────────────────────────────

    def _run_filter(self) -> None:
        entries = read_quality_scores(self.cfg.psx_path)
        jpg_dir = Path(self.cfg.jpg_dir)
        threshold = self.cfg.threshold
        mode = self.cfg.filter_mode

        bad = [e for e in entries if e.quality < threshold]
        self.sig_log.emit(
            f"Threshold < {threshold:.3f}: {len(bad)} / {len(entries)} JPGs\n"
        )

        if not bad:
            self.sig_log.emit("No JPGs below threshold – nothing to do.\n")
            self.sig_done.emit(0)
            return

        ok, fail = 0, 0
        for i, e in enumerate(bad, 1):
            if self._cancel.is_set():
                self.sig_log.emit("Aborted.\n")
                self.sig_cancelled.emit()
                return

            src = jpg_dir / e.filename
            if not src.exists():
                self.sig_log.emit(f"  ! {e.filename} not found\n")
                fail += 1
                continue

            try:
                remove_file(src, mode)
                ok += 1
                self.sig_log.emit(f"  ✓ {e.filename}  (Q={e.quality:.4f})\n")
            except Exception as ex:
                fail += 1
                self.sig_log.emit(f"  ✗ {e.filename}: {ex}\n")

            self.sig_progress.emit(
                f"{mode}: {i}/{len(bad)}", i, len(bad)
            )

        self.sig_log.emit(
            f"\nDone: {ok} removed, {fail} errors. "
            f"{len(entries) - len(bad)} remaining.\n"
        )
        self.sig_done.emit(ok)

    # ── CLEANUP (Phase 2) ──────────────────────────────────────────────

    def _run_cleanup(self) -> None:
        jpg_dir = self.cfg.jpg_dir
        raw_dir = self.cfg.raw_dir

        self.sig_log.emit("Scanning JPGs and RAWs …\n")
        orphans = find_orphaned_raws(jpg_dir, raw_dir)
        self.sig_orphans_found.emit(orphans)

        jpg_count = len(list(Path(jpg_dir).iterdir()))
        raw_count = len(list(Path(raw_dir).iterdir()))
        self.sig_log.emit(
            f"JPGs: {jpg_count}  |  RAWs: {raw_count}  |  Orphans: {len(orphans)}\n"
        )

        if not orphans:
            self.sig_log.emit("✓ No orphaned RAWs – all matched.\n")
            self.sig_done.emit(0)
            return

        ok, fail = 0, 0
        for i, r in enumerate(orphans, 1):
            if self._cancel.is_set():
                self.sig_log.emit("Aborted.\n")
                self.sig_cancelled.emit()
                return

            try:
                remove_file(r, "trash")
                ok += 1
                self.sig_log.emit(f"  ✓ {r.name}\n")
            except Exception as ex:
                fail += 1
                self.sig_log.emit(f"  ✗ {r.name}: {ex}\n")

            self.sig_progress.emit(f"Trash: {i}/{len(orphans)}", i, len(orphans))

        remaining = raw_count - ok
        self.sig_log.emit(
            f"\nDone: {ok} RAWs removed, {fail} errors. "
            f"{remaining} RAWs remaining.\n"
        )
        self.sig_done.emit(ok)
