"""
IgorVision – Results table
==========================
"""
from __future__ import annotations

import logging
import os

from PyQt5.QtCore import Qt
from PyQt5.QtGui import QColor, QPixmap
from PyQt5.QtWidgets import (
    QAbstractItemView,
    QHeaderView,
    QTableWidget,
    QTableWidgetItem,
)

from models import ImageQualityMetrics, status_text

logger = logging.getLogger(__name__)

HEADERS = [
    "Preview",
    "Filename",
    "Quality",
    "Status",
    "Size",
    "Dimensions",
]

# Colour constants for score coding (light pastel fills)
_BG_GOOD = QColor(200, 255, 200)
_BG_WARN = QColor(255, 255, 200)
_BG_BAD = QColor(255, 200, 200)
# Dark text used on the light pastel fills so it stays readable in both the
# dark and the light theme (the white dark-theme text is unreadable on them).
_FG_ON_LIGHT = QColor(28, 31, 36)

# Role that carries the image path on every row (used to resolve results
# independent of the table's visual sort order).
PATH_ROLE = Qt.UserRole + 1


class QualityTableModel:
    """Manages the results ``QTableWidget``."""

    def __init__(self, table: QTableWidget):
        self._table = table
        self._results: list[ImageQualityMetrics] = []
        self._setup()

    # ------------------------------------------------------------------
    # public API
    # ------------------------------------------------------------------

    @property
    def results(self) -> list[ImageQualityMetrics]:
        return self._results

    def set_results(self, results: list[ImageQualityMetrics]) -> None:
        self._results = list(results)
        self._refresh()

    def add_result(self, result: ImageQualityMetrics) -> None:
        """Append a single result (used during streaming analysis)."""
        self._results.append(result)
        if len(self._results) % 10 == 0:
            self._refresh()

    def path_at_row(self, row: int) -> str | None:
        """Path stored on the (visual) table row *row*, or None."""
        item = self._table.item(row, 1)
        if item is None:
            return None
        return item.data(PATH_ROLE)

    # ------------------------------------------------------------------
    # internals
    # ------------------------------------------------------------------

    def _setup(self) -> None:
        t = self._table
        t.setColumnCount(len(HEADERS))
        t.setHorizontalHeaderLabels(HEADERS)
        t.setSelectionBehavior(QAbstractItemView.SelectRows)
        t.setEditTriggers(QAbstractItemView.NoEditTriggers)
        t.setAlternatingRowColors(True)
        t.verticalHeader().setVisible(False)

        hdr = t.horizontalHeader()
        hdr.setSectionResizeMode(0, QHeaderView.Fixed)
        hdr.setSectionResizeMode(1, QHeaderView.Stretch)
        for col in (2, 3, 4, 5):
            hdr.setSectionResizeMode(col, QHeaderView.ResizeToContents)
        t.setColumnWidth(0, 100)

    def _refresh(self) -> None:
        t = self._table
        t.setRowCount(len(self._results))
        for row, r in enumerate(self._results):
            self._populate(row, r)
        if t.rowCount() > 0:
            t.sortItems(2, Qt.DescendingOrder)

    def _populate(self, row: int, r: ImageQualityMetrics) -> None:
        t = self._table

        # --- thumbnail ---
        item = QTableWidgetItem()
        if r.thumbnail_data:
            pm = QPixmap()
            if pm.loadFromData(r.thumbnail_data):
                item.setData(
                    Qt.DecorationRole,
                    pm.scaled(80, 80, Qt.KeepAspectRatio, Qt.SmoothTransformation),
                )
        t.setItem(row, 0, item)

        # --- filename (carries the full path for row-independent lookup) ---
        name_item = QTableWidgetItem(os.path.basename(r.path))
        name_item.setToolTip(r.path)
        name_item.setData(PATH_ROLE, r.path)
        t.setItem(row, 1, name_item)

        # --- quality score (colour-coded) ---
        q_item = QTableWidgetItem(f"{r.composite_score:.3f}")
        q_item.setData(Qt.UserRole, r.composite_score)
        if r.composite_score > 0.7:
            q_item.setBackground(_BG_GOOD)
        elif r.composite_score > 0.4:
            q_item.setBackground(_BG_WARN)
        else:
            q_item.setBackground(_BG_BAD)
        q_item.setForeground(_FG_ON_LIGHT)
        t.setItem(row, 2, q_item)

        # --- status (blur + exposure + photogrammetry checks) ---
        s_item = QTableWidgetItem(status_text(r))
        base_tip = (
            f"score {r.composite_score:.3f} · sharp {r.peak_sharpness:.0f} · "
            f"tenengrad {r.tenengrad:.0f} · grad_p95 {r.gradient_p95:.1f} · "
            f"sobel_aniso {r.sobel_aniso:.3f} · "
            f"features {r.feature_density:.1f}/kpx (uni {r.feature_uniformity:.2f}) · "
            f"vign {r.vignetting:.2f} · "
            f"clip hi {r.clip_high * 100:.1f}% · clip lo {r.clip_low * 100:.1f}% · "
            f"≈ {r.wb_kelvin:.0f} K · Δexpo {r.expo_dev:+.2f} · ΔWB {r.wb_dev:.3f} · "
            f"MB {r.mb_penalty * 100:.0f}%"
            + (f" · overlap ?" if r.overlap_next >= 0 and int(r.overlap_matches * r.overlap_inlier_ratio) < 8 else
               f" · overlap {r.overlap_next * 100:.0f}%" if r.overlap_next >= 0 else "")
        )
        s_item.setToolTip(f"{base_tip} · {r.load_note}" if r.load_note else base_tip)
        if r.is_error or r.is_blurry:
            s_item.setBackground(_BG_BAD)
            s_item.setForeground(_FG_ON_LIGHT)
        elif (not r.exposure_ok or not r.clipping_ok or r.low_features or r.duplicate_of
              or r.exposure_outlier or r.wb_outlier or r.low_dynamic_range
              or r.high_iso or r.aperture_outlier or r.soft_motion_blur
              or r.uneven_features or r.has_vignetting):
            s_item.setBackground(_BG_WARN)
            s_item.setForeground(_FG_ON_LIGHT)
        t.setItem(row, 3, s_item)

        # --- file size ---
        if r.file_size > 0:
            t.setItem(row, 4, QTableWidgetItem(f"{r.file_size / (1024 * 1024):.1f} MB"))
        else:
            t.setItem(row, 4, QTableWidgetItem("N/A"))

        # --- dimensions ---
        w, h = r.dimensions
        t.setItem(row, 5, QTableWidgetItem(f"{w}×{h}"))
