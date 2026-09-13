"""
IgorVision – Full-size preview dialog
=====================================
"""
from __future__ import annotations

import os

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import QDialog, QLabel, QVBoxLayout

from models import ImageQualityMetrics

from .image_viewer import ImageViewer


class ImagePreviewDialog(QDialog):
    """Modal dialog showing a full-resolution image with its metrics."""

    def __init__(
        self,
        image_path: str,
        result: ImageQualityMetrics,
        parent=None,
    ):
        super().__init__(parent)
        self._result = result
        self.setWindowTitle(f"Preview: {os.path.basename(image_path)}")
        self.setModal(True)
        self.resize(1200, 900)
        self._setup_ui(image_path)

    # ------------------------------------------------------------------

    def _setup_ui(self, image_path: str) -> None:
        layout = QVBoxLayout(self)

        # --- image viewer ---
        self._viewer = ImageViewer()
        layout.addWidget(self._viewer)

        # --- info bar ---
        r = self._result
        if r.is_blurry and not r.exposure_ok:
            status = "🔴 Blurry + Exposure"
        elif r.is_blurry:
            status = "🔴 Blurry"
        elif not r.exposure_ok:
            status = "🟡 Exposure"
        else:
            status = "🟢 Sharp"
        info = (
            f"<b>Score:</b> {r.composite_score:.3f}   |   "
            f"<b>Status:</b> {status}   |   "
            f"<b>Size:</b> {r.file_size / (1024 * 1024):.1f} MB   |   "
            f"<b>Dimensions:</b> {r.dimensions[0]}×{r.dimensions[1]}   |   "
            f"<b>Noise:</b> {r.noise_level:.1f}   |   "
            f"<b>Contrast:</b> {r.contrast:.1f}   |   "
            f"<b>Sharpness:</b> {r.peak_sharpness:.1f}   |   "
            f"<b>Tenengrad:</b> {r.tenengrad:.0f}   |   "
            f"<b>Grad p95:</b> {r.gradient_p95:.1f}   |   "
            f"<b>Sobel Aniso:</b> {r.sobel_aniso:.3f}   |   "
            f"<b>Features:</b> {r.feature_density:.1f}/kpx (uni {r.feature_uniformity:.2f})   |   "
            f"<b>Vignetting:</b> {r.vignetting:.2f}   |   "
            f"<b>MB Δ:</b> {r.mb_delta_conc * 100:.1f}%   |   "
            f"<b>MB Penalty:</b> {r.mb_penalty * 100:.0f}%"
            + (f"   |   <b>Overlap:</b> ? (unreliable)" if r.overlap_next >= 0 and int(r.overlap_matches * r.overlap_inlier_ratio) < 8 else
               f"   |   <b>Overlap:</b> {r.overlap_next * 100:.1f} %" if r.overlap_next >= 0 else "")
        )
        label = QLabel(info)
        label.setStyleSheet(
            "background-color: #f0f0f0; padding: 8px; border-radius: 4px;"
        )
        layout.addWidget(label)

        # --- load image ---
        if not self._viewer.set_image_from_path(image_path):
            err = QLabel("Failed to load image")
            err.setAlignment(Qt.AlignCenter)
            layout.addWidget(err)
