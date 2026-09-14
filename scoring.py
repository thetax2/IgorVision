"""
IgorVision – Bokeh-aware, **absolute** scoring
==============================================

Design goals (aligned with Agisoft Metashape's "Check image quality"):

1. **Absolute, batch-independent score.**  Each image is scored on its
   own, so the result does not change when other images are in/out of
   the batch.  (Metashape's check behaves the same way.)

2. **Bokeh-friendly.**  The score is driven by the **sharpest ~10 % of
   image blocks** (mean of the top-k).  A sharp subject in front of a
   creamy-bokeh background therefore scores HIGH – exactly the case the
   old relative scoring misclassified.  A genuinely soft image has no
   sharp blocks at all, so its top-k average stays LOW.

3. **Resolution-independent.**  Laplacian variance grows roughly with the
   square of the image size for the same content, so the measured peak
   is normalised to the standard analysis resolution
   (``config.analysis_dim``) before being mapped to 0…1.

Scale mapping (linear in log10 space):

    score = (log10(peak_norm) − log10(blur_floor))
            / (log10(sharp_ref) − log10(blur_floor)),  clipped to [0, 1]

``blur_floor`` (30)  →  score 0.0  ("definitely blurry")
``sharp_ref`` (600)  →  score 1.0  ("definitely sharp")

Both are plain, tunable numbers in :mod:`config` – easy to calibrate
against Metashape's own output.

Noise
-----
Sensor noise is high-frequency and would inflate the sharpness score, so
a mild, configurable penalty is applied (``noise_penalty_max``).

Exposure
--------
Metashape also flags bad exposure: we do the same with simple
brightness / contrast checks (``exposure_ok``), reported separately from
the blur verdict.
"""
from __future__ import annotations

import numpy as np

from config import AnalysisConfig
from models import BlockMetrics


def _topk_peak(sharpness: np.ndarray, fraction: float) -> float:
    """Mean of the top-*fraction* sharpest blocks (minimum 2)."""
    n = sharpness.size
    k = max(2, int(round(n * fraction)))
    k = min(k, n)
    top = np.sort(sharpness)[::-1][:k]
    return float(top.mean())


def score_from_blocks(
    bm: BlockMetrics,
    config: AnalysisConfig,
    analysis_longest_side: int,
) -> tuple[float, float, float]:
    """
    Compute the **absolute** quality metrics for one image.

    Parameters
    ----------
    bm:
        Per-block sharpness (Laplacian variance), measured on an image
        whose longest side is ``min(original_side, analysis_dim)``.
    config:
        Tunable parameters (see :mod:`config`).
    analysis_longest_side:
        Actual longest side of the image the blocks were computed on.

    Returns
    -------
    (peak_sharpness, sharpness_spread, composite_score)

    * ``peak_sharpness`` – resolution-normalised top-k block sharpness
      (raw value, handy for calibration / CSV).
    * ``sharpness_spread`` – mean / peak ratio, [0, 1]  (informational
      only – intentionally **not** penalised, bokeh images have low
      spread by nature).
    * ``composite_score`` – final 0…1 score including the noise penalty.
    """
    sharpness = bm.sharpness
    if sharpness.size == 0 or sharpness.max() < 1e-10:
        return 0.0, 0.0, 0.0

    # --- resolution normalisation --------------------------------------
    # Downscaling by factor f reduces Laplacian variance by ~f², so
    # normalising *up* to the reference resolution multiplies by f².
    f = max(1.0, config.analysis_dim / max(1, analysis_longest_side))
    peak_raw = _topk_peak(sharpness, config.topk_fraction)
    peak_norm = peak_raw * f * f

    # --- log-linear mapping to [0, 1] ----------------------------------
    lo = float(np.log10(max(config.blur_floor, 1e-6)))
    hi = float(np.log10(max(config.sharp_ref, config.blur_floor + 1e-6)))
    t = (float(np.log10(max(peak_norm, 1e-6))) - lo) / (hi - lo)
    base_score = float(np.clip(t, 0.0, 1.0))

    # --- spread (info only) ---------------------------------------------
    mean = float(sharpness.mean())
    spread = min(1.0, mean / peak_raw) if peak_raw > 0 else 0.0

    return peak_norm, spread, base_score


def apply_noise_penalty(score: float, noise_level: float, config: AnalysisConfig) -> float:
    """Mild multiplicative penalty for high sensor noise."""
    if config.noise_penalty_max <= 0.0 or config.noise_ref <= 0.0:
        return score
    frac = min(1.0, max(0.0, noise_level / config.noise_ref))
    return score * (1.0 - config.noise_penalty_max * frac)


def apply_motion_blur_penalty(
    score: float,
    delta_conc: float,
    lag_aniso: float,
    config: AnalysisConfig,
) -> tuple[float, float]:
    """
    Multiplicative penalty for camera shake.

    Each cue is mapped to an evidence fraction in [0, 1] (linear
    between its ``good`` and ``bad`` calibration values); the two
    fractions are then **multiplied** (AND), so a penalty only applies
    when *both* the spectral and the spatial cue agree.  This is
    essential: sharp, strongly textured images can be extreme in one
    cue (thatch → high ``delta_conc``; reeds → low ``lag_aniso``),
    while a genuinely shaken image is extreme in both.  Returns
    ``(penalised_score, penalty_fraction)``.
    """
    if not config.motion_blur_check or score <= 0.0:
        return score, 0.0

    def frac(value: float, good: float, bad: float, rising: bool) -> float:
        span = abs(bad - good)
        if span <= 1e-12:
            return 0.0
        t = (value - good) / span if rising else (good - value) / span
        return float(np.clip(t, 0.0, 1.0))

    f = (
        frac(delta_conc, config.mb_delta_good, config.mb_delta_bad, True)
        * frac(lag_aniso, config.mb_lag_good, config.mb_lag_bad, False)
    )
    if f <= 0.0:
        return score, 0.0
    penalised = score * (1.0 - f * config.motion_blur_penalty_max)
    return float(max(0.0, penalised)), f


def exposure_ok(brightness: float, contrast: float, config: AnalysisConfig) -> bool:
    """Simple Metashape-style exposure sanity check."""
    if contrast < config.min_contrast:
        return False
    if brightness < config.min_brightness:
        return False
    if brightness > config.max_brightness:
        return False
    return True
