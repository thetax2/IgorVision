"""
workers/compare_worker.py
=========================
QThread worker for the comparison (inventory report).
Wraps core.comparison.run_comparison and communicates via Qt signals.
"""

from __future__ import annotations

import threading
from pathlib import Path

from PyQt5.QtCore import QThread, pyqtSignal

from tools.comparison import (
    CompareConfig,
    CancelledError,
    DiffResult,
    check_exiftool,
    plan_destination,
    run_comparison,
    transfer_results,
)


class CompareWorker(QThread):
    """
    This worker reads from disk and ExifTool and builds the final
    inventory report, which is returned to the GUI as an in-memory object.
    """
    sig_log = pyqtSignal(str)
    sig_progress = pyqtSignal(str, int, int)
    sig_done = pyqtSignal(list)
    sig_error = pyqtSignal(str)
    sig_cancelled = pyqtSignal()

    def __init__(self, cfg: CompareConfig, parent=None):
        super().__init__(parent)
        self._cfg = cfg
        self._cancel = threading.Event()

    def request_cancel(self) -> None:
        self._cancel.set()

    @property
    def is_cancelling(self) -> bool:
        return self._cancel.is_set()

    def run(self) -> None:
        try:
            results = run_comparison(
                self._cfg,
                log=self.sig_log.emit,
                progress=lambda phase, cur, tot: self.sig_progress.emit(phase, cur, tot),
                cancel_flag=self._cancel,
            )
            self.sig_done.emit(results)
        except CancelledError:
            self.sig_cancelled.emit()
        except Exception as exc:
            self.sig_error.emit(str(exc))


class ReplanWorker(QThread):
    """
    "Refresh": re-plans the destination paths of the existing result list.

    Reads destination paths via `stat`/`exists` – this previously ran
    synchronously in the GUI thread and froze the GUI. Now in the
    background, with progress and cancellation. The DiffResult objects
    are updated in-place (dest_path/status); copied/moved/skipped
    entries remain unchanged.
    """
    sig_log = pyqtSignal(str)
    sig_progress = pyqtSignal(str, int, int)
    sig_done = pyqtSignal(list, int)  # (results, changed_count)
    sig_error = pyqtSignal(str)
    sig_cancelled = pyqtSignal()

    def __init__(self, results: list[DiffResult], cfg: CompareConfig, parent=None):
        super().__init__(parent)
        self._results = results
        self._cfg = cfg
        self._cancel = threading.Event()

    def request_cancel(self) -> None:
        self._cancel.set()

    @property
    def is_cancelling(self) -> bool:
        return self._cancel.is_set()

    def run(self) -> None:
        try:
            cfg = self._cfg
            total = len(self._results)
            changed = 0
            for i, r in enumerate(self._results, 1):
                if self._cancel.is_set():
                    raise CancelledError()
                if r.status not in ("missing", "present"):
                    continue
                dest, already = plan_destination(cfg, r.entry, r.meta)
                new_status = "present" if already else "missing"
                if dest != r.dest_path or new_status != r.status:
                    changed += 1
                r.dest_path = dest
                r.status = new_status
                if i % 25 == 0 or i == total:
                    self.sig_progress.emit("Refreshing", i, total)
            self.sig_done.emit(self._results, changed)
        except CancelledError:
            self.sig_cancelled.emit()
        except Exception as exc:
            self.sig_error.emit(str(exc))


class ExifToolCheckWorker(QThread):
    """
    Checks ExifTool availability in the background (`-ver` call),
    so that typing in the ExifTool field does not block the GUI.
    """
    sig_done = pyqtSignal(bool)

    def __init__(self, binary: str, parent=None):
        super().__init__(parent)
        self._binary = binary

    def run(self) -> None:
        try:
            ok = bool(self._binary) and check_exiftool(self._binary)
        except Exception:
            ok = False
        self.sig_done.emit(ok)


class TransferWorker(QThread):
    """
    Takes the finished inventory report from RAM and performs the actual
    I/O operations (Copy/Move and hash fallback) in the background.
    """
    sig_log = pyqtSignal(str)
    sig_progress = pyqtSignal(str, int, int)
    sig_done = pyqtSignal(list)
    sig_error = pyqtSignal(str)
    sig_cancelled = pyqtSignal()

    def __init__(self, results: list[DiffResult], cfg: CompareConfig,
                 mode: str, parent=None):
        super().__init__(parent)
        self._results = results
        self._cfg = cfg
        self._mode = mode
        self._cancel = threading.Event()

    def request_cancel(self) -> None:
        self._cancel.set()

    @property
    def is_cancelling(self) -> bool:
        return self._cancel.is_set()

    def run(self) -> None:
        try:
            results = transfer_results(
                self._results,
                self._cfg,
                self._mode,
                log=self.sig_log.emit,
                progress=lambda phase, cur, tot: self.sig_progress.emit(phase, cur, tot),
                cancel_flag=self._cancel,
            )
            self.sig_done.emit(results)
        except CancelledError:
            self.sig_cancelled.emit()
        except Exception as exc:
            self.sig_error.emit(str(exc))