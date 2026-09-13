"""
IgorVision – Zoom / pan image viewer
====================================

Full-resolution preview with:

* mouse-wheel zoom (anchored under the cursor)
* scroll-hand pan
* **button zoom** – in / out (anchored at the view centre)
* **absolute zoom** – 25 % / 50 % / 100 % / 200 % / 300 %
  (100 % = 1 image pixel per 1 screen pixel)
* **re-centre** the image after panning
* **fit to view** (overview)

``set_image_from_path`` loads the *full* resolution file (with a soft
cap for very large files) so zooming stays sharp – not the small table
thumbnail.
"""
from __future__ import annotations

import logging

import cv2
import numpy as np
from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtGui import QBrush, QColor, QImage, QPainter, QPixmap, QWheelEvent
from PyQt5.QtWidgets import QGraphicsScene, QGraphicsView

logger = logging.getLogger(__name__)

# Absolute zoom limits, as a multiple of native (1.0 == 100 %).
MIN_ZOOM = 0.05   # 5 %
MAX_ZOOM = 5.0    # 500 %
_ZOOM_STEP = 1.25


class ImageViewer(QGraphicsView):
    """Image viewer with wheel zoom, pan, and absolute-zoom controls."""

    zoom_changed = pyqtSignal(int)  # current zoom as percent (100 == 1:1)

    def __init__(self):
        super().__init__()
        self._scene = QGraphicsScene(self)
        self.setScene(self._scene)
        self._item = None

        self.setRenderHint(QPainter.Antialiasing)
        self.setRenderHint(QPainter.SmoothPixmapTransform)
        self.setDragMode(QGraphicsView.ScrollHandDrag)
        self.setTransformationAnchor(QGraphicsView.AnchorUnderMouse)
        self.setResizeAnchor(QGraphicsView.AnchorViewCenter)
        self.setBackgroundBrush(QBrush(QColor(240, 240, 240)))
        self.setMouseTracking(True)

    # ------------------------------------------------------------------
    # public API – loading
    # ------------------------------------------------------------------

    def set_image_from_path(self, path: str, max_dim: int = 8192) -> bool:
        """Load and display an image from *path* at (near-)full resolution.

        Files larger than *max_dim* on the longest side are decoded at a
        reduced resolution to keep memory/decode time sane; anything up to
        the cap keeps full detail for zooming.
        """
        try:
            data = np.fromfile(path, dtype=np.uint8)
            if data.size == 0:
                return False

            flag = cv2.IMREAD_COLOR
            try:
                from PIL import Image
                with Image.open(path) as im:
                    longest = max(im.size)
                if longest > max_dim:
                    if longest > 4 * max_dim:
                        flag = cv2.IMREAD_REDUCED_COLOR_4
                    elif longest > 2 * max_dim:
                        flag = cv2.IMREAD_REDUCED_COLOR_2
            except Exception:
                pass

            bgr = cv2.imdecode(data, flag)
            if bgr is None:
                return False
            rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
            h, w, ch = rgb.shape
            img = QImage(rgb.data, w, h, ch * w, QImage.Format_RGB888)
            pixmap = QPixmap.fromImage(img)
            self._show(pixmap)
            return True
        except Exception:
            logger.exception("Failed to load image: %s", path)
            return False

    def set_image_from_data(self, data: bytes) -> bool:
        """Load and display an image from PNG / JPEG bytes (fallback path)."""
        try:
            pixmap = QPixmap()
            if not pixmap.loadFromData(data):
                return False
            self._show(pixmap)
            return True
        except Exception:
            logger.exception("Failed to load image from data")
            return False

    def show_keypoints(self, path: str, keypoints: np.ndarray,
                       analysis_dim: int = 2048, max_dim: int = 4096) -> bool:
        """
        Load an image and overlay SIFT keypoints as yellow dots.

        The keypoints are in **analysis resolution** (longest side = analysis_dim).
        They are scaled up to the displayed image size.

        Parameters
        ----------
        path: image file path
        keypoints: Nx2 array of (x, y) in analysis-resolution pixels
        analysis_dim: the longest-side dimension used during analysis (default 2048)
        max_dim: max display size (longest side)
        """
        try:
            data = np.fromfile(path, dtype=np.uint8)
            if data.size == 0:
                return False
            bgr = cv2.imdecode(data, cv2.IMREAD_COLOR)
            if bgr is None:
                return False

            h, w = bgr.shape[:2]
            orig_longest = max(h, w)

            # Optionally resize image for display
            disp_scale = 1.0
            if orig_longest > max_dim:
                disp_scale = max_dim / orig_longest
                bgr = cv2.resize(bgr, (int(w * disp_scale), int(h * disp_scale)),
                                 interpolation=cv2.INTER_AREA)
                h, w = bgr.shape[:2]

            # Key scale: map from analysis-resolution coords to displayed coords.
            #   analysis: longest side = analysis_dim
            #   displayed: longest side = min(orig_longest, max_dim)
            kpt_scale = (orig_longest * disp_scale) / analysis_dim

            # Debug log to help diagnose scaling issues
            logger.info(
                "show_keypoints: img=%dx%d orig=%d scale=%.3f kpts=%d kpt_scale=%.3f",
                w, h, orig_longest, disp_scale, len(keypoints), kpt_scale,
            )

            # Larger, high-contrast dots: red fill + white border for visibility
            radius = max(6, int(0.005 * min(h, w)))  # ~13px on 2700px-wide display
            for x, y in keypoints:
                px = int(x * kpt_scale)
                py = int(y * kpt_scale)
                if 0 <= px < w and 0 <= py < h:
                    # White outer ring + red center for high contrast
                    cv2.circle(bgr, (px, py), radius, (255, 255, 255), 2, cv2.LINE_AA)
                    cv2.circle(bgr, (px, py), max(2, radius - 3), (0, 0, 255), -1, cv2.LINE_AA)

            rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
            h, w, ch = rgb.shape
            img = QImage(rgb.data, w, h, ch * w, QImage.Format_RGB888)
            pixmap = QPixmap.fromImage(img)
            self._show(pixmap)
            return True
        except Exception:
            logger.exception("show_keypoints failed: %s", path)
            return False

    def clear(self) -> None:
        """Remove the current image."""
        self._scene.clear()
        self._item = None
        self.resetTransform()
        self.viewport().update()

    # ------------------------------------------------------------------
    # public API – zoom & framing
    # ------------------------------------------------------------------

    def reset_view(self) -> None:
        """Fit the whole image into the view (overview)."""
        if self._item is None:
            return
        self.fitInView(self._item, Qt.KeepAspectRatio)
        self._emit_zoom()

    def center_image(self) -> None:
        """Re-centre the image at its current zoom level."""
        if self._item is None:
            return
        self.centerOn(self._item)

    def zoom_in(self) -> None:
        """Zoom in one step, anchored at the view centre."""
        self._zoom_relative(_ZOOM_STEP, under_mouse=False)

    def zoom_out(self) -> None:
        """Zoom out one step, anchored at the view centre."""
        self._zoom_relative(1.0 / _ZOOM_STEP, under_mouse=False)

    def zoom_to(self, percent: float) -> None:
        """Set an absolute zoom level (percent; 100 == 1:1) and centre."""
        if self._item is None:
            return
        scale = max(MIN_ZOOM, min(MAX_ZOOM, float(percent) / 100.0))
        self.resetTransform()
        self.scale(scale, scale)
        self.centerOn(self._item)
        self._emit_zoom()

    def current_zoom_percent(self) -> int:
        if self._item is None:
            return 100
        return int(round(self.transform().m11() * 100.0))

    # ------------------------------------------------------------------
    # internals
    # ------------------------------------------------------------------

    def _show(self, pixmap: QPixmap) -> None:
        self.setUpdatesEnabled(False)
        try:
            if self._item is None:
                self._item = self._scene.addPixmap(pixmap)
            else:
                self._item.setPixmap(pixmap)
            self.reset_view()
        finally:
            self.setUpdatesEnabled(True)

    def _zoom_relative(self, factor: float, under_mouse: bool = False) -> None:
        """Multiply the current scale by *factor*, clamped to the limits."""
        if self._item is None:
            return
        cur = self.transform().m11()
        new = max(MIN_ZOOM, min(MAX_ZOOM, cur * factor))
        if new == cur:
            return
        self.setTransformationAnchor(
            QGraphicsView.AnchorUnderMouse if under_mouse else QGraphicsView.AnchorViewCenter
        )
        self.scale(new / cur, new / cur)
        self._emit_zoom()

    def _emit_zoom(self) -> None:
        self.zoom_changed.emit(self.current_zoom_percent())

    def wheelEvent(self, event: QWheelEvent) -> None:  # type: ignore[override]
        """Mouse-wheel zoom (anchored under cursor)."""
        if self._item is None:
            return
        factor = _ZOOM_STEP if event.angleDelta().y() > 0 else 1.0 / _ZOOM_STEP
        self._zoom_relative(factor, under_mouse=True)
