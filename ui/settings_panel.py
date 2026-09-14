"""
IgorVision – Settings panel
============================

Tab-aware settings panel placed to the right of the image preview.

* **Quality page** – every tunable parameter from ``config.py`` as a
  slider + spinbox row (booleans as checkboxes), grouped into sections.
* **Overlap page** – SIFT / matching parameters as plain spinboxes
  (same ranges as the old parameter bar above the preview).

Behaviour
---------
* **Live apply** – every change is written into ``DEFAULT_CONFIG``
  immediately, so the next analysis / overlap scan picks it up without
  a restart.  ``changed(name, value)`` is emitted for side effects
  (e.g. moving the stats histogram threshold tick).
* **Persistence** – values are stored under ``ui/config/<name>`` in
  QSettings (see ``MainWindow._load_settings`` / ``_save_settings``).
* **Reset** – *Reset to Defaults* restores the factory defaults of
  ``AnalysisConfig`` (captured from a fresh instance, so live
  mutations of ``DEFAULT_CONFIG`` never corrupt the reset target).

Note: ``overlap_check`` is intentionally **not** part of this panel –
it is already controlled by the "Overlap-Check" checkbox in the
toolbar (avoiding two sources of truth for the same setting).
"""
from __future__ import annotations

import dataclasses
import logging
from dataclasses import dataclass

from PyQt5.QtCore import Qt, QSettings, pyqtSignal
from PyQt5.QtWidgets import (
    QCheckBox,
    QDoubleSpinBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QSlider,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from config import DEFAULT_CONFIG, AnalysisConfig

logger = logging.getLogger(__name__)

# Factory defaults, captured once from a *fresh* instance.
DEFAULTS: dict = {
    f.name: getattr(AnalysisConfig(), f.name)
    for f in dataclasses.fields(AnalysisConfig)
}


@dataclass(frozen=True)
class ParamSpec:
    """Declarative description of one tunable parameter."""

    name: str          # attribute name on AnalysisConfig
    label: str         # display label (keep short – fixed label width)
    kind: str          # "int" | "float" | "bool"
    min: float = 0.0
    max: float = 1.0
    step: float = 1.0
    decimals: int = 0
    tooltip: str = ""
    slider: bool = True  # False → spinbox only (Overlap page)


# ---------------------------------------------------------------------------
# parameter definitions (Quality page)
# ---------------------------------------------------------------------------

QUALITY_SECTIONS: list[tuple[str, list[ParamSpec]]] = [
    ("Resolution & Grid", [
        ParamSpec("analysis_dim", "Analysis Size (px)", "int", 512, 8192, 512,
                  tooltip="Longest side of the analysis image (images are downscaled to this)."),
        ParamSpec("grid_size", "Grid Size", "int", 2, 16, 1,
                  tooltip="Analysis cells per image side (8 → 8×8 = 64 blocks)."),
        ParamSpec("topk_fraction", "Top-k Fraction", "float", 0.01, 0.5, 0.01, 2,
                  tooltip="Fraction of the sharpest blocks used for the subject-sharpness score."),
    ]),
    ("Sharpness", [
        ParamSpec("blur_floor", "Blur Floor", "float", 0.0, 200.0, 1.0, 1,
                  tooltip="Normalised sharpness of a 'definitely blurry' image (score ≈ 0)."),
        ParamSpec("sharp_ref", "Sharp Reference", "float", 100.0, 3000.0, 10.0, 0,
                  tooltip="Normalised sharpness of a 'definitely sharp' image (score = 1)."),
        ParamSpec("blur_threshold", "Blur Threshold", "float", 0.0, 1.0, 0.01, 2,
                  tooltip="Score below this → flagged 'Blurry'."),
    ]),
    ("Noise", [
        ParamSpec("noise_penalty_max", "Max Noise Penalty", "float", 0.0, 0.5, 0.01, 2,
                  tooltip="Maximum score reduction applied to noisy images."),
        ParamSpec("noise_ref", "Noise Reference", "float", 5.0, 100.0, 1.0, 0,
                  tooltip="Noise level at which the full penalty applies."),
    ]),
    ("Exposure", [
        ParamSpec("min_contrast", "Min Contrast", "float", 0.0, 100.0, 1.0, 0,
                  tooltip="Std-dev below this → flat / washed out."),
        ParamSpec("min_brightness", "Min Brightness", "float", 0.0, 100.0, 1.0, 0,
                  tooltip="Mean brightness below this → underexposed."),
        ParamSpec("max_brightness", "Max Brightness", "float", 150.0, 255.0, 1.0, 0,
                  tooltip="Mean brightness above this → overexposed."),
    ]),
    ("Motion Blur", [
        ParamSpec("motion_blur_check", "Motion-Blur Check", "bool",
                  tooltip="Enable the two-cue (spectral + directional) motion-blur detector."),
        ParamSpec("mb_delta_good", "Δ Conc. Good", "float", 0.0, 0.5, 0.005, 3,
                  tooltip="delta_conc ≤ this → no spectral evidence."),
        ParamSpec("mb_delta_bad", "Δ Conc. Bad", "float", 0.0, 0.5, 0.005, 3,
                  tooltip="delta_conc ≥ this → full spectral evidence."),
        ParamSpec("mb_lag_good", "Lag Aniso. Good", "float", 0.0, 0.5, 0.005, 3,
                  tooltip="lag_aniso ≥ this → no directional evidence."),
        ParamSpec("mb_lag_bad", "Lag Aniso. Bad", "float", 0.0, 0.5, 0.005, 3,
                  tooltip="lag_aniso ≤ this → full directional evidence."),
        ParamSpec("motion_blur_penalty_max", "Max MB Penalty", "float", 0.0, 1.0, 0.01, 2,
                  tooltip="Max. score reduction when both cues fully indicate blur (score ×= 1 − value)."),
        ParamSpec("motion_blur_soft_check", "Soft (Motion) Warning", "bool",
                  tooltip="Surface slight camera shake as a 🟡 warning (never a reject)."),
        ParamSpec("soft_mb_floor", "Soft-MB Floor", "float", 0.0, 1.0, 0.01, 2,
                  tooltip="mb_penalty ≥ this → 'Soft (Motion)' warning."),
    ]),
    ("Features & Vignetting", [
        ParamSpec("feature_check", "Feature Check", "bool",
                  tooltip="Flag images with too few usable Harris features (sky, plain wall, …)."),
        ParamSpec("min_feature_density", "Min Feature Density", "float", 0.0, 20.0, 0.5, 1,
                  tooltip="Harris corners / 1k px below this → 'Few Features'."),
        ParamSpec("min_feature_uniformity", "Feature Uniformity", "float", 0.0, 0.5, 0.01, 2,
                  tooltip="Min/max Harris ratio below this → 'Uneven Features'."),
        ParamSpec("min_vignetting", "Min Vignetting", "float", 0.0, 1.0, 0.01, 2,
                  tooltip="Corner/centre brightness ratio below this → 'Vignetting'."),
    ]),
    ("Clipping", [
        ParamSpec("clip_check", "Clipping Check", "bool",
                  tooltip="Flag clipped highlights and crushed shadows."),
        ParamSpec("max_clip_high", "Max Clip Highlights", "float", 0.0, 0.2, 0.005, 3,
                  tooltip="Fraction of clipped highlights above this → flag."),
        ParamSpec("max_clip_low", "Max Clip Shadows", "float", 0.0, 1.0, 0.01, 2,
                  tooltip="Fraction of crushed shadows above this → flag."),
    ]),
    ("Exposure & WB Consistency", [
        ParamSpec("exposure_consistency", "Expo-Drift Check", "bool",
                  tooltip="Flag per-image exposure drift vs the set median (EXIF EV or luma)."),
        ParamSpec("exposure_outlier_ev", "Expo Δ (EV)", "float", 0.0, 3.0, 0.05, 2,
                  tooltip="|ΔEV| vs set median (stops) above this → 'Expo drift' (EXIF path)."),
        ParamSpec("exposure_outlier_luma", "Expo Δ (Luma)", "float", 0.0, 1.0, 0.01, 2,
                  tooltip="|Δluma|/median vs set median above this → 'Expo drift' (no-EXIF fallback)."),
        ParamSpec("low_dynamic_range_check", "Flat Check", "bool",
                  tooltip="Flag images whose used tonal range is too small (washed out)."),
        ParamSpec("min_dynamic_range", "Min Dynamic Range", "float", 0.0, 150.0, 1.0, 0,
                  tooltip="Luma p95−p5 below this → 'Flat'."),
        ParamSpec("wb_consistency", "WB-Drift Check", "bool",
                  tooltip="Flag white-balance drift vs the set median (white-point gain vector)."),
        ParamSpec("wb_gain_dev_max", "WB Gain Dev.", "float", 0.0, 0.5, 0.005, 3,
                  tooltip="White-point gain distance vs set median above this → 'WB drift'."),
        ParamSpec("iso_consistency", "Hi-ISO Check", "bool",
                  tooltip="Flag ISO values far above the set median."),
        ParamSpec("iso_dev_stops_max", "ISO Δ (stops)", "float", 0.0, 5.0, 0.1, 1,
                  tooltip="log2(iso / set-median-iso) above this → 'Hi-ISO'."),
        ParamSpec("aperture_consistency", "Aperture-Drift Check", "bool",
                  tooltip="Flag aperture values far from the set median (DoF change)."),
        ParamSpec("aperture_dev_stops_max", "Aperture Δ (stops)", "float", 0.0, 2.0, 0.05, 2,
                  tooltip="log2(N / set-median-N) above this → 'Aperture drift'."),
    ]),
    ("Ingestion", [
        ParamSpec("reduced_decode", "Reduced Decode", "bool",
                  tooltip="Decode very large files at reduced resolution (4×/16× faster, no quality loss for the pipeline)."),
    ]),
]

# ---------------------------------------------------------------------------
# parameter definitions (Overlap page – spinboxes only, like the old bar)
# ---------------------------------------------------------------------------

OVERLAP_PARAMS: list[ParamSpec] = [
    ParamSpec("sift_n_features", "SIFT Feat", "int", 500, 10000, 500,
              slider=False, tooltip="Max. SIFT keypoints per image."),
    ParamSpec("sift_ratio", "Ratio", "float", 0.50, 0.95, 0.05, 2,
              slider=False, tooltip="Lowe ratio test threshold (lower = stricter match filter)."),
    ParamSpec("sift_ransac_px", "RANSAC", "float", 1.0, 10.0, 0.5, 1,
              slider=False, tooltip="RANSAC reprojection threshold (pixels)."),
]


# ---------------------------------------------------------------------------
# one parameter row
# ---------------------------------------------------------------------------

class _ParamRow(QWidget):
    """One parameter row: label + slider + spinbox (or a checkbox)."""

    valueChanged = pyqtSignal(str, object)  # (name, value)

    _LABEL_W = 140
    _SPIN_W = 76

    def __init__(self, spec: ParamSpec, initial, parent: QWidget | None = None):
        super().__init__(parent)
        self._spec = spec

        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)

        if spec.kind == "bool":
            self._check = QCheckBox(spec.label)
            self._check.setChecked(bool(initial))
            if spec.tooltip:
                self._check.setToolTip(spec.tooltip)
            self._check.toggled.connect(lambda _c: self._emit())
            lay.addWidget(self._check)
            lay.addStretch(1)
            return

        self._label = QLabel(spec.label)
        self._label.setFixedWidth(self._LABEL_W)
        if spec.tooltip:
            self._label.setToolTip(spec.tooltip)

        if spec.kind == "int":
            self._spin: QSpinBox | QDoubleSpinBox = QSpinBox()
            self._spin.setRange(int(spec.min), int(spec.max))
            self._spin.setSingleStep(int(spec.step))
        else:
            self._spin = QDoubleSpinBox()
            self._spin.setRange(float(spec.min), float(spec.max))
            self._spin.setSingleStep(float(spec.step))
            self._spin.setDecimals(spec.decimals)
        if spec.tooltip:
            self._spin.setToolTip(spec.tooltip)
        self._spin.setFixedWidth(self._SPIN_W)

        lay.addWidget(self._label)

        self._slider: QSlider | None = None
        if spec.slider:
            self._slider = QSlider(Qt.Horizontal)
            steps = max(1, int(round((spec.max - spec.min) / spec.step)))
            self._slider.setRange(0, steps)
            self._slider.setPageStep(max(1, steps // 10))
            self._slider.valueChanged.connect(self._on_slider)
            lay.addWidget(self._slider, 1)

        lay.addWidget(self._spin)
        if self._slider is None:
            lay.addStretch(1)

        self._spin.valueChanged.connect(self._on_spin)
        self.set_value(initial)  # initial state (no signal)

    # ------------------------------------------------------------------
    # public API
    # ------------------------------------------------------------------

    def value(self):
        if self._spec.kind == "bool":
            return self._check.isChecked()
        v = self._spin.value()
        return int(v) if self._spec.kind == "int" else float(v)

    def set_value(self, v) -> None:
        """Set the row value *without* emitting ``valueChanged``."""
        if self._spec.kind == "bool":
            self._check.blockSignals(True)
            self._check.setChecked(bool(v))
            self._check.blockSignals(False)
            return

        v = max(self._spin.minimum(), min(self._spin.maximum(), v))
        self._spin.blockSignals(True)
        self._spin.setValue(v)
        self._spin.blockSignals(False)

        if self._slider is not None:
            steps = int(round((float(v) - self._spec.min) / self._spec.step))
            steps = max(self._slider.minimum(), min(self._slider.maximum(), steps))
            self._slider.blockSignals(True)
            self._slider.setValue(steps)
            self._slider.blockSignals(False)

    def coerce(self, raw):
        """Coerce a QSettings value (may arrive as str on Windows)."""
        if self._spec.kind == "bool":
            if isinstance(raw, str):
                return raw.strip().lower() in ("1", "true", "yes", "on")
            return bool(raw)
        if self._spec.kind == "int":
            return int(float(raw))
        return float(raw)

    # ------------------------------------------------------------------
    # internals
    # ------------------------------------------------------------------

    def _emit(self) -> None:
        self.valueChanged.emit(self._spec.name, self.value())

    def _on_spin(self, v) -> None:
        if self._slider is not None:
            steps = int(round((float(v) - self._spec.min) / self._spec.step))
            steps = max(self._slider.minimum(), min(self._slider.maximum(), steps))
            if self._slider.value() != steps:
                self._slider.blockSignals(True)
                self._slider.setValue(steps)
                self._slider.blockSignals(False)
        self._emit()

    def _on_slider(self, steps: int) -> None:
        v = self._spec.min + steps * self._spec.step
        if self._spec.kind == "int":
            v = int(round(v))
        else:
            v = round(float(v), self._spec.decimals)
        v = max(self._spin.minimum(), min(self._spin.maximum(), v))
        if self._spin.value() != v:
            self._spin.blockSignals(True)
            self._spin.setValue(v)
            self._spin.blockSignals(False)
        self._emit()


# ---------------------------------------------------------------------------
# the panel
# ---------------------------------------------------------------------------

class SettingsPanel(QWidget):
    """Tab-aware settings panel (Quality / Overlap) for the preview area."""

    PAGE_QUALITY = 0
    PAGE_OVERLAP = 1

    changed = pyqtSignal(str, object)  # (param name, new value) – live applied

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._rows: dict[str, _ParamRow] = {}

        outer = QVBoxLayout(self)
        outer.setContentsMargins(10, 8, 10, 8)
        outer.setSpacing(8)

        self._title = QLabel("Quality Settings")
        self._title.setStyleSheet("font-size: 13px; font-weight: 600;")
        outer.addWidget(self._title)

        self._stack = QStackedWidget()
        outer.addWidget(self._stack, 1)

        self._btn_reset = QPushButton("↺  Reset to Defaults")
        self._btn_reset.setToolTip("Restore all parameters to the config.py defaults")
        self._btn_reset.clicked.connect(self.reset_to_defaults)
        outer.addWidget(self._btn_reset)

        self._stack.addWidget(self._build_quality_page())
        self._stack.addWidget(self._build_overlap_page())

    # ------------------------------------------------------------------
    # page building
    # ------------------------------------------------------------------

    def _build_quality_page(self) -> QWidget:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)

        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(2, 2, 2, 2)
        lay.setSpacing(6)

        for section, specs in QUALITY_SECTIONS:
            lay.addSpacing(6)
            lay.addWidget(self._section_label(section))
            for spec in specs:
                self._add_row(lay, spec)
        lay.addStretch(1)

        scroll.setWidget(page)
        return scroll

    def _build_overlap_page(self) -> QWidget:
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(2, 2, 2, 2)
        lay.setSpacing(8)

        lay.addSpacing(6)
        lay.addWidget(self._section_label("SIFT / Feature Matching"))

        hint = QLabel(
            "Parameters for the pair-wise overlap scan: SIFT keypoint "
            "count, Lowe ratio test and RANSAC reprojection threshold. "
            "Changes apply to the next overlap scan."
        )
        hint.setWordWrap(True)
        hint.setStyleSheet("font-style: italic;")
        lay.addWidget(hint)

        for spec in OVERLAP_PARAMS:
            self._add_row(lay, spec)
        lay.addStretch(1)
        return page

    def _section_label(self, text: str) -> QLabel:
        lbl = QLabel(text)
        lbl.setStyleSheet("font-weight: 600; font-size: 12px;")
        return lbl

    def _add_row(self, lay: QVBoxLayout, spec: ParamSpec) -> None:
        row = _ParamRow(spec, getattr(DEFAULT_CONFIG, spec.name))
        self._rows[spec.name] = row
        row.valueChanged.connect(self._on_row_changed)
        lay.addWidget(row)

    def _on_row_changed(self, name: str, value) -> None:
        self._apply(name, value)
        self.changed.emit(name, value)

    # ------------------------------------------------------------------
    # public API
    # ------------------------------------------------------------------

    def set_page(self, index: int) -> None:
        """Show the page matching the active left tab (0/1)."""
        index = max(self.PAGE_QUALITY, min(self.PAGE_OVERLAP, index))
        self._stack.setCurrentIndex(index)
        self._title.setText(
            "Quality Settings" if index == self.PAGE_QUALITY
            else "Overlap Settings"
        )

    def value(self, name: str):
        return self._rows[name].value()

    def param_names(self) -> list[str]:
        return list(self._rows)

    def load_settings(self, s: QSettings) -> None:
        """Restore persisted values (``ui/config/<name>``); missing keys
        keep the current value.  Applied to ``DEFAULT_CONFIG`` live."""
        for name, row in self._rows.items():
            raw = s.value(f"ui/config/{name}")
            if raw is None:
                continue
            try:
                v = row.coerce(raw)
            except (TypeError, ValueError):
                logger.warning("Skipping invalid setting ui/config/%s=%r", name, raw)
                continue
            row.set_value(v)
            self._apply(name, v)

    def save_settings(self, s: QSettings) -> None:
        for name, row in self._rows.items():
            s.setValue(f"ui/config/{name}", row.value())

    def reset_to_defaults(self) -> None:
        """Restore every parameter to its ``config.py`` default (live)."""
        for name, row in self._rows.items():
            v = DEFAULTS[name]
            row.set_value(v)
            self._apply(name, v)
            self.changed.emit(name, v)

    # ------------------------------------------------------------------
    # internals
    # ------------------------------------------------------------------

    @staticmethod
    def _apply(name: str, value) -> None:
        """Write a value into the live config the workers read from."""
        try:
            setattr(DEFAULT_CONFIG, name, value)
        except AttributeError:
            logger.warning("Unknown config parameter: %s", name)
