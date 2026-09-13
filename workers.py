"""
IgorVision – Multiprocessing workers
====================================

Each worker decodes one image, computes block + global metrics and the
**absolute** quality score.  Results are final per image – there is no
batch-dependent normalisation (same semantics as Metashape's check).
"""
from __future__ import annotations

import logging
import math
import os
import threading
from concurrent.futures import ProcessPoolExecutor, as_completed
from typing import Callable, Optional

import cv2
import numpy as np

from config import DEFAULT_CONFIG
from imageio import load_image
from models import ImageQualityMetrics
from metrics import (
    compute_block_metrics,
    compute_clip_stats,
    compute_exposure_wb_metrics,
    compute_feature_density,
    compute_global_metrics,
    compute_motion_blur_features,
    compute_pair_overlap,
    compute_vignetting,
    detect_sift_keypoints,
    downsample,
    ev_from_exif,
)
from scoring import (
    apply_motion_blur_penalty,
    apply_noise_penalty,
    exposure_ok,
    score_from_blocks,
)
from utils import create_thumbnail

logger = logging.getLogger(__name__)


def _slow_shutter(exif: dict) -> bool:
    """
    EXIF corroboration for motion blur: shutter time longer than the
    reciprocal of the focal length (``t > 1/f``).  Not a verdict by
    itself (a slow shutter on a tripod is fine) – it sharpens the
    evidence when the pixel-based motion detector already fires.
    """
    shutter = exif.get("shutter")
    focal = exif.get("focal_length")
    if not shutter or not focal or focal <= 0:
        return False
    return bool(shutter > 1.0 / focal)


# ---------------------------------------------------------------------------
# single-image pipeline  (top-level for pickling)
# ---------------------------------------------------------------------------

def _analyze_single(path: str) -> ImageQualityMetrics:
    try:
        li = load_image(path)
        if li.bgr is None:
            logger.warning("Could not decode: %s (%s)", path, li.note)
            return _error_result(path, note=li.note, md5=li.md5)

        bgr = li.bgr
        h, w = bgr.shape[:2]
        # Green channel: highest spatial resolution in the Bayer pattern
        # (2× green pixels), best SNR, and immune to chromatic-aberration
        # edge smearing that affects the R+B-weighted luminance conversion.
        gray = bgr[:, :, 1].copy()

        # One downsampled pair for all analysis (fast + resolution-independent)
        gray_s = downsample(gray, DEFAULT_CONFIG.analysis_dim)
        bgr_s = downsample(bgr, DEFAULT_CONFIG.analysis_dim)
        longest = max(gray_s.shape[:2])

        # Block metrics (vectorised) + global display metrics (shared Laplacian)
        bm, lap = compute_block_metrics(gray_s, DEFAULT_CONFIG)
        gm = compute_global_metrics(gray_s, bgr_s, lap)

        # Absolute, bokeh-aware score + motion-blur (camera shake) penalty
        peak, spread, base = score_from_blocks(bm, DEFAULT_CONFIG, longest)
        score = apply_noise_penalty(base, gm["noise_level"], DEFAULT_CONFIG)
        delta_conc, lag_aniso = compute_motion_blur_features(gray_s)
        score, mb_penalty = apply_motion_blur_penalty(
            score, delta_conc, lag_aniso, DEFAULT_CONFIG
        )
        score = float(np.clip(score, 0.0, 1.0))
        # "Soft (Motion)" warning: real (both-cue) motion blur that is not yet
        # strong enough to drop the score below the blurry threshold.  Warning
        # only – surfaces "minimal verrissen" captures that would read "Sharp".
        soft_mb = bool(
            DEFAULT_CONFIG.motion_blur_soft_check
            and mb_penalty >= DEFAULT_CONFIG.soft_mb_floor
            and not (score < DEFAULT_CONFIG.blur_threshold)
        )

        # Photogrammetry checks: SfM feature richness + clipping + vignetting
        cfg = DEFAULT_CONFIG
        feat, feat_uniform = (
            compute_feature_density(gray_s) if cfg.feature_check else (0.0, 1.0)
        )
        low_features = bool(cfg.feature_check and feat < cfg.min_feature_density)
        uneven_features = bool(
            cfg.feature_check and feat_uniform < cfg.min_feature_uniformity
            and feat >= cfg.min_feature_density
        )
        vignetting = compute_vignetting(gray_s)
        has_vignetting = bool(vignetting < cfg.min_vignetting)
        clip_high, clip_low = (
            compute_clip_stats(bgr_s) if cfg.clip_check else (0.0, 0.0)
        )
        clipping_ok = bool(
            clip_high <= cfg.max_clip_high and clip_low <= cfg.max_clip_low
        )

        # Exposure & white-balance (Belichtung & WB) signatures
        ewb = compute_exposure_wb_metrics(gray_s, bgr_s)
        ev = ev_from_exif(li.exif)
        low_dr = bool(
            cfg.low_dynamic_range_check and ewb["dynamic_range"] < cfg.min_dynamic_range
        )

        try:
            fsize = os.path.getsize(path)
        except OSError:
            fsize = 0

        ow, oh = li.original_size if li.original_size else (w, h)
        return ImageQualityMetrics(
            path=path,
            peak_sharpness=peak,
            sharpness_spread=spread,
            laplacian_variance=gm["laplacian_variance"],
            sobel_variance=gm["sobel_variance"],
            sobel_var_x=gm["sobel_var_x"],
            sobel_var_y=gm["sobel_var_y"],
            sobel_aniso=gm["sobel_aniso"],
            tenengrad=gm["tenengrad"],
            gradient_p95=gm["gradient_p95"],
            edge_density=gm["edge_density"],
            noise_level=gm["noise_level"],
            contrast=gm["contrast"],
            brightness=gm["brightness"],
            saturation=gm["saturation"],
            composite_score=score,
            is_blurry=score < DEFAULT_CONFIG.blur_threshold,
            exposure_ok=exposure_ok(gm["brightness"], gm["contrast"], DEFAULT_CONFIG),
            mb_delta_conc=delta_conc,
            mb_lag_aniso=lag_aniso,
            mb_penalty=mb_penalty,
            soft_motion_blur=soft_mb,
            thumbnail_data=create_thumbnail(bgr, size=128),
            file_size=fsize,
            dimensions=(ow, oh),
            exif=li.exif,
            slow_shutter=_slow_shutter(li.exif),
            load_backend=li.backend,
            load_note=li.note,
            md5=li.md5,
            feature_density=feat,
            low_features=low_features,
            feature_uniformity=feat_uniform,
            uneven_features=uneven_features,
            vignetting=vignetting,
            has_vignetting=has_vignetting,
            clip_high=clip_high,
            clip_low=clip_low,
            clipping_ok=clipping_ok,
            luma_median=ewb["luma_median"],
            dynamic_range=ewb["dynamic_range"],
            wb_kelvin=ewb["wb_kelvin"],
            wb_gain_rg=ewb["wb_gain_rg"],
            wb_gain_bg=ewb["wb_gain_bg"],
            ev=ev,
            low_dynamic_range=low_dr,
        )

    except Exception:
        logger.exception("Analysis failed for %s", path)
        return _error_result(path)


def _error_result(path: str, note: str = "", md5: str = "") -> ImageQualityMetrics:
    return ImageQualityMetrics(
        path=path,
        peak_sharpness=0.0,
        sharpness_spread=0.0,
        laplacian_variance=0.0,
        sobel_variance=0.0,
        edge_density=0.0,
        noise_level=0.0,
        contrast=0.0,
        brightness=0.0,
        saturation=0.0,
        composite_score=0.0,
        is_blurry=True,
        exposure_ok=False,
        thumbnail_data=b"",
        file_size=0,
        dimensions=(0, 0),
        is_error=True,
        load_note=note,
        md5=md5,
    )


def _mark_duplicates(results: list[ImageQualityMetrics]) -> None:
    """
    Group results by file MD5; every file after the first (path-sorted)
    of a group gets ``duplicate_of`` set to that first path.
    """
    groups: dict[str, list[ImageQualityMetrics]] = {}
    for r in results:
        if r.md5:
            groups.setdefault(r.md5, []).append(r)
    for members in groups.values():
        if len(members) < 2:
            continue
        first = sorted(members, key=lambda r: r.path)[0]
        for r in members:
            if r is not first:
                r.duplicate_of = first.path


# ---------------------------------------------------------------------------
# exposure & white-balance consistency (Belichtung & WB)
# ---------------------------------------------------------------------------

def _median(xs: list[float]) -> float:
    """Robust median of a list (0.0 when empty)."""
    if not xs:
        return 0.0
    xs = sorted(xs)
    n = len(xs)
    m = n // 2
    return float(xs[m]) if n % 2 else (xs[m - 1] + xs[m]) / 2.0


def _mark_exposure_outliers(
    results: list[ImageQualityMetrics], cfg: "AnalysisConfig"
) -> None:
    """Flag images whose exposure deviates from the set's robust median.

    Prefers EXIF EV (exact, in stops) when at least three images have it;
    otherwise falls back to the luminance-median signature.
    """
    evs = [r.ev for r in results if r.ev and r.ev > 0]
    if len(evs) >= 3:
        med = _median(evs)
        for r in results:
            if r.ev and r.ev > 0:
                r.expo_dev = r.ev - med
                r.exposure_outlier = abs(r.expo_dev) > cfg.exposure_outlier_ev
        return
    lumas = [r.luma_median for r in results if r.luma_median > 0]
    if len(lumas) < 3:
        return
    med = _median(lumas)
    if med <= 0:
        return
    for r in results:
        if r.luma_median > 0:
            r.expo_dev = (r.luma_median - med) / med
            r.exposure_outlier = abs(r.expo_dev) > cfg.exposure_outlier_luma


def _mark_wb_outliers(
    results: list[ImageQualityMetrics], cfg: "AnalysisConfig"
) -> None:
    """Flag images whose white point deviates from the set's median gains."""
    if len(results) < 3:
        return
    med_rg = _median([r.wb_gain_rg for r in results])
    med_bg = _median([r.wb_gain_bg for r in results])
    for r in results:
        r.wb_dev = math.hypot(r.wb_gain_rg - med_rg, r.wb_gain_bg - med_bg)
        r.wb_outlier = r.wb_dev > cfg.wb_gain_dev_max


def _mark_iso_aperture(
    results: list[ImageQualityMetrics], cfg: "AnalysisConfig"
) -> None:
    """Flag ISO / aperture drift relative to the set's robust median."""
    if cfg.iso_consistency:
        isos = [r.exif.get("iso") for r in results if r.exif.get("iso")]
        if len(isos) >= 3:
            med = _median(isos)
            for r in results:
                iso = r.exif.get("iso")
                if iso and med > 0:
                    r.iso_dev_stops = math.log2(iso / med)
                    r.high_iso = r.iso_dev_stops > cfg.iso_dev_stops_max
    if cfg.aperture_consistency:
        aps = [r.exif.get("aperture") for r in results if r.exif.get("aperture")]
        if len(aps) >= 3:
            med = _median(aps)
            for r in results:
                ap = r.exif.get("aperture")
                if ap and med > 0:
                    r.aperture_dev_stops = math.log2(ap / med)
                    r.aperture_outlier = abs(r.aperture_dev_stops) > cfg.aperture_dev_stops_max


# ---------------------------------------------------------------------------
# pair-wise overlap estimation (ORB + homography, sequential)
# ---------------------------------------------------------------------------

def _compute_overlaps(
    results: list[ImageQualityMetrics],
    progress_callback: Optional[Callable[[int, int, str], None]] = None,
) -> None:
    """
    Estimate the geometric overlap between each image and its successor
    in path-sorted (capture) order.

    Uses ORB feature matching + RANSAC homography
    (``metrics.compute_pair_overlap``) on analysis-resolution green
    channels.  Sequential, keeps at most two images in memory.

    Results are stored on each image:
      - ``overlap_next``: 0…1 fraction of image area covered by next
      - ``overlap_matches``: good ORB matches
      - ``overlap_inlier_ratio``: RANSAC inlier ratio

    The last image in the sequence keeps the default ``overlap_next=-1``
    (no successor).  Performance: ~20-30 ms per pair → ~30-45 s for
    1500 frames.
    """
    valid = [r for r in results if not r.is_error]
    if len(valid) < 2:
        return
    valid.sort(key=lambda r: r.path)

    cfg = DEFAULT_CONFIG
    total = len(valid) - 1  # number of pairs
    prev_gray: Optional[np.ndarray] = None
    prev_result: Optional[ImageQualityMetrics] = None
    pair_idx = 0

    for r in valid:
        gray = None
        try:
            li = load_image(r.path)
            if li.bgr is not None:
                g = li.bgr[:, :, 1].copy()  # green channel
                gray = downsample(g, cfg.analysis_dim)
        except Exception:
            logger.warning("Overlap: failed to load %s", r.path)

        if prev_result is not None and prev_gray is not None and gray is not None:
            try:
                ov, matches, inlier = compute_pair_overlap(
                    prev_gray, gray,
                    n_features=cfg.sift_n_features,
                    ratio_thresh=cfg.sift_ratio,
                    ransac_px=cfg.sift_ransac_px,
                )
                prev_result.overlap_next = ov
                prev_result.overlap_matches = matches
                prev_result.overlap_inlier_ratio = inlier
            except Exception:
                logger.warning(
                    "Overlap: pair failed %s → %s", prev_result.path, r.path
                )
            pair_idx += 1
            if progress_callback:
                progress_callback(pair_idx, total, r.path)

        prev_gray = gray
        prev_result = r


# ---------------------------------------------------------------------------
# batch orchestration
# ---------------------------------------------------------------------------

def run_analysis(
    image_paths: list[str],
    num_workers: int,
    progress_callback: Optional[Callable[[int, int, str], None]] = None,
    overlap_callback: Optional[Callable[[int, int, str], None]] = None,
    stop_event: Optional[threading.Event] = None,
) -> list[ImageQualityMetrics]:
    """Analyse *image_paths* in parallel; results arrive in completion order."""
    if not image_paths:
        return []

    results: list[ImageQualityMetrics] = []
    total = len(image_paths)
    completed = 0

    with ProcessPoolExecutor(max_workers=max(1, num_workers)) as pool:
        future_map = {pool.submit(_analyze_single, p): p for p in image_paths}

        for fut in as_completed(future_map):
            if stop_event is not None and stop_event.is_set():
                for f in future_map:
                    f.cancel()
                break
            try:
                res = fut.result(timeout=300)
            except Exception as exc:
                logger.error("Worker error: %s", exc)
                res = _error_result(future_map[fut])

            results.append(res)
            completed += 1
            if progress_callback:
                progress_callback(completed, total, res.path)

    # duplicate detection across the batch (cheap, in-process)
    _mark_duplicates(results)
    # exposure & white-balance consistency across the batch
    if DEFAULT_CONFIG.exposure_consistency:
        _mark_exposure_outliers(results, DEFAULT_CONFIG)
    if DEFAULT_CONFIG.wb_consistency:
        _mark_wb_outliers(results, DEFAULT_CONFIG)
    if DEFAULT_CONFIG.iso_consistency or DEFAULT_CONFIG.aperture_consistency:
        _mark_iso_aperture(results, DEFAULT_CONFIG)
    # Overlap is NOT run here anymore – it runs in a separate visualisation
    # thread (OverlapScanWorker) after quality results are displayed.
    # This ensures the table is filled first, and the user gets per-image
    # SIFT keypoint feedback in the preview.
    return results


# ---------------------------------------------------------------------------
# standalone overlap scan worker (Qt thread, with keypoint visualisation)
# ---------------------------------------------------------------------------

class OverlapScanWorker:
    """
    Runs the pair-wise overlap scan in a separate thread, emitting
    keypoint visualisation data for the UI preview.

    Signals (connected by the main window):
      - keypoints_ready(path, kpts_xy, good_matches)
      - progress(completed, total, current_path)
      - finished(results)
    """

    def __init__(self, results: list[ImageQualityMetrics], stop_event: threading.Event):
        self._results = [r for r in results if not r.is_error]
        self._results.sort(key=lambda r: r.path)
        self._stop_event = stop_event
        # callbacks set by the main window
        self.on_keypoints = None   # (path, kpts_xy, good_matches) -> None
        self.on_progress = None    # (completed, total, path) -> None
        self.on_finished = None    # (results) -> None

    def run(self) -> None:
        cfg = DEFAULT_CONFIG
        valid = self._results
        total = len(valid) - 1
        if total < 1:
            if self.on_finished:
                self.on_finished(valid)
            return

        prev_gray = None
        prev_result = None
        pair_idx = 0

        for r in valid:
            if self._stop_event.is_set():
                break

            gray = None
            try:
                li = load_image(r.path)
                if li.bgr is not None:
                    g = li.bgr[:, :, 1].copy()
                    gray = downsample(g, cfg.analysis_dim)
            except Exception as e_load:
                print(f"[OVERLAP] LOAD FAIL: {r.path} -> {e_load}")

            if prev_result is not None and prev_gray is not None and gray is not None:
                try:
                    # Detect keypoints for visualisation
                    kpts, _ = detect_sift_keypoints(gray, cfg.sift_n_features)

                    # Compute overlap
                    ov, matches, inlier = compute_pair_overlap(
                        prev_gray, gray,
                        n_features=cfg.sift_n_features,
                        ratio_thresh=cfg.sift_ratio,
                        ransac_px=cfg.sift_ransac_px,
                    )
                    prev_result.overlap_next = ov
                    prev_result.overlap_matches = matches
                    prev_result.overlap_inlier_ratio = inlier

                    print(f"[OVERLAP] {os.path.basename(r.path)}: ov={ov:.3f} matches={matches} inlier={inlier:.3f} kpts={len(kpts)}")

                    # Emit visualisation (show keypoints on the NEXT image)
                    # .copy() ensures the buffer survives after this iteration
                    if self.on_keypoints and len(kpts) > 0:
                        self.on_keypoints(r.path, kpts.copy(), matches)

                except Exception as e:
                    import traceback
                    print(f"[OVERLAP] PAIR FAIL: {r.path} -> {e}")
                    traceback.print_exc()
            else:
                if pair_idx < 3:
                    print(f"[OVERLAP] SKIP: prev_result={prev_result is not None} prev_gray={prev_gray is not None} gray={gray is not None}")

            pair_idx += 1
            if self.on_progress:
                self.on_progress(pair_idx, total, r.path)

            prev_gray = gray
            prev_result = r

        if self.on_finished:
            self.on_finished(valid)
