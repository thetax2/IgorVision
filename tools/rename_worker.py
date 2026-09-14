"""
workers/rename_worker.py
========================
QThread worker: scans JPG and RAW folders, matches by base number,
performs rename/copy.

Principle:
  RAW:  image0001.cr3
  JPG:  image0001_35mm_UpperPart.jpg
  Match on "image0001"
  →  image0001.cr3  →  image0001_35mm_UpperPart.cr3
"""

from __future__ import annotations

import os
import re
import shutil
from pathlib import Path

from PyQt5.QtCore import QThread, pyqtSignal

from tools.rename_engine import RenameConfig, RenameMapping


# ──────────────────────────────────────────────────────────────────────
# Basis-ID-Extraktion
# ──────────────────────────────────────────────────────────────────────

_CAMERA_PATTERNS = [
    re.compile(r'^(image\d+)', re.IGNORECASE),
    re.compile(r'^(IMG_\d+)', re.IGNORECASE),
    re.compile(r'^(DSC_\d+)', re.IGNORECASE),
    re.compile(r'^(_MG_\d+)', re.IGNORECASE),
    re.compile(r'^(P\d{4,})', re.IGNORECASE),
    re.compile(r'^(\d{4,})', re.IGNORECASE),
]


def extract_base_name(filename: str) -> str:
    """
    Extracts the base ID from a filename (without extension).
    image0001_35mm_UpperPart → image0001
    image0001.cr3            → image0001
    """
    name = os.path.splitext(filename)[0]
    for pat in _CAMERA_PATTERNS:
        m = pat.match(name)
        if m:
            return m.group(1).upper()
    # Fallback: alles vor dem ersten '_'
    parts = name.split('_')
    if len(parts) >= 2:
        return f"{parts[0]}_{parts[1]}".upper()
    return name.upper()


# ──────────────────────────────────────────────────────────────────────
# Worker
# ──────────────────────────────────────────────────────────────────────

class RenameWorker(QThread):
    sig_log = pyqtSignal(str)
    sig_progress = pyqtSignal(str, int, int)
    sig_analyzed = pyqtSignal(list)    # list[RenameMapping]
    sig_done = pyqtSignal(list)        # list[RenameMapping] nach Execute
    sig_error = pyqtSignal(str)
    sig_cancelled = pyqtSignal()

    def __init__(self, cfg: RenameConfig, parent=None):
        super().__init__(parent)
        self.cfg = cfg
        self._cancel = False

    def request_cancel(self) -> None:
        self._cancel = True

    # ── Lauf ──────────────────────────────────────────────────────────

    def run(self) -> None:
        try:
            if self.cfg.mode == "analyze":
                self._do_analyze()
            else:
                self._do_execute()
        except Exception as e:
            self.sig_error.emit(str(e))

    # ── Analyse ───────────────────────────────────────────────────────

    def _do_analyze(self) -> None:
        cfg = self.cfg
        self.sig_log.emit("── Analysis started ──\n")

        # 1) Collect JPGs: base_id → [filenames without extension]
        jpg_map: dict[str, list[str]] = {}
        total_jpg = 0
        for d in cfg.jpg_dirs:
            if self._cancel:
                self.sig_cancelled.emit()
                return
            self.sig_log.emit(f"Scanning JPG: {d}\n")
            for root, _, files in os.walk(d):
                for f in files:
                    if f.lower().endswith(tuple(cfg.jpg_extensions)):
                        base = extract_base_name(f)
                        jpg_map.setdefault(base, []).append(
                            os.path.splitext(f)[0]
                        )
                        total_jpg += 1
        self.sig_log.emit(f"  → {total_jpg} JPG, "
                          f"{len(jpg_map)} unique IDs\n")

        # 2) Collect RAW: base_id → path
        raw_map: dict[str, Path] = {}
        total_raw = 0
        for d in cfg.raw_dirs:
            if self._cancel:
                self.sig_cancelled.emit()
                return
            self.sig_log.emit(f"Scanning RAW: {d}\n")
            for root, _, files in os.walk(d):
                for f in files:
                    if f.lower().endswith(tuple(cfg.raw_extensions)):
                        base = extract_base_name(f)
                        raw_map[base] = Path(root) / f
                        total_raw += 1
        self.sig_log.emit(f"  → {total_raw} RAW\n")

        # 3) 1:1 Matching
        mappings: list[RenameMapping] = []
        matched = 0
        for base in sorted(jpg_map.keys() & raw_map.keys()):
            jpg_name = jpg_map[base][0]  # erstes (einziges) JPG
            raw_path = raw_map[base]
            raw_ext = raw_path.suffix
            mappings.append(RenameMapping(
                base_name=base,
                raw_filename=raw_path.name,
                raw_path=raw_path,
                new_filename=jpg_name + raw_ext,
            ))
            matched += 1

        # RAW ohne JPG
        unmatched_raw = raw_map.keys() - jpg_map.keys()
        # JPG ohne RAW
        unmatched_jpg = jpg_map.keys() - raw_map.keys()

        self.sig_log.emit(f"── Result ──\n")
        self.sig_log.emit(f"  Matched:   {matched}\n")
        self.sig_log.emit(f"  RAW without JPG:  {len(unmatched_raw)}\n")
        self.sig_log.emit(f"  JPG without RAW:  {len(unmatched_jpg)}\n")

        if unmatched_raw:
            sample = sorted(unmatched_raw)[:5]
            self.sig_log.emit(f"  Sample unmatched RAW: {', '.join(sample)}\n")
        if unmatched_jpg:
            sample = sorted(unmatched_jpg)[:5]
            self.sig_log.emit(f"  Sample unmatched JPG: {', '.join(sample)}\n")

        self.sig_progress.emit(f"Analysis done: {matched} matches",
                               matched, matched)
        self.sig_analyzed.emit(mappings)

    # ── Execute ───────────────────────────────────────────────────────

    def _do_execute(self) -> None:
        cfg = self.cfg
        self.sig_log.emit("── Execution started ──\n")
        if cfg.dry_run:
            self.sig_log.emit("⚠ DRY RUN – no files will be copied\n")

        # Mapping neu aufbauen
        jpg_map: dict[str, list[str]] = {}
        for d in cfg.jpg_dirs:
            for root, _, files in os.walk(d):
                for f in files:
                    if f.lower().endswith(tuple(cfg.jpg_extensions)):
                        base = extract_base_name(f)
                        jpg_map.setdefault(base, []).append(
                            os.path.splitext(f)[0]
                        )

        raw_map: dict[str, Path] = {}
        for d in cfg.raw_dirs:
            for root, _, files in os.walk(d):
                for f in files:
                    if f.lower().endswith(tuple(cfg.raw_extensions)):
                        base = extract_base_name(f)
                        raw_map[base] = Path(root) / f

        mappings: list[RenameMapping] = []
        for base in sorted(jpg_map.keys() & raw_map.keys()):
            jpg_name = jpg_map[base][0]
            raw_path = raw_map[base]
            raw_ext = raw_path.suffix
            mappings.append(RenameMapping(
                base_name=base,
                raw_filename=raw_path.name,
                raw_path=raw_path,
                new_filename=jpg_name + raw_ext,
            ))

        if not mappings:
            self.sig_error.emit("No mappings found.")
            return

        total = len(mappings)
        os.makedirs(cfg.target_dir, exist_ok=True)

        done = 0
        errors = 0
        skipped = 0

        for m in mappings:
            if self._cancel:
                self.sig_cancelled.emit()
                return

            target = cfg.target_dir / m.new_filename

            if target.exists() and not cfg.overwrite:
                m.status = "skipped"
                skipped += 1
                self.sig_log.emit(f"  ⊘ {m.new_filename} (exists)\n")
            elif cfg.dry_run:
                m.status = "done"
                self.sig_log.emit(f"  ▶ {m.raw_filename} → {m.new_filename}\n")
            else:
                try:
                    shutil.copy2(str(m.raw_path), str(target))
                    m.status = "done"
                    self.sig_log.emit(f"  ✓ {m.raw_filename} → {m.new_filename}\n")
                except Exception as e:
                    m.status = "error"
                    errors += 1
                    self.sig_log.emit(f"  ✗ {m.new_filename}: {e}\n")

            done += 1
            self.sig_progress.emit(
                f"{done}/{total}  ({m.raw_filename} → {m.new_filename})",
                done, total,
            )

        self.sig_log.emit(f"── Done: {done} processed, "
                          f"{skipped} skipped, {errors} errors ──\n")
        self.sig_done.emit(mappings)
