"""
tools/realityscan_worker.py
===========================
Process runner for the RealityScan command centre.

Instead of shelling out to a ``.bat`` file, the pipeline is executed
**directly** against the RealityScan executable via ``QProcess``:

* live ``stdout`` / ``stderr`` → :signal:`log` (shown in the log panel)
* optional **progress file** polling (``writeProgress``) → :signal:`progress`
* clean **abort** (kill the process tree)
* **exit code** → :signal:`finished`

The pipeline itself stays structured JSON (see
:mod:`tools.realityscan_engine`); a ``.bat`` is only ever an *export
artifact*, never the execution path.
"""
from __future__ import annotations

import os
import re
import time

from PyQt5.QtCore import QObject, QProcess, QThread, QTimer, pyqtSignal

from tools.realityscan_engine import RSPipeline, build_args, ordered_nodes


class RealityScanRunner(QObject):
    """Runs one pipeline (a single RealityScan invocation)."""

    log = pyqtSignal(str)                 # a line of stdout/stderr
    progress = pyqtSignal(int, str)       # (percent 0-100, label)
    state_changed = pyqtSignal(str)       # running | finished | aborted | error
    finished = pyqtSignal(int)            # exit code (0 = ok)

    def __init__(self, parent: QObject | None = None):
        super().__init__(parent)
        self._proc: QProcess | None = None
        self._progress_file: str = ""
        self._progress_timer: QTimer | None = None
        self._aborted = False
        self._last_progress = -1

    # ------------------------------------------------------------------
    @property
    def running(self) -> bool:
        return self._proc is not None and self._proc.state() != QProcess.NotRunning

    def run(self, pipeline: RSPipeline) -> None:
        if self.running:
            self.log("A pipeline is already running – abort it first.")
            return

        exe = pipeline.exe.strip()
        if not exe or not os.path.isfile(exe):
            self.state_changed.emit("error")
            self.log(f"ERROR: RealityScan executable not found:\n  {exe}")
            self.finished.emit(-1)
            return

        args = build_args(pipeline)
        self._aborted = False
        self._last_progress = -1

        # working directory: the exe's folder (RealityScan expects its DLLs there)
        workdir = os.path.dirname(exe) or "."

        self._proc = QProcess(self)
        self._proc.setWorkingDirectory(workdir)
        self._proc.readyReadStandardOutput.connect(self._read_stdout)
        self._proc.readyReadStandardError.connect(self._read_stderr)
        self._proc.errorOccurred.connect(self._on_proc_error)
        self._proc.finished.connect(self._on_finished)

        self.log("=" * 62)
        self.log(f"Pipeline : {pipeline.name}")
        self.log(f"Exe      : {exe}")
        self.log(f"Args     : {' '.join(args)}")
        self.log("-" * 62)

        self._proc.start(exe, args)
        if not self._proc.waitForStarted(5000):
            self.state_changed.emit("error")
            self.log("ERROR: process failed to start.")
            self.finished.emit(-1)
            return

        self.state_changed.emit("running")
        self._start_progress_polling(pipeline)

    # ------------------------------------------------------------------
    def abort(self) -> None:
        self._aborted = True
        if self._proc is not None and self._proc.state() != QProcess.NotRunning:
            self._proc.kill()
            self.log("ABORT requested – killing process…")

    # ------------------------------------------------------------------
    # internals
    # ------------------------------------------------------------------
    def _read_stdout(self) -> None:
        if self._proc is None:
            return
        data = bytes(self._proc.readAllStandardOutput()).decode("utf-8", "replace")
        for line in data.splitlines():
            if line.strip():
                self.log(line)

    def _read_stderr(self) -> None:
        if self._proc is None:
            return
        data = bytes(self._proc.readAllStandardError()).decode("utf-8", "replace")
        for line in data.splitlines():
            if line.strip():
                self.log("[stderr] " + line)

    def _on_proc_error(self, err) -> None:  # type: ignore[no-untyped-def]
        names = {
            QProcess.FailedToStart: "FailedToStart",
            QProcess.Crashed: "Crashed",
            QProcess.Timedout: "Timedout",
            QProcess.ReadError: "ReadError",
            QProcess.WriteError: "WriteError",
        }
        self.log(f"PROCESS ERROR: {names.get(err, err)}")

    def _on_finished(self, exit_code: int, exit_status) -> None:  # type: ignore[no-untyped-def]
        self._stop_progress_polling()
        if self._aborted:
            self.state_changed.emit("aborted")
            self.log("Pipeline ABORTED by user.")
        else:
            ok = exit_status == QProcess.NormalExit and exit_code == 0
            self.state_changed.emit("finished")
            self.log("-" * 62)
            self.log(f"Pipeline finished – exit code {exit_code} "
                     f"({'OK' if ok else 'ERROR'}).")
        self.finished.emit(exit_code)

    # ── progress file polling (writeProgress) ────────────────────────
    def _find_progress_file(self, pipeline: RSPipeline) -> str:
        for node in ordered_nodes(pipeline):
            if node.command == "writeProgress":
                for v in node.params.values():
                    if v:
                        return str(v)
        return ""

    def _start_progress_polling(self, pipeline: RSPipeline) -> None:
        self._progress_file = self._find_progress_file(pipeline)
        if not self._progress_file:
            return
        self._progress_timer = QTimer(self)
        self._progress_timer.timeout.connect(self._poll_progress)
        self._progress_timer.start(1000)

    def _stop_progress_polling(self) -> None:
        if self._progress_timer is not None:
            self._progress_timer.stop()
            self._progress_timer = None

    def _poll_progress(self) -> None:
        path = self._progress_file
        if not path or not os.path.isfile(path):
            return
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                content = f.read()
        except Exception:
            return
        # look for the last "NN%" or "NN %" occurrence
        percents = re.findall(r"(\d{1,3})\s*%", content)
        if not percents:
            return
        pct = int(percents[-1])
        if 0 <= pct <= 100 and pct != self._last_progress:
            self._last_progress = pct
            self.progress.emit(pct, f"{pct} %")
