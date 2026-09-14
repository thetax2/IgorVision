"""
IgorVision – Main application window
====================================
"""
from __future__ import annotations

import logging
import multiprocessing as mp
import os
import sys
import threading

from PyQt5.QtCore import QObject, Qt, QSettings, QThread, pyqtSignal
from PyQt5.QtGui import QColor

try:  # PyQt5 < 5.12 style
    from PyQt5.QtGui import QAction
except ImportError:  # PyQt6-style build
    from PyQt5.QtWidgets import QAction
from PyQt5.QtWidgets import (
    QApplication,
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QGridLayout,
    QHeaderView,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QSlider,
    QSplitter,
    QStackedWidget,
    QTabBar,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from config import DEFAULT_CONFIG
from models import ImageQualityMetrics, status_text
from utils import collect_image_paths
from workers import run_analysis

from .image_viewer import ImageViewer
from .table_model import QualityTableModel
from .preview_dialog import ImagePreviewDialog
from .settings_panel import SettingsPanel
from .stats_panel import StatsPanel
from .compare_tab import CompareTab
from .rename_tab import RenameTab
from .sort_tab import SortTab
from .metashape_tab import MetashapeTab

logger = logging.getLogger(__name__)


def _as_bool(value, default: bool) -> bool:
    """
    Robust bool from a QSettings value.

    On Windows QSettings may hand back booleans as the strings
    ``'true'``/``'false'`` (registry storage), and ``bool('false')``
    would wrongly be ``True``.
    """
    if value is None:
        return default
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "on")
    return bool(value)


def build_export_rows(results: list[ImageQualityMetrics]) -> list[dict]:
    """
    Flat, export-ready rows (one dict per image) with all quality,
    photogrammetry and EXIF fields.  Shared by the CSV export and the
    future CLI/JSON output – kept free of Qt so it can be tested
    headless.
    """
    data = []
    for r in results:
        e = r.exif or {}
        data.append({
            "filename": os.path.basename(r.path),
            "path": r.path,
            "status": status_text(r),
            "quality_score": round(r.composite_score, 4),
            "is_blurry": r.is_blurry,
            "exposure_ok": r.exposure_ok,
            "clipping_ok": r.clipping_ok,
            "clip_high_pct": round(r.clip_high * 100, 3),
            "clip_low_pct": round(r.clip_low * 100, 3),
            "feature_density": round(r.feature_density, 3),
            "low_features": r.low_features,
            "feature_uniformity": round(r.feature_uniformity, 4),
            "uneven_features": r.uneven_features,
            "vignetting": round(r.vignetting, 4),
            "has_vignetting": r.has_vignetting,
            "slow_shutter": r.slow_shutter,
            "duplicate_of": r.duplicate_of,
            "ev": round(r.ev, 3),
            "expo_dev": round(r.expo_dev, 3),
            "exposure_outlier": r.exposure_outlier,
            "wb_kelvin": round(r.wb_kelvin, 0),
            "wb_gain_rg": round(r.wb_gain_rg, 3),
            "wb_gain_bg": round(r.wb_gain_bg, 3),
            "wb_dev": round(r.wb_dev, 4),
            "wb_outlier": r.wb_outlier,
            "luma_median": round(r.luma_median, 1),
            "dynamic_range": round(r.dynamic_range, 1),
            "low_dynamic_range": r.low_dynamic_range,
            "iso_dev_stops": round(r.iso_dev_stops, 3),
            "high_iso": r.high_iso,
            "aperture_dev_stops": round(r.aperture_dev_stops, 3),
            "aperture_outlier": r.aperture_outlier,
            "overlap_next": round(r.overlap_next, 4) if r.overlap_next >= 0 else -1,
            "overlap_matches": r.overlap_matches,
            "overlap_inlier_ratio": round(r.overlap_inlier_ratio, 4),
            "is_error": r.is_error,
            "load_backend": r.load_backend,
            "load_note": r.load_note,
            "peak_sharpness": round(r.peak_sharpness, 2),
            "sharpness_spread": round(r.sharpness_spread, 4),
            "mb_delta_conc": round(r.mb_delta_conc, 4),
            "mb_lag_aniso": round(r.mb_lag_aniso, 4),
            "mb_penalty": round(r.mb_penalty, 4),
            "soft_motion_blur": r.soft_motion_blur,
            "laplacian_variance": round(r.laplacian_variance, 2),
            "sobel_variance": round(r.sobel_variance, 2),
            "sobel_var_x": round(r.sobel_var_x, 2),
            "sobel_var_y": round(r.sobel_var_y, 2),
            "sobel_aniso": round(r.sobel_aniso, 4),
            "tenengrad": round(r.tenengrad, 2),
            "gradient_p95": round(r.gradient_p95, 2),
            "edge_density": round(r.edge_density, 4),
            "noise_level": round(r.noise_level, 2),
            "contrast": round(r.contrast, 2),
            "brightness": round(r.brightness, 2),
            "saturation": round(r.saturation, 2),
            "exif_make": e.get("make", ""),
            "exif_model": e.get("model", ""),
            "exif_aperture": e.get("aperture", ""),
            "exif_shutter": e.get("shutter", ""),
            "exif_iso": e.get("iso", ""),
            "exif_focal_length": e.get("focal_length", ""),
            "exif_datetime": e.get("datetime", ""),
            "exif_gps": ", ".join(str(v) for v in e["gps"]) if e.get("gps") else "",
            "file_size_mb": round(r.file_size / (1024 * 1024), 2),
            "width": r.dimensions[0],
            "height": r.dimensions[1],
        })
    return data


# ---------------------------------------------------------------------------
# background analysis worker (QObject for signal/slot)
# ---------------------------------------------------------------------------

class _AnalysisWorker(QObject):
    """Runs the batch analysis in a background ``QThread``.

    Two phases:
      1. Quality metrics (parallel) → ``progress`` signal
      2. Overlap check  (sequential) → ``overlap_progress`` signal
    """

    progress = pyqtSignal(int, int, str)
    overlap_progress = pyqtSignal(int, int, str)
    overlap_started = pyqtSignal()
    finished = pyqtSignal(list)
    error = pyqtSignal(str)

    def __init__(
        self,
        paths: list[str],
        num_workers: int,
        stop_event: threading.Event,
        do_overlap: bool = True,
    ):
        super().__init__()
        self._paths = paths
        self._num_workers = num_workers
        self._stop_event = stop_event
        self._do_overlap = do_overlap

    def run(self) -> None:  # type: ignore[override]
        try:
            overlap_cb = (
                lambda c, t, p: self.overlap_progress.emit(c, t, p)
                if self._do_overlap
                else None
            )

            results = run_analysis(
                self._paths,
                self._num_workers,
                progress_callback=lambda c, t, p: self.progress.emit(c, t, p),
                overlap_callback=overlap_cb,
                stop_event=self._stop_event,
            )
            self.finished.emit(results)
        except Exception as exc:
            logger.exception("Analysis failed")
            self.error.emit(str(exc))


# ---------------------------------------------------------------------------
# main window
# ---------------------------------------------------------------------------

class MainWindow(QMainWindow):
    """Main IgorVision window with all user-facing functionality."""

    # Signal for thread-safe keypoint visualisation (emitted from worker thread)
    _keypoints_signal = pyqtSignal(str, object, int)  # (path, kpts_xy, matches)

    def __init__(self):
        super().__init__()
        self.setWindowTitle("IgorVision – Image Quality Inspector")
        self.setGeometry(100, 100, 1600, 1000)

        self._results: list[ImageQualityMetrics] = []
        self._by_path: dict[str, ImageQualityMetrics] = {}
        self._selected_paths: list[str] = []
        self._worker: _AnalysisWorker | None = None
        self._thread: QThread | None = None
        self._stop_event: threading.Event | None = None

        # persisted across sessions (see _load_settings / _save_settings)
        self._last_folder: str = ""
        self._last_files_dir: str = ""
        self._filter_mode: str = "all"

        self._setup_ui()
        self._connect_signals()
        self._load_settings()

    # ==================================================================
    # UI setup
    # ==================================================================

    def _setup_ui(self) -> None:
        self._setup_menu()

        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)

        # --- top-level tabs: Quality + Overlap (shared analysis view)
        #     + IGOR file tools ---
        self._top_tabs = QTabBar()
        self._top_tabs.addTab("Quality")
        self._top_tabs.addTab("Overlap")
        self._top_tabs.addTab("Compare")
        self._top_tabs.addTab("Rename")
        self._top_tabs.addTab("Sort")
        self._top_tabs.addTab("Metashape")

        self._main_stack = QStackedWidget()

        # Quality and Overlap share one analysis page (toolbar, preview,
        # stats, info, settings); only the left results view swaps.
        analysis_page = QWidget()
        page_layout = QVBoxLayout(analysis_page)
        page_layout.setContentsMargins(0, 0, 0, 0)

        # --- toolbar ---
        toolbar = QHBoxLayout()

        self._btn_folder = QPushButton("📁  Select Folder")
        self._btn_files = QPushButton("🖼️  Select Images")
        self._chk_recursive = QCheckBox("Include Subfolders")
        self._chk_recursive.setChecked(True)

        # CPU cores
        cores_box = QVBoxLayout()
        cores_box.addWidget(QLabel("CPU Cores:"))
        cores_row = QHBoxLayout()
        self._cpu_count = mp.cpu_count()
        self._cores_slider = QSlider(Qt.Horizontal)
        self._cores_slider.setRange(1, self._cpu_count)
        self._cores_slider.setValue(self._cpu_count)
        self._cores_label = QLabel(str(self._cpu_count))
        cores_row.addWidget(self._cores_slider)
        cores_row.addWidget(self._cores_label)
        cores_box.addLayout(cores_row)

        self._btn_start = QPushButton("🚀  Start Analysis")
        self._btn_stop = QPushButton("⏹  Stop")
        self._btn_stop.setEnabled(False)
        self._btn_overlap_scan = QPushButton("🔍  Scan Overlap")
        self._btn_overlap_scan.setEnabled(False)  # enabled after quality analysis

        toolbar.addWidget(self._btn_folder)
        toolbar.addWidget(self._btn_files)
        toolbar.addWidget(self._chk_recursive)
        self._chk_overlap = QCheckBox("Overlap-Check")
        self._chk_overlap.setChecked(True)
        toolbar.addWidget(self._chk_overlap)
        toolbar.addLayout(cores_box)
        toolbar.addWidget(self._btn_start)
        toolbar.addWidget(self._btn_overlap_scan)
        toolbar.addWidget(self._btn_stop)
        toolbar.addStretch()
        page_layout.addLayout(toolbar)

        # --- main splitter ---
        splitter = self._splitter = QSplitter(Qt.Horizontal)

        # left: stacked results views (Quality / Overlap)
        self._left_stack = QStackedWidget()

        # --- view 1: Quality (table + filters) ---
        quality_view = QWidget()
        quality_layout = QVBoxLayout(quality_view)
        quality_layout.setContentsMargins(0, 0, 0, 0)

        self._table = QTableWidget()
        self._table_model = QualityTableModel(self._table)
        quality_layout.addWidget(self._table)

        filter_row = QHBoxLayout()
        filter_row.addWidget(QLabel("Quality Threshold:"))
        self._quality_slider = QSlider(Qt.Horizontal)
        self._quality_slider.setRange(0, 100)
        self._quality_slider.setValue(50)
        self._quality_label = QLabel("0.50")
        filter_row.addWidget(self._quality_slider)
        filter_row.addWidget(self._quality_label)

        self._btn_show_all = QPushButton("Show All")
        self._btn_show_blurry = QPushButton("Show Blurry Only")
        filter_row.addWidget(self._btn_show_all)
        filter_row.addWidget(self._btn_show_blurry)
        quality_layout.addLayout(filter_row)

        self._left_stack.addWidget(quality_view)

        # --- view 2: Overlap (capture order) ---
        overlap_view = QWidget()
        overlap_layout = QVBoxLayout(overlap_view)
        overlap_layout.setContentsMargins(0, 0, 0, 0)

        self._overlap_table = QTableWidget(0, 5)
        self._overlap_table.setHorizontalHeaderLabels([
            "#", "Filename", "Overlap %", "Matches", "Inlier %"
        ])
        oh = self._overlap_table.horizontalHeader()
        oh.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        oh.setSectionResizeMode(1, QHeaderView.Stretch)
        oh.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        oh.setSectionResizeMode(3, QHeaderView.ResizeToContents)
        oh.setSectionResizeMode(4, QHeaderView.ResizeToContents)
        self._overlap_table.verticalHeader().setVisible(False)
        self._overlap_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._overlap_table.setSelectionMode(QAbstractItemView.SingleSelection)
        self._overlap_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._overlap_table.setAlternatingRowColors(True)
        overlap_layout.addWidget(self._overlap_table)

        self._left_stack.addWidget(overlap_view)

        splitter.addWidget(self._left_stack)

        # right: preview column + settings panel (horizontal splitter)
        preview_col = QWidget()
        right_layout = QVBoxLayout(preview_col)

        self._stats = StatsPanel(DEFAULT_CONFIG.blur_threshold)
        right_layout.addWidget(self._stats)

        self._viewer = ImageViewer()
        self._viewer.setMinimumSize(300, 160)
        self._viewer.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

        # --- zoom toolbar above the preview ---
        zoom_bar = QWidget()
        zrow = QHBoxLayout(zoom_bar)
        zrow.setContentsMargins(0, 0, 0, 0)
        zrow.setSpacing(4)
        self._btn_zoom_out = QPushButton("−")
        self._btn_zoom_in = QPushButton("+")
        self._btn_zoom_center = QPushButton("⌖ Center")
        self._btn_zoom_fit = QPushButton("⤢ Fit")
        self._btn_zoom_in.setFixedWidth(30)
        self._btn_zoom_out.setFixedWidth(30)
        self._zoom_combo = QComboBox()
        self._zoom_combo.setEditable(True)
        self._zoom_combo.addItems(["25 %", "50 %", "100 %", "200 %", "300 %"])
        self._zoom_combo.setMinimumWidth(84)
        zrow.addWidget(self._btn_zoom_out)
        zrow.addWidget(self._btn_zoom_in)
        zrow.addWidget(self._btn_zoom_center)
        zrow.addWidget(self._btn_zoom_fit)
        zrow.addWidget(self._zoom_combo)
        zrow.addStretch(1)
        right_layout.addWidget(zoom_bar)
        right_layout.addWidget(self._viewer, 1)   # preview = majority of the right side

        # Info: compact tabbed panel below the preview (replaces the long scroll wall)
        self._info_tabs = QTabWidget()
        self._info_tabs.setDocumentMode(True)
        self._info_labels = {}
        self._build_info_tabs()
        self._info_tabs.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        self._info_tabs.setMaximumHeight(280)
        self._clear_info()
        right_layout.addWidget(self._info_tabs, 0)

        # action buttons (compact 2x2 grid to keep the preview large)
        self._btn_keep = QPushButton("📁 Move to Keep")
        self._btn_reject = QPushButton("🗑️ Move to Reject")
        self._btn_explorer = QPushButton("📂 Explorer")
        self._btn_export = QPushButton("📊 Export CSV")
        actions = QGridLayout()
        actions.setSpacing(6)
        actions.addWidget(self._btn_keep, 0, 0)
        actions.addWidget(self._btn_reject, 0, 1)
        actions.addWidget(self._btn_explorer, 1, 0)
        actions.addWidget(self._btn_export, 1, 1)
        right_layout.addLayout(actions)

        # --- settings panel (right of the preview, tab-aware) ---
        self._settings = SettingsPanel()
        self._settings.setMinimumWidth(280)

        self._right_split = QSplitter(Qt.Horizontal)
        self._right_split.addWidget(preview_col)
        self._right_split.addWidget(self._settings)
        self._right_split.setSizes([600, 320])
        self._right_split.setChildrenCollapsible(False)

        splitter.addWidget(self._right_split)
        splitter.setSizes([1000, 900])
        # stretch so the table area always fills the window and the
        # progress bar stays pinned to the bottom
        page_layout.addWidget(splitter, 1)

        # --- progress bar (bottom of the window) ---
        self._progress = QProgressBar()
        self._progress.setRange(0, 1000)
        self._progress.setFormat("%p%")
        self._progress.setFixedHeight(22)
        self._progress_label = QLabel("Ready")
        prog_row = QHBoxLayout()
        prog_row.addWidget(self._progress)
        prog_row.addWidget(self._progress_label)
        page_layout.addLayout(prog_row)

        # status bar
        self.statusBar().showMessage("Ready")

        # --- register top-level pages: shared analysis + file tools ---
        self._main_stack.addWidget(analysis_page)
        self._main_stack.addWidget(CompareTab())
        self._main_stack.addWidget(RenameTab())
        self._main_stack.addWidget(SortTab())
        self._main_stack.addWidget(MetashapeTab())

        self._top_tabs.currentChanged.connect(self._on_top_tab_changed)
        layout.addWidget(self._top_tabs)
        layout.addWidget(self._main_stack, 1)

    # ==================================================================
    # menu / theme
    # ==================================================================

    def _setup_menu(self) -> None:
        mb = self.menuBar()

        m_settings = mb.addMenu("&Settings")
        self._act_theme_dark = QAction("Dark Theme", self, checkable=True, checked=True)
        self._act_theme_light = QAction("Light Theme", self, checkable=True)
        self._act_theme_dark.triggered.connect(lambda: self._set_theme("dark"))
        self._act_theme_light.triggered.connect(lambda: self._set_theme("light"))
        m_settings.addAction(self._act_theme_dark)
        m_settings.addAction(self._act_theme_light)

        m_help = mb.addMenu("&Help")
        act_about = QAction("About IgorVision", self)
        act_about.triggered.connect(self._show_about)
        m_help.addAction(act_about)

    def _set_theme(self, theme: str) -> None:
        """Switch between the dark and light design (persisted via QSettings)."""
        from PyQt5.QtWidgets import QApplication
        from styles import apply_stylesheet

        apply_stylesheet(QApplication.instance(), theme=theme)
        self._act_theme_dark.setChecked(theme == "dark")
        self._act_theme_light.setChecked(theme == "light")

    def _show_about(self) -> None:
        QMessageBox.about(
            self,
            "About IgorVision",
            "<b>IgorVision</b> – Professional Image Quality Inspector<br>"
            "Bokeh-aware, absolute quality scoring for photogrammetry workflows,<br>"
            "plus integrated file tools: Compare, Rename, Sort, Metashape cleanup.",
        )

    # ==================================================================
    # signal connections
    # ==================================================================

    def _connect_signals(self) -> None:
        self._btn_folder.clicked.connect(self._select_folder)
        self._btn_files.clicked.connect(self._select_files)
        self._btn_start.clicked.connect(self._start_analysis)
        self._btn_stop.clicked.connect(self._stop_analysis)
        self._btn_overlap_scan.clicked.connect(self._start_overlap_scan)
        self._keypoints_signal.connect(self._on_keypoints_visualize)

        self._cores_slider.valueChanged.connect(
            lambda v: self._cores_label.setText(str(v))
        )

        self._quality_slider.valueChanged.connect(self._update_quality_filter)
        self._btn_show_all.clicked.connect(lambda: self._filter_results("all"))
        self._btn_show_blurry.clicked.connect(lambda: self._filter_results("blurry"))

        self._table.selectionModel().selectionChanged.connect(self._on_selection_changed)
        self._table.cellDoubleClicked.connect(self._open_preview_dialog)
        self._overlap_table.selectionModel().selectionChanged.connect(
            self._on_overlap_table_selection
        )

        # zoom / framing controls
        self._btn_zoom_in.clicked.connect(self._viewer.zoom_in)
        self._btn_zoom_out.clicked.connect(self._viewer.zoom_out)
        self._btn_zoom_center.clicked.connect(self._viewer.center_image)
        self._btn_zoom_fit.clicked.connect(self._viewer.reset_view)
        self._zoom_combo.activated.connect(self._on_zoom_preset)
        if hasattr(self._zoom_combo, "textActivated"):
            self._zoom_combo.textActivated.connect(self._on_zoom_preset)
        self._viewer.zoom_changed.connect(self._on_zoom_changed)

        self._btn_keep.clicked.connect(lambda: self._move_selected("keep"))
        self._btn_reject.clicked.connect(lambda: self._move_selected("reject"))
        self._btn_explorer.clicked.connect(self._open_in_explorer)
        self._btn_export.clicked.connect(self._export_results)

        self._stats.select_requested.connect(self._select_path)

        # settings panel: live-apply side effects (e.g. histogram tick)
        self._settings.changed.connect(self._on_settings_changed)

    # ==================================================================
    # file selection
    # ==================================================================

    def _select_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(
            self, "Select Folder with Images", self._last_folder
        )
        if folder:
            self._last_folder = folder
            self._selected_paths = [folder]
            self.statusBar().showMessage(f"Selected folder: {folder}")

    def _select_files(self) -> None:
        files, _ = QFileDialog.getOpenFileNames(
            self,
            "Select Images",
            self._last_files_dir,
            "Images (*.png *.jpg *.jpeg *.tif *.tiff *.bmp *.gif *.webp);;All Files (*)",
        )
        if files:
            self._last_files_dir = os.path.dirname(files[0])
            self._selected_paths = files
            self.statusBar().showMessage(f"Selected {len(files)} files")

    # ==================================================================
    # analysis control
    # ==================================================================

    def _start_analysis(self) -> None:
        if not self._selected_paths:
            QMessageBox.warning(self, "No Selection", "Please select folder or files first.")
            return

        recursive = self._chk_recursive.isChecked()
        image_paths = collect_image_paths(self._selected_paths, recursive)

        if not image_paths:
            QMessageBox.warning(self, "No Images", "No images found in selected locations.")
            return

        # Clear previous results
        self._results.clear()
        self._table_model.set_results([])
        self._stats.set_results([])

        # Start background analysis
        self._stop_event = threading.Event()
        num_workers = self._cores_slider.value()
        do_overlap = self._chk_overlap.isChecked()

        # Note: SIFT / quality parameters are applied live by the
        # settings panel (ui/settings_panel.py) – the worker reads them
        # from DEFAULT_CONFIG at run time.

        self._worker = _AnalysisWorker(
            image_paths, num_workers, self._stop_event, do_overlap=do_overlap
        )
        self._thread = QThread()
        self._worker.moveToThread(self._thread)

        self._worker.progress.connect(self._on_progress)
        self._worker.overlap_progress.connect(self._on_overlap_progress)
        self._worker.finished.connect(self._on_analysis_finished)
        self._worker.error.connect(self._on_error)

        self._thread.started.connect(self._worker.run)
        self._thread.start()

        # UI state
        self._btn_start.setEnabled(False)
        self._btn_stop.setEnabled(True)
        self._progress.setValue(0)
        self.statusBar().showMessage(
            f"Analysing {len(image_paths)} images with {num_workers} cores…"
        )

    def _stop_analysis(self) -> None:
        if self._stop_event is not None:
            self._stop_event.set()
        if self._thread is not None:
            self._thread.quit()
            self._thread.wait(3000)
            self._thread = None
            self._worker = None
        self._btn_start.setEnabled(True)
        self._btn_stop.setEnabled(False)
        self.statusBar().showMessage("Analysis stopped")

    # ==================================================================
    # analysis callbacks (called from worker thread via signals)
    # ==================================================================

    def _on_progress(self, completed: int, total: int, current: str) -> None:
        if total > 0:
            self._progress.setValue(int(completed / total * 1000))
        self._progress_label.setText(
            f"Quality: {completed}/{total}  {os.path.basename(current)}"
        )

    def _on_overlap_progress(self, completed: int, total: int, current: str) -> None:
        if total > 0:
            self._progress.setValue(int(completed / total * 1000))
        self._progress_label.setText(
            f"Overlap: {completed}/{total}  →  {os.path.basename(current)}"
        )

    def _on_analysis_finished(self, results: list[ImageQualityMetrics]) -> None:
        self._results = results
        self._by_path = {r.path: r for r in results}
        self._table_model.set_results(results)
        self._stats.set_results(results)
        self._populate_overlap_table(results)

        self._btn_start.setEnabled(True)
        self._btn_stop.setEnabled(False)
        self._btn_overlap_scan.setEnabled(True)
        self._progress.setValue(1000)
        self._progress_label.setText("Quality: Done")

        total = len(results)
        blurry = sum(1 for r in results if r.is_blurry)

        # Auto-start overlap scan with keypoint visualisation if enabled
        if self._chk_overlap.isChecked() and len(results) > 1:
            self.statusBar().showMessage(
                f"Quality done ({total} images, {blurry} blurry). Starting overlap scan…"
            )
            self._start_overlap_scan()
        else:
            self.statusBar().showMessage(
                f"Complete: {total} images, {blurry} flagged as blurry"
            )

        # Clean up thread
        if self._thread is not None:
            self._thread.quit()
            self._thread.wait(2000)
            self._thread = None
            self._worker = None

    def _on_error(self, msg: str) -> None:
        logger.error(msg)
        self.statusBar().showMessage(f"Warning: {msg}", 5000)
        self._btn_start.setEnabled(True)
        self._btn_stop.setEnabled(False)

    # ==================================================================
    # standalone overlap scan (with keypoint visualisation)
    # ==================================================================

    def _start_overlap_scan(self) -> None:
        """Run the overlap scan in a background thread with live
        keypoint visualisation in the preview."""
        if not self._results:
            QMessageBox.information(self, "Overlap Scan",
                                    "No images loaded.\nStart the analysis first.")
            return

        from workers import OverlapScanWorker

        self._btn_overlap_scan.setEnabled(False)
        self._btn_start.setEnabled(False)
        self._progress.setValue(0)
        self._progress_label.setText("Overlap Scan: starting…")
        self.statusBar().showMessage("Scanning overlap with SIFT keypoint visualisation…")

        # Reset overlap values
        for r in self._results:
            r.overlap_next = -1.0
            r.overlap_matches = 0
            r.overlap_inlier_ratio = 0.0

        stop_event = threading.Event()
        self._overlap_stop = stop_event

        # Callbacks (called from worker thread – use signals for GUI updates)
        def _on_keypoints(path: str, kpts: np.ndarray, matches: int) -> None:
            self._keypoints_signal.emit(path, kpts, matches)

        def _on_progress(done: int, total: int, path: str) -> None:
            # Safe: Qt widget methods are thread-safe for simple setters
            self._progress.setValue(int(done / total * 1000) if total > 0 else 0)
            self._progress_label.setText(
                f"Overlap Scan: {done}/{total}  →  {os.path.basename(path)}"
            )

        def _on_finished(results) -> None:
            self._populate_overlap_table(self._results)
            self._btn_overlap_scan.setEnabled(True)
            self._btn_start.setEnabled(True)
            self._progress.setValue(1000)
            self._progress_label.setText("Overlap Scan: Done")
            self.statusBar().showMessage("Overlap scan complete.")

        worker = OverlapScanWorker(self._results, stop_event)
        worker.on_keypoints = _on_keypoints
        worker.on_progress = _on_progress
        worker.on_finished = _on_finished

        # Run in a daemon thread (CPU-bound cv2/numpy work releases the GIL)
        self._overlap_thread_ref = threading.Thread(target=worker.run, daemon=True)
        self._overlap_thread_ref.start()

    def _on_keypoints_visualize(self, path: str, kpts: np.ndarray, matches: int) -> None:
        """GUI-thread handler: show keypoints on the image in the preview."""
        print(f"[KPT] Visualising {len(kpts)} keypoints on {os.path.basename(path)} "
              f"(range x:[{kpts[:,0].min():.0f}-{kpts[:,0].max():.0f}] "
              f"y:[{kpts[:,1].min():.0f}-{kpts[:,1].max():.0f}])")
        self._viewer.show_keypoints(path, kpts)

    # ==================================================================
    # table filtering
    # ==================================================================

    def _update_quality_filter(self, value: int) -> None:
        threshold = value / 100.0
        self._quality_label.setText(f"{threshold:.2f}")
        self._apply_quality_filter(threshold)

    def _apply_quality_filter(self, threshold: float) -> None:
        t = self._table
        for row in range(t.rowCount()):
            item = t.item(row, 2)
            if item is None:
                continue
            score = item.data(Qt.UserRole)
            if score is not None:
                t.setRowHidden(row, score < threshold)

    def _filter_results(self, filter_type: str) -> None:
        self._filter_mode = filter_type
        t = self._table
        for row in range(t.rowCount()):
            if filter_type == "all":
                t.setRowHidden(row, False)
            elif filter_type == "blurry":
                r = self._result_at_row(row)
                t.setRowHidden(row, r is None or not r.is_blurry)

    # ==================================================================
    # preview & info
    # ==================================================================

    def _on_selection_changed(self, selected, _deselected) -> None:  # type: ignore[no-untyped-def]
        indexes = selected.indexes()
        if not indexes:
            self._viewer.clear()
            self._clear_info()
            return

        row = indexes[0].row()
        r = self._result_at_row(row)
        if r is not None:
            # Full resolution from the file (sharp zoom); thumbnail only as fallback.
            if not self._viewer.set_image_from_path(r.path) and r.thumbnail_data:
                self._viewer.set_image_from_data(r.thumbnail_data)
            self._viewer.zoom_to(25)   # default preview zoom (25 %)
            self._update_info(r)

    def _on_zoom_preset(self, _index: int = 0) -> None:
        """Apply the zoom level chosen in the combo (preset or typed value)."""
        text = self._zoom_combo.currentText().strip()
        num = "".join(ch for ch in text if ch.isdigit())
        if num:
            self._viewer.zoom_to(int(num))

    def _on_zoom_changed(self, pct: int) -> None:
        """Mirror the live zoom level into the combo (no preset side-effect)."""
        self._zoom_combo.blockSignals(True)
        self._zoom_combo.setEditText(f"{pct} %")
        self._zoom_combo.blockSignals(False)

    def _build_info_tabs(self) -> None:
        """
        Build the compact tabbed info panel (replaces the old single-label
        scroll wall).  Each tab is a rich-text ``QLabel`` stored in
        ``self._info_labels`` keyed by a short id used by ``_update_info``.
        """
        tabs = [
            ("Quality", "score"),
            ("SfM checks", "sfm"),
            ("Exposure & WB", "ewb"),
            ("EXIF", "exif"),
            ("Global", "global"),
        ]
        for title, key in tabs:
            label = QLabel("")
            label.setWordWrap(True)
            label.setTextFormat(Qt.RichText)
            label.setAlignment(Qt.AlignTop | Qt.AlignLeft)
            label.setContentsMargins(6, 6, 6, 6)
            self._info_tabs.addTab(label, title)
            self._info_labels[key] = label

    def _clear_info(self) -> None:
        """Reset all info tabs to an empty state (no selection)."""
        for label in self._info_labels.values():
            label.setText("")

    def _update_info(self, r: ImageQualityMetrics) -> None:
        exif_html = ""
        if r.exif:
            e = r.exif
            parts = []
            if e.get("make") or e.get("model"):
                parts.append(f"<b>Camera:</b> {e.get('make', '')} {e.get('model', '')}".strip())
            if e.get("focal_length"):
                parts.append(f"f = {e['focal_length']:.1f} mm")
            if e.get("aperture"):
                parts.append(f"f/{e['aperture']:.1f}")
            if e.get("shutter"):
                s = e["shutter"]
                shutter_txt = f"1/{int(round(1 / s))}" if s < 1 else f"{s:.2f} s"
                warn = " ⚠️ (slow for focal length)" if r.slow_shutter else ""
                parts.append(f"shutter {shutter_txt}{warn}")
            if e.get("iso"):
                parts.append(f"ISO {int(e['iso'])}")
            if e.get("datetime"):
                parts.append(str(e["datetime"]))
            if e.get("gps"):
                lat, lon = e["gps"]
                parts.append(f"GPS {lat:.5f}, {lon:.5f}")
            if parts:
                exif_html = "<hr><b>EXIF:</b><br>" + "<br>".join(parts) + "<br>"

        dup_html = ""
        if r.duplicate_of:
            dup_html = f"<hr><b>⚠️ Duplicate of:</b> {os.path.basename(r.duplicate_of)}<br>"

        err_html = ""
        if r.is_error:
            err_html = f"<hr><b style=\"color:#c00\">⚫ Load error:</b> {r.load_note or 'unknown'}<br>"

        # Exposure & white balance (raw values always shown for calibration)
        ev_txt = "n/a" if not r.ev else f"{r.ev:.1f}"
        expo_txt = "n/a" if r.expo_dev == 0.0 else f"{r.expo_dev:+.2f}"
        iso_txt = "n/a"
        if r.exif and r.exif.get("iso"):
            iso_txt = f"ISO {r.exif['iso']:.0f} ({r.iso_dev_stops:+.1f} st)"
        ap_txt = "n/a"
        if r.exif and r.exif.get("aperture"):
            ap_txt = f"f/{r.exif['aperture']:.1f} ({r.aperture_dev_stops:+.2f} st)"
        ewb_html = (
            "<hr><b>Exposure & white balance:</b><br>"
            f"• Luma median: {r.luma_median:.0f} · Dynamic range (p95-p5): {r.dynamic_range:.0f}"
            f"{' ⚠️ flat' if r.low_dynamic_range else ''}<br>"
            f"• EV (ISO-100): {ev_txt} · Δ vs set: {expo_txt}"
            f"{' ⚠️ drift' if r.exposure_outlier else ''}<br>"
            f"• White balance: ≈ {r.wb_kelvin:.0f} K (R/G {r.wb_gain_rg:.2f}, B/G {r.wb_gain_bg:.2f})"
            f" · Δ vs set: {r.wb_dev:.3f}{' ⚠️ drift' if r.wb_outlier else ''}<br>"
            f"• {iso_txt} · {ap_txt}<br>"
        )

        score_html = f"""
        <b>File:</b> {os.path.basename(r.path)}<br>
        <b>Dimensions:</b> {r.dimensions[0]} × {r.dimensions[1]} px<br>
        <b>File Size:</b> {r.file_size / (1024 * 1024):.1f} MB<br>
        <b>Quality Score:</b> {r.composite_score:.3f}<br>
        <b>Status:</b> {status_text(r)}<br>
        {err_html}
        <hr>
        <b>Sharpness:</b><br>
        • Subject Sharpness (top 10 %, norm.): {r.peak_sharpness:.1f}<br>
        • Sharpness Spread (mean/peak): {r.sharpness_spread:.3f}<br>
        • Tenengrad (Sobel-Gradient-Energie): {r.tenengrad:.0f}<br>
        • Gradient p95 (Peak-Detail): {r.gradient_p95:.1f}<br>
        <hr>
        <b>Motion Blur (Kamerawackeln):</b><br>
        • Spektrale Δ-Konzentration: {r.mb_delta_conc * 100:.1f} %<br>
        • Lag-Anisotropie: {r.mb_lag_aniso:.3f}<br>
        • Sobel Anisotropie (X/Y): {r.sobel_aniso:.3f}{" ⚠️ directional" if r.sobel_aniso < 0.2 else ""}<br>
        • Motion-Blur-Penalty: {r.mb_penalty * 100:.0f} %{" ⚠️ Soft (Motion)" if r.soft_motion_blur else ""}<br>
        """
        sfm_html = f"""
        <b>Photogrammetry Checks (SfM-Readiness):</b><br>
        • Feature Density (Harris corners/1k px): {r.feature_density:.2f}{" ⚠️ few features" if r.low_features else ""}<br>
        • Feature Uniformity (4×4 grid min/max): {r.feature_uniformity:.3f}{" ⚠️ uneven" if r.uneven_features else ""}<br>
        • Vignetting (corner/centre): {r.vignetting:.3f}{" ⚠️ vignetting" if r.has_vignetting else ""}<br>
        • Clipped Highlights: {r.clip_high * 100:.2f} %<br>
        • Crushed Shadows: {r.clip_low * 100:.2f} %<br>
        {("• Overlap (next): ?  (unreliable: {} m, ~{} inliers)<br>".format(r.overlap_matches, int(r.overlap_matches * r.overlap_inlier_ratio)) if r.overlap_next >= 0 and int(r.overlap_matches * r.overlap_inlier_ratio) < 8 else "• Overlap (next): {:.1f} %  ({} m · {:.0f} % inl)<br>".format(r.overlap_next * 100, r.overlap_matches, r.overlap_inlier_ratio) if r.overlap_next >= 0 else "• Overlap (next): n/a (last frame / no next)<br>")}
        """
        global_html = f"""
        <b>Global Metrics:</b><br>
        • Laplacian Variance: {r.laplacian_variance:.1f}<br>
        • Sobel Variance: {r.sobel_variance:.1f}   (X: {r.sobel_var_x:.1f} / Y: {r.sobel_var_y:.1f})<br>
        • Sobel Anisotropy (X/Y): {r.sobel_aniso:.3f}<br>
        • Tenengrad: {r.tenengrad:.0f}<br>
        • Gradient p95: {r.gradient_p95:.1f}<br>
        • Edge Density: {r.edge_density:.3f}<br>
        • Noise Level: {r.noise_level:.1f}<br>
        • Contrast: {r.contrast:.1f}<br>
        • Brightness: {r.brightness:.1f}<br>
        • Saturation: {r.saturation:.1f}
        """
        self._info_labels["score"].setText(score_html)
        self._info_labels["sfm"].setText(sfm_html)
        self._info_labels["ewb"].setText(ewb_html)
        self._info_labels["exif"].setText((exif_html + dup_html) or "<i>no EXIF data</i>")
        self._info_labels["global"].setText(global_html)

    def _open_preview_dialog(self, row: int, _col: int) -> None:
        r = self._result_at_row(row)
        if r is not None:
            dlg = ImagePreviewDialog(r.path, r, self)
            dlg.exec_()

    def _populate_overlap_table(
        self, results: list[ImageQualityMetrics]
    ) -> None:
        """Fill the Overlap tab with frames in capture (path-sorted) order."""
        valid = sorted(
            (r for r in results if not r.is_error), key=lambda r: r.path
        )
        t = self._overlap_table
        t.setRowCount(len(valid))
        for i, r in enumerate(valid):
            t.setItem(i, 0, QTableWidgetItem(str(i + 1)))
            t.setItem(i, 1, QTableWidgetItem(os.path.basename(r.path)))
            if r.overlap_next >= 0:
                # Reliability gate: if inlier count < 8, the homography is
                # geometrically unstable → show "?" instead of a wrong number.
                inlier_count = int(r.overlap_matches * r.overlap_inlier_ratio)
                if inlier_count < 8:
                    ov_item = QTableWidgetItem("?")
                    ov_item.setForeground(QColor(150, 150, 150))
                    ov_item.setToolTip(
                        f"Unreliable: {r.overlap_matches} matches, "
                        f"only ~{inlier_count} inliers (need ≥ 8). "
                        f"Homography geometrically unstable."
                    )
                else:
                    ov_item = QTableWidgetItem(f"{r.overlap_next * 100:.1f}")
                    # Colour-code: < 30 % = red, 30-90 % = green, > 90 % = blue
                    if r.overlap_next < 0.30:
                        ov_item.setBackground(QColor(255, 210, 210))
                    elif r.overlap_next > 0.90:
                        ov_item.setBackground(QColor(200, 220, 255))
                    else:
                        ov_item.setBackground(QColor(210, 255, 210))
                    # Dark text on the light fills (readable in both themes)
                    ov_item.setForeground(QColor(28, 31, 36))
                t.setItem(i, 2, ov_item)
            else:
                t.setItem(i, 2, QTableWidgetItem("n/a"))
            t.setItem(i, 3, QTableWidgetItem(str(r.overlap_matches) if r.overlap_matches else "–"))
            if r.overlap_inlier_ratio > 0:
                t.setItem(i, 4, QTableWidgetItem(f"{r.overlap_inlier_ratio * 100:.0f}"))
            else:
                t.setItem(i, 4, QTableWidgetItem("–"))

        # Store path on row 0 col 1 for lookup
        for i, r in enumerate(valid):
            t.item(i, 1).setData(Qt.UserRole, r.path)

    def _on_overlap_table_selection(self, selected, _deselected) -> None:  # type: ignore[no-untyped-def]
        """When a row is selected in the Overlap tab, show it in the preview."""
        indexes = selected.indexes()
        if not indexes:
            return
        row = indexes[0].row()
        item = self._overlap_table.item(row, 1)
        if item is None:
            return
        path = item.data(Qt.UserRole)
        if path and path in self._by_path:
            r = self._by_path[path]
            if not self._viewer.set_image_from_path(r.path) and r.thumbnail_data:
                self._viewer.set_image_from_data(r.thumbnail_data)
            self._viewer.zoom_to(25)
            self._update_info(r)

    def _on_top_tab_changed(self, index: int) -> None:
        """Top-level tab switch.

        Tabs 0/1 (Quality / Overlap) share the analysis page: only the
        left results view and the settings page (quality vs. SIFT)
        swap.  Tabs 2+ are the standalone file tools.
        """
        if index <= 1:
            self._main_stack.setCurrentIndex(0)
            self._left_stack.setCurrentIndex(index)
            self._settings.set_page(index)
        else:
            self._main_stack.setCurrentIndex(index - 1)

    def _on_settings_changed(self, name: str, value) -> None:
        """React to live settings-panel changes (value already applied
        to ``DEFAULT_CONFIG`` by the panel)."""
        if name == "blur_threshold":
            # keep the stats histogram threshold tick in sync
            self._stats.set_threshold(float(value))

    # ==================================================================
    # file operations
    # ==================================================================

    def _selected_rows(self) -> list[int]:
        rows: set[int] = set()
        for item in self._table.selectedItems():
            rows.add(item.row())
        return sorted(rows)

    def _result_at_row(self, row: int) -> ImageQualityMetrics | None:
        """
        Resolve the analysis result for a *visual* table row.

        The table is user-sortable, so the visual row order does not
        necessarily match ``self._results`` – the result is therefore
        looked up via the path stored on the row's items.
        """
        path = self._table_model.path_at_row(row)
        if path is None:
            return None
        return self._by_path.get(path)

    def _select_path(self, path: str) -> None:
        """Select the table row for *path* (from the stats best/worst)."""
        for row in range(self._table.rowCount()):
            if self._table_model.path_at_row(row) == path:
                self._table.selectRow(row)
                item = self._table.item(row, 1)
                if item is not None:
                    self._table.scrollToItem(item)
                break

    def _move_selected(self, folder_type: str) -> None:
        rows = self._selected_rows()
        if not rows:
            QMessageBox.information(self, "No Selection", "Please select images to move.")
            return

        from pathlib import Path

        folder = QFileDialog.getExistingDirectory(
            self, f"Select {folder_type.title()} Folder"
        )
        if not folder:
            return

        moved = 0
        for row in rows:
            r = self._result_at_row(row)
            if r is None:
                continue
            try:
                src = Path(r.path)
                dest = Path(folder) / src.name
                counter = 1
                while dest.exists():
                    dest = Path(folder) / f"{src.stem}_{counter}{src.suffix}"
                    counter += 1
                src.rename(dest)
                moved += 1
            except Exception as exc:
                QMessageBox.warning(self, "Move Error", f"Failed to move {src.name}: {exc}")

        if moved:
            QMessageBox.information(
                self,
                "Move Complete",
                f"Successfully moved {moved} image(s) to {folder_type} folder.",
            )
            self._refresh_results()

    def _refresh_results(self) -> None:
        existing = [r for r in self._results if os.path.exists(r.path)]
        self._results = existing
        self._by_path = {r.path: r for r in existing}
        self._table_model.set_results(existing)

    def _open_in_explorer(self) -> None:
        rows = self._selected_rows()
        if not rows:
            QMessageBox.information(self, "No Selection", "Please select an image.")
            return

        row = rows[0]
        r = self._result_at_row(row)
        if r is None or not os.path.exists(r.path):
            return
        path = r.path
        if sys.platform == "win32":
            os.startfile(os.path.dirname(path))  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            os.system(f'open -R "{path}"')
        else:
            os.system(f'xdg-open "{os.path.dirname(path)}"')

    # ==================================================================
    # export
    # ==================================================================

    def _export_results(self) -> None:
        if not self._results:
            QMessageBox.information(self, "No Data", "No results to export.")
            return

        filename, _ = QFileDialog.getSaveFileName(
            self,
            "Export Results",
            "image_quality_analysis.csv",
            "CSV Files (*.csv);;All Files (*)",
        )
        if not filename:
            return

        try:
            import pandas as pd

            df = pd.DataFrame(build_export_rows(self._results))
            df.to_csv(filename, index=False)

            QMessageBox.information(
                self, "Export Complete", f"Results exported to:\n{filename}"
            )
        except Exception as exc:
            QMessageBox.critical(self, "Export Error", f"Failed to export:\n{exc}")

    # ==================================================================
    # settings persistence (QSettings)
    # ==================================================================

    def _load_settings(self) -> None:
        """Restore window layout and user preferences from QSettings."""
        s = QSettings()
        try:
            geo = s.value("ui/window_geometry")
            if geo is not None:
                self.restoreGeometry(bytes(geo))

            state = s.value("ui/splitter_state")
            if state is not None:
                self._splitter.restoreState(bytes(state))

            self._cores_slider.setValue(int(s.value("ui/cores", self._cpu_count)))
            self._chk_recursive.setChecked(_as_bool(s.value("ui/recursive"), True))
            self._chk_overlap.setChecked(_as_bool(s.value("ui/overlap_check"), True))
            self._quality_slider.setValue(int(s.value("ui/quality_threshold", 50)))

            # settings panel (quality + overlap parameters)
            # migrate SIFT values saved by older versions (pre-panel keys)
            for old_key, new_name in (("ui/sift_features", "sift_n_features"),
                                     ("ui/sift_ratio", "sift_ratio"),
                                     ("ui/sift_ransac", "sift_ransac_px")):
                if s.value(old_key) is not None and s.value(f"ui/config/{new_name}") is None:
                    s.setValue(f"ui/config/{new_name}", s.value(old_key))
            self._settings.load_settings(s)
            self._stats.set_threshold(DEFAULT_CONFIG.blur_threshold)

            state = s.value("ui/right_splitter_state")
            if state is not None:
                self._right_split.restoreState(bytes(state))
            self._last_folder = str(s.value("ui/last_folder", "") or "")
            self._last_files_dir = str(s.value("ui/last_files_dir", "") or "")
            self._filter_mode = str(s.value("ui/filter_mode", "all") or "all")
            if self._filter_mode not in ("all", "blurry"):
                self._filter_mode = "all"

            theme = str(s.value("ui/theme", "dark") or "dark")
            if theme not in ("dark", "light"):
                theme = "dark"
            self._set_theme(theme)

            # keep label + (empty) table in sync with the restored threshold
            self._update_quality_filter(self._quality_slider.value())
        except Exception:
            logger.exception("Failed to restore settings – using defaults")

    def _save_settings(self) -> None:
        """Persist window layout and user preferences to QSettings."""
        s = QSettings()
        try:
            s.setValue("ui/window_geometry", self.saveGeometry())
            s.setValue("ui/splitter_state", self._splitter.saveState())
            s.setValue("ui/cores", self._cores_slider.value())
            s.setValue("ui/recursive", self._chk_recursive.isChecked())
            s.setValue("ui/overlap_check", self._chk_overlap.isChecked())
            s.setValue("ui/quality_threshold", self._quality_slider.value())
            self._settings.save_settings(s)
            s.setValue("ui/right_splitter_state", self._right_split.saveState())
            s.setValue("ui/last_folder", self._last_folder)
            s.setValue("ui/last_files_dir", self._last_files_dir)
            s.setValue("ui/filter_mode", self._filter_mode)
            s.setValue("ui/theme", "dark" if self._act_theme_dark.isChecked() else "light")
            s.sync()
        except Exception:
            logger.exception("Failed to save settings")

    def closeEvent(self, event) -> None:  # type: ignore[override]
        """Stop a running analysis and save settings on window close."""
        if self._thread is not None and self._thread.isRunning():
            if self._stop_event is not None:
                self._stop_event.set()
            self._thread.quit()
            self._thread.wait(2000)
        self._save_settings()
        super().closeEvent(event)
