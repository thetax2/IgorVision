"""
IgorVision – Configuration
==========================

All tunable parameters live here.  Scoring is **absolute**: each image is
scored independently, so results do not depend on the rest of the batch
(the same behaviour as Agisoft Metashape's "Check image quality").

Calibration reference
---------------------
Sharpness is measured as Laplacian variance of the sharpest ~10 % of
image blocks, **normalised to the standard analysis resolution**
(``analysis_dim``), i.e. the value a 2048-px image of the same content
would produce.

Typical values at that reference resolution:

=================  ===========================================
value              meaning
=================  ===========================================
< 10               motion blur / very soft / far out of focus
30  (blur_floor)   "definitely blurry" – score ≈ 0.0
600 (sharp_ref)    "definitely sharp"  – score = 1.0
> 2000             extremely fine, high-frequency texture
=================  ===========================================

Adjust ``blur_floor`` / ``sharp_ref`` if the scores do not match what you
see (e.g. compared with Metashape's own quality check).
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class AnalysisConfig:
    # --- resolution ---------------------------------------------------
    # Longest side of the analysis image.  Images larger than this are
    # downscaled (fast + resolution-independent); smaller images keep
    # their native size and sharpness is normalised up to this reference.
    analysis_dim: int = 2048

    # --- block grid ----------------------------------------------------
    # Grid of analysis cells per image side (8 x 8 = 64 blocks).
    grid_size: int = 8
    # Fraction of the sharpest blocks used for the "subject sharpness"
    # (0.10 = mean of the top ~6 blocks).  Bokeh images have only a few
    # sharp blocks; the average keeps the score high while a genuinely
    # soft image stays low.
    topk_fraction: float = 0.10

    # --- absolute sharpness scale --------------------------------------
    blur_floor: float = 30.0    # normalised sharpness of a "definitely blurry" image
    sharp_ref: float = 600.0    # normalised sharpness of a "definitely sharp" image
    blur_threshold: float = 0.5  # score below this → flagged "Blurry"

    # --- noise -----------------------------------------------------------
    # Mild penalty for noisy images (noise is high-frequency and would
    # otherwise inflate the sharpness score).
    noise_penalty_max: float = 0.15
    noise_ref: float = 25.0     # noise level at which the full penalty applies

    # --- exposure (Metashape also flags "bad exposure") -----------------
    min_contrast: float = 18.0   # std below this → flat / washed out
    min_brightness: float = 22.0  # mean below this → underexposed
    max_brightness: float = 232.0  # mean above this → overexposed

    # --- motion blur (camera shake) ----------------
    # The plain Laplacian score is direction-blind: on strongly textured
    # images (thatch, bark, reeds) it stays high even when the camera
    # shook, because edges parallel to the motion survive the smear.
    #
    # Detection uses two complementary cues (see
    # metrics.compute_motion_blur_features) that must **agree** (AND –
    # the two evidence fractions are multiplied):
    #
    #   delta_conc (spectral) – motion blur strips the multi-directional
    #                  fine detail out of the high-frequency angular
    #                  profile while the texture-aligned direction
    #                  survives → the high band becomes more
    #                  concentrated than the low band.
    #   lag_aniso  (spatial)  – one direction loses high-frequency
    #                  energy in the sharpest blocks (the smear runs
    #                  across it).
    #
    # The AND is essential: sharp, strongly textured images can be
    # extreme in *one* cue (thatch → high delta_conc up to +24 %;
    # reeds → low lag_aniso down to 0.07) but in 4,241 sharp reference
    # photos (Galli Colmap/Interior/Exterior + EichenHain) never in
    # both.  The blurry reference image (23.1 % / 0.143…0.148) is the
    # only photo that triggers both cues; the next sharp photo sits at
    # (17.8 % / 0.207), so the calibrated ramps
    # (delta_conc 0.15…0.20, lag_aniso 0.10…0.21) sit in the gap.
    motion_blur_check: bool = True
    mb_delta_good: float = 0.15   # delta_conc ≤ this → no spectral evidence
    mb_delta_bad: float = 0.20    # delta_conc ≥ this → full spectral evidence
    mb_lag_good: float = 0.21     # lag_aniso ≥ this → no directional evidence
    mb_lag_bad: float = 0.10      # lag_aniso ≤ this → full directional evidence
    # Maximum score reduction when both cues fully indicate blur
    # (score ×= 1 − motion_blur_penalty_max).
    motion_blur_penalty_max: float = 0.95

    # "Soft (Motion)" warning tier – the detector found REAL (both-cue) motion
    # blur but not enough to push the score below blur_threshold, so the image
    # would otherwise still read "Sharp".  This surfaces "minimal verrissen"
    # captures (slight camera shake at capture).  Calibrated on the Staatsgalerie
    # Raum13 set: genuine slight-blur fires ~0.4…0.6+, while weak directional-
    # texture fires (herringbone floors, windows, radiators) stay at 0.02…0.15,
    # so a floor at 0.20 sits in the gap.  Warning only (🟡), never a reject;
    # lower it (e.g. 0.10) to surface more candidates, raise to tighten.
    motion_blur_soft_check: bool = True
    soft_mb_floor: float = 0.20     # mb_penalty ≥ this → "Soft (Motion)"

    # --- ingestion ------------------------------------------------------
    # Decode very large files at reduced resolution (IMREAD_REDUCED_*).
    # The analysis only needs `analysis_dim` anyway – a 50 MP JPEG then
    # decodes ~4×/16× faster with no quality loss for the pipeline.
    reduced_decode: bool = True

    # --- photogrammetry checks (SfM-readiness) --------------------------
    # Feature density: Harris corner count per 1000 pixels of the
    # analysis image above the absolute response threshold (see
    # metrics.HARRIS_FEATURE_THRESHOLD).  Sharp images with almost no
    # usable structure (sky, plain wall, water, snow, glass) score low
    # here even though the sharpness score is high – exactly the "no
    # features found" case in Metashape.  Calibrated on real
    # photogrammetry sets: textured scenes measure ~2…35 corners/1k px,
    # empty scenes ~0.
    feature_check: bool = True
    min_feature_density: float = 2.0    # corners / 1k px
    min_feature_uniformity: float = 0.05  # min/max Harris ratio below this → "Uneven Features"

    # Vignetting: corner/centre brightness ratio below this → warning
    # (reduces effective SfM image area; dark corners carry fewer features)
    min_vignetting: float = 0.50       # < 0.50 → "Vignetting"

    # Pair-wise overlap: geometric overlap between consecutive images
    # (ORB + homography, analysis resolution).  Diagnostic only – useful
    # for film/frame sequences where a target subset overlap (e.g. 30 %)
    # is needed in a separate tool.
    overlap_check: bool = True

    # --- SIFT / feature-matching parameters (overlap) -----------------
    # These control the keypoint detection and matching quality for the
    # pair-wise overlap estimation.  Exposed in the UI as a compact
    # parameter bar so users can tune without editing code.
    sift_n_features: int = 5000       # SIFT keypoint count (500…10000)
    sift_ratio: float = 0.75          # Lowe ratio threshold (0.50…0.95)
    sift_ransac_px: float = 5.0       # RANSAC reprojection threshold (px)

    # Clipping: fraction of pixels that have lost information
    # (any channel ≥ 253 = clipped highlight, all channels ≤ 2 =
    # crushed shadow).  Clipped highlights are the classic drone
    # back-light killer for photogrammetry.
    #
    # Thresholds (calibrated on real sets):
    #   highlights 2 %  – blown highlights usually indicate a capture
    #                     mistake (back-lit drone shots); a sharp, well
    #                     textured thatch photo measured 0.65 %.
    #   shadows  25 %   – dark *materials* (thatch, bark, night scenes)
    #                     routinely carry 10–20 % crushed shadow without
    #                     being underexposed (a 0.988-scoring thatch
    #                     photo measured 18.7 %); a genuinely
    #                     underexposed photo usually has most of the
    #                     frame crushed (> 40 %).
    clip_check: bool = True
    max_clip_high: float = 0.02   # > 2 % clipped highlights → flag
    max_clip_low: float = 0.25    # > 25 % crushed shadows  → flag

    # --- exposure & white-balance consistency (Belichtung & WB) --------
    # The real SfM pain is rarely a single "too dark" photo – it is a set
    # whose photos do not AGREE (auto-exposure / auto-WB drift between
    # overlapping shots → seams, colour shifts, photometric-match failures).
    # So the per-image values below are always computed, and the *outlier*
    # flags compare each image to the set's robust median.
    #
    #   expo drift   – EV (from EXIF, exact) or luminance vs set median
    #   WB drift     – white-point gain vector (R/G, B/G) vs set median
    #   flat         – used tonal span (luma p95-p5) too small (washed out)
    #   Hi-ISO       – ISO well above the set median (noise + drift)
    #   Aperture Δ   – aperture far from the set median (DoF change)
    #
    # All are warnings (🟡), and the raw values are always exported so you
    # can re-calibrate these thresholds against Metashape / your own sets.

    exposure_consistency: bool = True
    exposure_outlier_ev: float = 0.5     # |ΔEV| vs set median (stops)  → "Expo drift" (EXIF path)
    exposure_outlier_luma: float = 0.25  # |Δluma|/median vs set median → "Expo drift" (no-EXIF fallback)

    low_dynamic_range_check: bool = True
    min_dynamic_range: float = 55.0      # luma p95-p5 below this → "Flat" (washed out)

    wb_consistency: bool = True
    wb_gain_dev_max: float = 0.15        # white-point gain distance vs set median → "WB drift"

    iso_consistency: bool = True
    iso_dev_stops_max: float = 2.0       # log2(iso / set-median-iso) above this → "Hi-ISO"

    aperture_consistency: bool = True
    aperture_dev_stops_max: float = 0.5  # log2(N / set-median-N) above this → "Aperture drift"


DEFAULT_CONFIG = AnalysisConfig()
