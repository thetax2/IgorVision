"""
IgorVision – Data models
"""
from dataclasses import dataclass, field
from typing import Tuple

import numpy as np


@dataclass
class BlockMetrics:
    """Per-block analysis results (internal)."""
    sharpness: np.ndarray  # (n_blocks,) Laplacian variance per block


@dataclass
class ImageQualityMetrics:
    """Complete quality-analysis result for a single image."""

    # --- identity ---
    path: str

    # --- core scores (raw, absolute – independent of the batch) ---
    peak_sharpness: float  # resolution-normalised sharpness of the sharpest ~10 % of blocks
    sharpness_spread: float  # mean / peak ratio, [0, 1]  (info only)

    # --- global display metrics ---
    laplacian_variance: float
    sobel_variance: float
    edge_density: float
    noise_level: float
    contrast: float
    brightness: float
    saturation: float

    # --- final (absolute, filled during per-image analysis) ---
    composite_score: float = 0.0
    is_blurry: bool = True
    exposure_ok: bool = True

    # --- motion blur evidence (raw, for UI/CSV/calibration) ---
    mb_delta_conc: float = 0.0   # spectral Δ-concentration (see config)
    mb_lag_aniso: float = 1.0    # lag-1 directional anisotropy (0…1)
    mb_penalty: float = 0.0      # applied motion-blur penalty fraction (0…1)
    soft_motion_blur: bool = False  # "Soft (Motion)": real but sub-reject motion-blur evidence

    # --- ingestion (EXIF / format) ---
    exif: dict = field(default_factory=dict)   # make, model, aperture, shutter, iso, focal_length, gps, datetime, …
    slow_shutter: bool = False                  # shutter slower than 1/focal-length
    load_backend: str = "cv2"                   # cv2 | pillow-heif | rawpy
    load_note: str = ""                         # human note (e.g. "RAW (rawpy)", missing-backend hint)
    md5: str = ""                               # file hash (duplicate detection)
    is_error: bool = False                      # decode/load failed

    # --- photogrammetry checks ---
    feature_density: float = 0.0  # Harris corners per 1k pixels (SfM feature estimate)
    low_features: bool = False    # sharp but feature-poor (sky, wall, water, …)
    feature_uniformity: float = 1.0  # min/max Harris ratio across 4×4 grid (1.0 = uniform)
    uneven_features: bool = False   # features concentrated in one area (SfM coverage risk)
    vignetting: float = 1.0         # corner/centre brightness ratio (1.0 = flat)
    has_vignetting: bool = False    # vignetting below threshold
    clip_high: float = 0.0        # fraction of clipped-highlight pixels (any channel ≥ 253)
    clip_low: float = 0.0         # fraction of clipped-shadow pixels  (all channels ≤ 2)
    clipping_ok: bool = True      # clip fractions within configured limits
    duplicate_of: str = ""        # path of the first file with the same md5

    # --- exposure & white balance (Belichtung & WB) ---
    luma_median: float = 0.0        # robust luminance median (0..255)
    dynamic_range: float = 0.0      # used tonal span (luma p95 - p5); small = flat
    wb_kelvin: float = 0.0          # estimated colour temperature (Kelvin)
    wb_gain_rg: float = 1.0         # white-point R/G gain (WB signal)
    wb_gain_bg: float = 1.0         # white-point B/G gain (WB signal)
    ev: float = 0.0                 # EXIF scene EV (ISO-100 norm.), 0 = unknown
    # set-consistency flags (filled in workers.run_analysis)
    expo_dev: float = 0.0           # deviation from set median (EV stops or luma fraction)
    exposure_outlier: bool = False  # "Expo drift"
    wb_dev: float = 0.0             # WB gain-vector distance from set median
    wb_outlier: bool = False        # "WB drift"
    low_dynamic_range: bool = False # "Flat" (washed out)
    iso_dev_stops: float = 0.0      # log2(iso / set-median-iso)
    high_iso: bool = False          # "Hi-ISO"
    aperture_dev_stops: float = 0.0 # log2(N / set-median-N)
    aperture_outlier: bool = False  # "Aperture drift"

    # --- gradient diagnostics (directional sharpness / CA-resistant) ---
    sobel_var_x: float = 0.0
    sobel_var_y: float = 0.0
    sobel_aniso: float = 1.0
    tenengrad: float = 0.0
    gradient_p95: float = 0.0

    # --- pair-wise overlap (SfM coverage, set-level) ---
    overlap_next: float = -1.0        # geometric overlap with next image (0…1), -1 = n/a
    overlap_matches: int = 0          # good ORB matches (0 = no match / n/a)
    overlap_inlier_ratio: float = 0.0 # RANSAC inlier ratio (0…1)

    # --- metadata ---
    thumbnail_data: bytes = field(default=b'', repr=False)
    file_size: int = 0
    dimensions: Tuple[int, int] = (0, 0)


# ---------------------------------------------------------------------------
# presentation (shared by table + info panel – no Qt imports)
# ---------------------------------------------------------------------------

def status_text(r: ImageQualityMetrics) -> str:
    """Combined human-readable status (blur + exposure + photogrammetry)."""
    if r.is_error:
        return "⚫ Error"
    problems: list[str] = []
    if r.is_blurry:
        problems.append("Blurry (Motion)" if r.mb_penalty > 0 else "Blurry")
    if r.soft_motion_blur and not r.is_blurry:
        problems.append("Soft (Motion)")
    if not r.exposure_ok:
        problems.append("Exposure")
    if not r.clipping_ok:
        problems.append("Clipping")
    if r.low_features:
        problems.append("Few Features")
    if r.uneven_features:
        problems.append("Uneven Features")
    if r.has_vignetting:
        problems.append("Vignetting")
    if r.duplicate_of:
        problems.append("Duplicate")
    if r.exposure_outlier:
        problems.append("Expo drift")
    if r.wb_outlier:
        problems.append("WB drift")
    if r.low_dynamic_range:
        problems.append("Flat")
    if r.high_iso:
        problems.append("Hi-ISO")
    if r.aperture_outlier:
        problems.append("Aperture drift")
    if not problems:
        return "🟢 Sharp"
    head = "🔴" if r.is_blurry else "🟡"
    text = " + ".join(problems)
    if r.slow_shutter and r.is_blurry:
        text += " · slow shutter"
    return f"{head} {text}"
