"""
IgorVision – Batch statistics panel
=====================================

Compact summary strip shown after each analysis:

* **status counts** – sharp / blurry / exposure / clipping / few
  features / duplicates / errors
* **score histogram** – 24 bins, colour-coded against the blur
  threshold, with the threshold tick
* **best / worst** image of the batch – click to select it in the table

The panel is a plain widget (no analysis logic) and updates from
``set_results``.
"""
from __future__ import annotations

import os

from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtGui import QColor, QPainter
from PyQt5.QtWidgets import (
    QGridLayout,
    QLabel,
    QVBoxLayout,
    QWidget,
)

from models import ImageQualityMetrics

_N_BINS = 24

# score-badges per bin position (below threshold → red, above → green,
# middle → yellow), drawn as bar colours
_C_RED = QColor(214, 94, 94)
_C_YELLOW = QColor(232, 196, 84)
_C_GREEN = QColor(110, 178, 106)
_C_TEXT = QColor(90, 90, 90)


class _Histogram(QWidget):
    """24-bin score histogram with threshold tick (pure QPainter)."""

    def __init__(self, threshold: float):
        super().__init__()
        self._threshold = threshold
        self._counts: list[float] = [0.0] * _N_BINS
        self.setMinimumHeight(72)
        self.setSizePolicy(1, 0)  # stretch horizontally

    def set_scores(self, scores: list[float]) -> None:
        if not scores:
            self._counts = [0.0] * _N_BINS
            self.update()
            return
        import numpy as np
        counts, _ = np.histogram(scores, bins=_N_BINS, range=(0.0, 1.0))
        self._counts = [float(c) for c in counts]
        self.update()

    def paintEvent(self, _ev) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, False)
        w, h = self.width(), self.height()
        axis_y = h - 14
        plot_h = axis_y - 6
        bar_w = w / _N_BINS
        max_c = max(self._counts) if any(self._counts) else 1.0

        for i, c in enumerate(self._counts):
            if c <= 0:
                continue
            x0 = i * bar_w
            bh = max(2, int(plot_h * c / max_c))
            t = (i + 0.5) / _N_BINS
            if t < self._threshold - 0.15:
                col = _C_RED
            elif t > self._threshold + 0.15:
                col = _C_GREEN
            else:
                col = _C_YELLOW
            p.fillRect(int(x0) + 1, axis_y - bh, max(1, int(bar_w) - 2), bh, col)

        # baseline
        p.setPen(QColor(180, 180, 180))
        p.drawLine(0, axis_y, w, axis_y)

        # threshold tick
        tx = int(self._threshold * w)
        p.setPen(QColor(120, 120, 120))
        p.drawLine(tx, 4, tx, axis_y)

        # labels
        p.setPen(_C_TEXT)
        p.drawText(0, axis_y + 2, 40, 12, Qt.AlignLeft, "0")
        p.drawText(w - 20, axis_y + 2, 20, 12, Qt.AlignRight, "1")
        p.end()


class StatsPanel(QWidget):
    """Batch summary: counts + histogram + best/worst (clickable)."""

    select_requested = pyqtSignal(str)  # image path

    def __init__(self, threshold: float = 0.5, parent: QWidget | None = None):
        super().__init__(parent)
        self._threshold = threshold

        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(4)

        self._counts_label = QLabel("No results yet")
        self._counts_label.setStyleSheet("padding: 2px;")
        layout.addWidget(self._counts_label)

        self._hist = _Histogram(threshold)
        layout.addWidget(self._hist)

        self._bw_row = QLabel("")
        self._bw_row.setOpenExternalLinks(False)
        self._bw_row.setStyleSheet("color: #333;")
        self._bw_row.mousePressEvent = self._on_bw_click  # type: ignore[assignment]
        layout.addWidget(self._bw_row)

        self._best_path = ""
        self._worst_path = ""

    # ------------------------------------------------------------------
    # public API
    # ------------------------------------------------------------------

    def set_results(self, results: list[ImageQualityMetrics]) -> None:
        ok = [r for r in results if not r.is_error]
        errors = len(results) - len(ok)

        clean = (lambda r: not r.is_blurry and r.exposure_ok and r.clipping_ok
                 and not r.low_features and not r.duplicate_of
                 and not r.exposure_outlier and not r.wb_outlier
                 and not r.low_dynamic_range and not r.high_iso
                 and not r.aperture_outlier and not r.soft_motion_blur)
        sharp = sum(1 for r in ok if clean(r))
        blurry = sum(1 for r in ok if r.is_blurry)
        exposure = sum(1 for r in ok if not r.exposure_ok)
        clipping = sum(1 for r in ok if not r.clipping_ok)
        few = sum(1 for r in ok if r.low_features)
        dups = sum(1 for r in ok if r.duplicate_of)
        expo_delta = sum(1 for r in ok if r.exposure_outlier)
        wb_delta = sum(1 for r in ok if r.wb_outlier)
        hi_iso = sum(1 for r in ok if r.high_iso)
        soft = sum(1 for r in ok if r.soft_motion_blur)

        chips = [
            f"Σ {len(ok)}",
            f"🟢 {sharp}",
            f"🔴 {blurry}",
            f"⚠️ exp {exposure}",
            f"clip {clipping}",
            f"few {few}",
            f"dup {dups}",
        ]
        if expo_delta:
            chips.append(f"expoΔ {expo_delta}")
        if wb_delta:
            chips.append(f"wbΔ {wb_delta}")
        if hi_iso:
            chips.append(f"isoΔ {hi_iso}")
        if soft:
            chips.append(f"🟡 soft {soft}")
        if errors:
            chips.append(f"⚫ err {errors}")
        self._counts_label.setText("   ".join(chips))

        self._hist.set_scores([r.composite_score for r in ok])

        if ok:
            best = max(ok, key=lambda r: r.composite_score)
            worst = min(ok, key=lambda r: r.composite_score)
            self._best_path, self._worst_path = best.path, worst.path
            self._bw_row.setText(
                f"best: {os.path.basename(best.path)} ({best.composite_score:.2f})"
                f"      worst: {os.path.basename(worst.path)} ({worst.composite_score:.2f})"
                f"   [click to select]"
            )
        else:
            self._best_path = self._worst_path = ""
            self._bw_row.setText("")

    # ------------------------------------------------------------------
    # internals
    # ------------------------------------------------------------------

    def _on_bw_click(self, _ev) -> None:  # type: ignore[no-untyped-def]
        if self._best_path or self._worst_path:
            # left half → best, right half → worst
            x = _ev.position().x() if hasattr(_ev, "position") else _ev.x()  # type: ignore[union-attr]
            pick = self._best_path if x < self._bw_row.width() / 2 else self._worst_path
            if pick:
                self.select_requested.emit(pick)
