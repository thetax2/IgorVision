"""
IgorVision – Metric computation
===============================

Two categories of metrics:

1. **Block-based** (used for scoring) – per grid-cell Laplacian variance,
   so a sharp subject is not diluted by a blurred (bokeh) background.

2. **Global** (info panel only) – whole-image statistics.

Everything runs on **one** downscaled copy of the image
(``analysis_dim``, default 2048 px).

Performance
-----------
The full-image Laplacian is computed **once**; per-block variances are
then cheap array reductions.  This is measurably faster than running a
separate Laplacian per grid cell (the old 64-kernel-pass loop) and
slightly faster than sliding box filters on this hardware.
"""
from __future__ import annotations

import math

import cv2
import numpy as np

from config import AnalysisConfig
from models import BlockMetrics


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def downsample(image: np.ndarray, max_dim: int) -> np.ndarray:
    """Return *image* so its longest side is ≤ *max_dim* (no-op if smaller)."""
    h, w = image.shape[:2]
    longest = max(h, w)
    if longest <= max_dim:
        return image
    scale = max_dim / longest
    return cv2.resize(
        image,
        (max(1, int(w * scale)), max(1, int(h * scale))),
        interpolation=cv2.INTER_AREA,
    )


# ---------------------------------------------------------------------------
# block-based metrics (for scoring)
# ---------------------------------------------------------------------------

def compute_block_metrics(
    gray: np.ndarray,
    config: AnalysisConfig,
) -> tuple[BlockMetrics, np.ndarray]:
    """
    Per-block **sharpness** (Laplacian variance) for an
    ``grid_size × grid_size`` grid.

    The full-image Laplacian is computed **once** (single kernel pass);
    each block's variance is then a cheap reduction over that shared
    array.  Compared with the old approach (a separate Laplacian per
    block) this halves the work and removes the artificial block-border
    boundary effects.

    Returns ``(BlockMetrics, laplacian_array)`` – the Laplacian array is
    reused by :func:`compute_global_metrics` to avoid a second pass.
    """
    h, w = gray.shape[:2]
    g = max(2, int(config.grid_size))
    bh = max(1, h // g)
    bw = max(1, w // g)

    lap = cv2.Laplacian(gray, cv2.CV_32F)

    sharp = np.empty(g * g, dtype=np.float64)
    for i in range(g):
        y0, y1 = i * bh, min((i + 1) * bh, h)
        for j in range(g):
            x0, x1 = j * bw, min((j + 1) * bw, w)
            sharp[i * g + j] = float(lap[y0:y1, x0:x1].var())

    return BlockMetrics(sharpness=sharp), lap


# ---------------------------------------------------------------------------
# motion blur (camera shake) – direction-independent detection
# ---------------------------------------------------------------------------

MB_N_ANGLES = 48


def _angular_profile(gray: np.ndarray, n_angles: int = MB_N_ANGLES):
    """
    Angular energy profiles of the windowed 2-D power spectrum in two
    radial bands (absolute frequency, cycles/px):

      low band  [0.04, 0.10]  – mid-scale structure
      high band [0.15, 0.40]  – fine detail (chosen to avoid the JPEG
                                8-px block frequency 1/8 = 0.125)

    Returns two normalised profiles (each sums to 1), one entry per
    angle bin in [0, pi) – orientations only (|P| is symmetric).
    """
    h, w = gray.shape
    g = gray.astype(np.float64) - gray.mean()
    win = (np.hanning(h)[:, None] * np.hanning(w)[None, :]).astype(np.float64)
    F = np.fft.rfft2(g * win)
    P = F.real ** 2 + F.imag ** 2

    fy = np.fft.fftfreq(h)
    fx = np.fft.rfftfreq(w)
    FX, FY = np.meshgrid(fx, fy)
    R = np.hypot(FX, FY)
    TH = np.arctan2(FY, FX) % np.pi

    def profile(r0: float, r1: float) -> np.ndarray:
        mask = (R >= r0) & (R <= r1)
        if not mask.any():
            return np.zeros(n_angles)
        idx = np.clip(np.ceil(TH[mask] * n_angles).astype(np.int64) - 1,
                      0, n_angles - 1)
        p = np.bincount(idx, weights=P[mask], minlength=n_angles).astype(
            np.float64)
        s = p.sum()
        return (p / s) if s > 0 else np.zeros(n_angles)

    return profile(0.04, 0.10), profile(0.15, 0.40)


def _lag_anisotropy(gray: np.ndarray) -> float:
    """
    Lag-1 directional energy anisotropy on the top-k sharpest blocks.

    For each of the 4 cardinal/diagonal directions the mean squared
    intensity difference at lag 1 is accumulated over the sharpest ~10 %
    of the analysis grid (the same blocks that drive the sharpness
    score).  The result is min/max over directions:

      1.0  isotropic (sharp, no directional loss)
      ~0   one direction lost (motion blur OR strongly directional
           texture – therefore used as a confirmation gate together
           with ``delta_conc``, see
           :func:`scoring.apply_motion_blur_penalty`)
    """
    h, w = gray.shape
    g = 8
    bh, bw = max(1, h // g), max(1, w // g)
    lap = cv2.Laplacian(gray, cv2.CV_32F)
    sharp = np.empty(g * g, dtype=np.float64)
    for i in range(g):
        y0, y1 = i * bh, min((i + 1) * bh, h)
        for j in range(g):
            x0, x1 = j * bw, min((j + 1) * bw, w)
            sharp[i * g + j] = float(lap[y0:y1, x0:x1].var())

    k = max(2, int(round(g * g * 0.10)))
    sel = np.argsort(sharp)[::-1][:k]

    pooled = np.zeros(4)
    for bi in sel:
        i, j = divmod(int(bi), g)
        block = gray[i * bh:(i + 1) * bh,
                     j * bw:(j + 1) * bw].astype(np.float64)
        bh2, bw2 = block.shape
        for d, (dx, dy) in enumerate(((1, 0), (0, 1), (1, 1), (1, -1))):
            if bh2 - abs(dy) < 2 or bw2 - abs(dx) < 2:
                continue
            a = block[max(0, dy):bh2 - max(0, -dy),
                      max(0, dx):bw2 - max(0, -dx)]
            c = block[max(0, -dy):bh2 - max(0, dy),
                      max(0, -dx):bw2 - max(0, dx)]
            d2 = a - c
            pooled[d] += float((d2 * d2).mean())

    if pooled.max() < 1e-6:
        return 1.0
    return float(pooled.min() / pooled.max())


def compute_motion_blur_features(gray: np.ndarray) -> tuple[float, float]:
    """
    Two complementary, direction-agnostic motion-blur evidence values.

    Returns
    -------
    (delta_conc, lag_aniso)

    delta_conc (0…~0.5)
        Dominant-bin share of the **high** band minus that of the
        **low** band in the angular power profile.

        Physics: motion blur strips the multi-directional fine detail
        out of the high band while the texture-aligned direction
        survives, so the high band becomes *more* concentrated than the
        low band.  Sharp images (even strongly textured ones) show the
        opposite: fine detail is multi-directional, so their high band
        is flatter (observed sharp range ≈ −9 %…+13 %; the blurry
        reference image ≈ +23 %).

    lag_aniso (0…1)
        See :func:`_lag_anisotropy`.  Low when one direction loses
        high-frequency energy (the smear runs across it).  Also low
        for strongly directional *sharp* textures, so the scorer
        uses it only as a confirmation gate: the motion-blur penalty
        applies when **both** cues are extreme (AND – see
        :func:`scoring.apply_motion_blur_penalty`).
    """
    p_low, p_high = _angular_profile(gray)
    delta_conc = float(max(p_high) - max(p_low))
    lag_aniso = _lag_anisotropy(gray)
    return delta_conc, lag_aniso


# ---------------------------------------------------------------------------
# global metrics (display only)
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# photogrammetry checks (SfM-readiness)
# ---------------------------------------------------------------------------

# Absolute Harris-response threshold for a "usable" corner feature.
# Harris response scales with local contrast², so a fixed ABSOLUTE
# threshold (not a relative one – flat images have a near-zero response
# map where relative thresholds collapse to "everything is a corner")
# separates textured scenes from empty ones.  Calibrated on real
# photogrammetry sets: sharp textured scenes measure in the low
# single digits … tens of corners per 1k px at this threshold,
# empty scenes (sky, wall, water) measure ~0.
HARRIS_FEATURE_THRESHOLD = 0.0002


def compute_feature_density(gray: np.ndarray) -> tuple[float, float]:
    """
    Estimated usable feature-point density for SfM and its
    **spatial uniformity**.

    Returns
    -------
    (density, uniformity)

    density (corners / 1k px)
        Total Harris corners above :data:`HARRIS_FEATURE_THRESHOLD`
        per 1000 pixels.  Sharp but feature-poor images (sky, wall,
        water) score low here even with a high sharpness score.

    uniformity (0…1)
        Min/max ratio of corner counts across a 4×4 spatial grid.
        1.0 = perfectly even; 0.0 = all corners in one cell.
        Low values indicate concentrated features (e.g. a textured
        subject in the centre with empty corners) – bad for SfM
        coverage even when the total count is fine.
    """
    h, w = gray.shape[:2]
    px = float(h * w)
    if px < 64:
        return 0.0, 1.0
    resp = cv2.cornerHarris(gray, blockSize=5, ksize=3, k=0.04)
    n = float(np.count_nonzero(resp > HARRIS_FEATURE_THRESHOLD))
    density = float(n / (px / 1000.0))

    # --- 4×4 grid uniformity ---
    g = 4
    bh, bw = h // g, w // g
    counts = np.empty(g * g)
    for i in range(g):
        for j in range(g):
            cell = resp[i * bh:(i + 1) * bh, j * bw:(j + 1) * bw]
            counts[i * g + j] = np.count_nonzero(cell > HARRIS_FEATURE_THRESHOLD)
    uniformity = float(counts.min() / counts.max()) if counts.max() > 0 else 1.0
    return density, uniformity


def compute_vignetting(gray: np.ndarray) -> float:
    """
    Corner/center brightness ratio (1.0 = flat, < 0.5 = strong
    vignetting).  Uses 10 % corner patches vs the 50 % centre.

    Vignetting (lens or natural) reduces the effective image area
    for SfM feature matching: dark corners carry fewer usable
    features, and the photometric mismatch between the dark corners
    and the bright centre can confuse the Bundle Adjustment.
    """
    h, w = gray.shape[:2]
    ch = max(1, h // 10)
    cw = max(1, w // 10)
    corner_mean = float(np.mean([
        gray[:ch, :cw].mean(),
        gray[:ch, -cw:].mean(),
        gray[-ch:, :cw].mean(),
        gray[-ch:, -cw:].mean(),
    ]))
    cy0, cy1 = h // 4, h * 3 // 4
    cx0, cx1 = w // 4, w * 3 // 4
    centre_mean = float(gray[cy0:cy1, cx0:cx1].mean())
    if centre_mean < 1.0:
        return 1.0
    return corner_mean / centre_mean


def compute_clip_stats(bgr: np.ndarray) -> tuple[float, float]:
    """
    Fraction of pixels that have lost information:

    * **highlights** – pixel with *any* channel ≥ 253 (the channel is
      clipped; geometry/colour in that spot is unrecoverable).
    * **shadows** – pixel with *all* channels ≤ 2 (crushed black,
      heavy noise floor – weaker evidence, hence a looser default).

    Returns ``(clip_high, clip_low)`` as fractions in [0, 1].
    """
    n = float(bgr.shape[0] * bgr.shape[1])
    if n <= 0:
        return 0.0, 0.0
    cmax = bgr.max(axis=2)
    cmin = bgr.min(axis=2)
    clip_high = float(np.count_nonzero(cmax >= 253) / n)
    clip_low = float(np.count_nonzero(cmin <= 2) / n)
    return clip_high, clip_low


def compute_global_metrics(gray: np.ndarray, bgr: np.ndarray, lap: np.ndarray) -> dict[str, float]:
    """Whole-image statistics for the detail panel (computed once, on the
    analysis-resolution image)."""
    laplacian_variance = float(np.var(lap))

    sx = cv2.Sobel(gray, cv2.CV_64F, 1, 0, ksize=3)
    sy = cv2.Sobel(gray, cv2.CV_64F, 0, 1, ksize=3)
    sobel_variance = float(np.sqrt(sx * sx + sy * sy).var())

    # --- directional / gradient diagnostics (zero extra Sobel passes) ---
    sobel_var_x = float(sx.var())
    sobel_var_y = float(sy.var())
    sobel_aniso = (
        min(sobel_var_x, sobel_var_y) / max(sobel_var_x, sobel_var_y)
        if max(sobel_var_x, sobel_var_y) > 1e-6 else 1.0
    )
    tenengrad = float(np.mean(sx * sx + sy * sy))
    grad_mag = np.sqrt(sx * sx + sy * sy)
    gradient_p95 = float(np.percentile(grad_mag, 95))

    mu = float(np.mean(gray))
    sigma = float(np.std(gray))
    low_t = max(0, int(mu - sigma))
    high_t = min(255, int(mu + sigma))
    edges = cv2.Canny(gray, low_t, high_t)
    edge_density = float(np.count_nonzero(edges) / edges.size)

    # noise: median of the (Gaussian-blur) residual
    blurred = cv2.GaussianBlur(gray, (3, 3), 0)
    residual = gray.astype(np.float64) - blurred.astype(np.float64)
    noise_level = float(np.median(np.abs(residual)))

    contrast = sigma
    brightness = mu

    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
    saturation = float(np.mean(hsv[:, :, 1]))

    return {
        "laplacian_variance": laplacian_variance,
        "sobel_variance": sobel_variance,
        "sobel_var_x": sobel_var_x,
        "sobel_var_y": sobel_var_y,
        "sobel_aniso": sobel_aniso,
        "tenengrad": tenengrad,
        "gradient_p95": gradient_p95,
        "edge_density": edge_density,
        "noise_level": noise_level,
        "contrast": contrast,
        "brightness": brightness,
        "saturation": saturation,
    }


# ---------------------------------------------------------------------------
# exposure & white-balance (Belichtung & WB)
# ---------------------------------------------------------------------------

def _srgb_to_linear(c: float) -> float:
    c = min(max(c, 0.0), 1.0)
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def _kelvin_from_rgb(r: float, g: float, b: float) -> float:
    """
    Estimate colour temperature (Kelvin) of a (white-point) sRGB value via
    CIE-1931 chromaticity + the McCamy approximation.  Approximate in
    absolute terms, but directionally reliable – which is what the
    WB-consistency check needs (relative deviation within a set, not a
    lab-grade Kelvin).
    """
    R = _srgb_to_linear(r / 255.0)
    G = _srgb_to_linear(g / 255.0)
    B = _srgb_to_linear(b / 255.0)
    X = 0.4124 * R + 0.3576 * G + 0.1805 * B
    Y = 0.2126 * R + 0.7152 * G + 0.0722 * B
    Z = 0.0193 * R + 0.1192 * G + 0.9505 * B
    s = X + Y + Z
    if s <= 1e-9:
        return 6500.0
    x = X / s
    y = Y / s
    n = (x - 0.3320) / (0.1858 - y)
    k = 449.0 * n ** 3 - 3522.0 * n ** 2 + 10226.0 * n + 6550.0
    return min(max(k, 1500.0), 11000.0)


def compute_exposure_wb_metrics(gray: np.ndarray, bgr: np.ndarray) -> dict:
    """
    Exposure & white-balance signatures for consistency checks:

    * ``luma_median``   – robust central luminance (0..255)
    * ``dynamic_range`` – used tonal span (luma p95 - p5); small = washed out
    * ``wb_kelvin``     – estimated colour temperature of the scene white point
    * ``wb_gain_rg`` / ``wb_gain_bg`` – white-point R/G, B/G gains (WB signal)

    The white point is estimated from the 95th percentile of each channel
    (robust to a few clipped highlights).  These per-image values are
    compared to the *set* median in ``workers.run_analysis`` – relative
    deviation is the photogrammetry concern (seams / colour shifts), not
    the absolute value.
    """
    luma = gray.astype(np.float32)
    p05, p50, p95 = np.percentile(luma, [5, 50, 95])
    wr = float(np.percentile(bgr[:, :, 2], 95))   # R
    wg = float(np.percentile(bgr[:, :, 1], 95))   # G
    wb = float(np.percentile(bgr[:, :, 0], 95))   # B
    if wg <= 1e-6:
        rg = bg = 1.0
    else:
        rg = wr / wg
        bg = wb / wg
    return {
        "luma_median": float(p50),
        "dynamic_range": float(p95 - p05),
        "wb_kelvin": _kelvin_from_rgb(wr, wg, wb),
        "wb_gain_rg": float(rg),
        "wb_gain_bg": float(bg),
    }


def ev_from_exif(exif: dict) -> float:
    """
    Approximate scene EV (ISO-100 normalised) from EXIF aperture/shutter/ISO.
    A constant scene gives a constant EV100, so drift between shots of the
    same set is a direct auto-exposure signal.  Returns 0.0 when any value
    is missing (→ luminance-based fallback in the worker).
    """
    n = exif.get("aperture")
    t = exif.get("shutter")
    iso = exif.get("iso")
    if not n or not t or t <= 0 or not iso or iso <= 0:
        return 0.0
    return math.log2((n * n) / (t * (iso / 100.0)))


# ---------------------------------------------------------------------------
# pair-wise overlap estimation (SIFT/AKAZE + bidirectional homography)
# ---------------------------------------------------------------------------

# Minimum RANSAC inliers for a geometrically reliable 8-DOF homography.
# Below this, the result is meaningless (e.g. bark/grass scenes with
# only 4-8 inliers give a near-random homography).
MIN_RELIABLE_INLIERS = 8


def _grid_overlap(H: np.ndarray, h_src: int, w_src: int,
                  h_dst: int, w_dst: int, grid: int = 12) -> float:
    """Fraction of a grid×grid sampling of the source image that falls
    within the destination image after warping via homography H."""
    xs = np.linspace(0, w_src, grid + 1)[:-1].astype(np.float32)
    ys = np.linspace(0, h_src, grid + 1)[:-1].astype(np.float32)
    gx, gy = np.meshgrid(xs, ys)
    pts = np.column_stack([gx.ravel(), gy.ravel()]).astype(np.float32).reshape(-1, 1, 2)
    warped = cv2.perspectiveTransform(pts, H).reshape(-1, 2)
    inside = (
        (warped[:, 0] >= 0) & (warped[:, 0] <= w_dst) &
        (warped[:, 1] >= 0) & (warped[:, 1] <= h_dst)
    )
    return float(inside.sum()) / len(inside)


def _overlap_from_kpts(
    kp1, des1, kp2, des2,
    h1: int, w1: int, h2: int, w2: int,
    ratio_thresh: float = 0.75,
    ransac_thresh: float = 5.0,
) -> tuple[float, int, int]:
    """
    Compute overlap from two keypoint/descriptor sets using bidirectional
    RANSAC homography + grid sampling.  Returns (overlap, inliers, good).
    """
    bf = cv2.BFMatcher(cv2.NORM_L2)
    try:
        raw = bf.knnMatch(des1, des2, k=2)
    except cv2.error:
        return 0.0, 0, 0

    good = [m for m, n in raw if m.distance < ratio_thresh * n.distance]
    if len(good) < 4:
        return 0.0, len(good), 0

    pts1 = np.float32([kp1[m.queryIdx].pt for m in good]).reshape(-1, 1, 2)
    pts2 = np.float32([kp2[m.trainIdx].pt for m in good]).reshape(-1, 1, 2)

    # Direction A: warp img2 points into img1 space (H: img2 → img1)
    H_a, mask_a = cv2.findHomography(pts2, pts1, cv2.RANSAC, ransac_thresh)
    inliers_a = int(mask_a.sum()) if mask_a is not None else 0

    # Direction B: warp img1 points into img2 space (H: img1 → img2)
    H_b, mask_b = cv2.findHomography(pts1, pts2, cv2.RANSAC, ransac_thresh)
    inliers_b = int(mask_b.sum()) if mask_b is not None else 0

    # Compute overlap for each direction
    ov_a = _grid_overlap(H_a, h2, w2, h1, w1) if H_a is not None and inliers_a >= 4 else 0.0
    ov_b = _grid_overlap(H_b, h1, w1, h2, w2) if H_b is not None and inliers_b >= 4 else 0.0

    # Pick the direction with more inliers (more geometrically stable)
    if inliers_a >= inliers_b:
        best_ov, best_inl = ov_a, inliers_a
    else:
        best_ov, best_inl = ov_b, inliers_b

    # If BOTH directions are reliable, take the max overlap
    # (handles cases where one direction is slightly off)
    if inliers_a >= MIN_RELIABLE_INLIERS and inliers_b >= MIN_RELIABLE_INLIERS:
        best_ov = max(ov_a, ov_b)

    return best_ov, best_inl, len(good)


def compute_pair_overlap(
    gray1: np.ndarray,
    gray2: np.ndarray,
    n_features: int = 5000,
    ratio_thresh: float = 0.75,
    ransac_px: float = 5.0,
) -> tuple[float, int, float]:
    """
    Estimate the **geometric overlap** between two images using SIFT
    (primary) or AKAZE (fallback) feature matching with bidirectional
    RANSAC homography.

    Multi-strategy approach for robustness on repetitive textures
    (bark, grass, thatch):
      1. SIFT with ``n_features`` keypoints (default 5000)
      2. AKAZE fallback if SIFT yields < 20 good matches
      3. Bidirectional homography (use the direction with more inliers)
      4. Grid-sampling overlap (12×12 points, MAX of both directions)
      5. Reliability gate: < ``MIN_RELIABLE_INLIERS`` (8) inliers →
         returns 0.0 with low match count (UI shows "?")

    Parameters
    ----------
    gray1, gray2:
        Grayscale images (analysis resolution, uint8).
    n_features:
        Maximum SIFT features per image (5000 default).

    Returns
    -------
    (overlap, good_matches, inlier_ratio)

    overlap (0…1)
        Fraction of the image area geometrically covered by the other
        image.  1.0 = full overlap (near-duplicate), 0.0 = no overlap
        or **unreliable** (see good_matches < MIN_RELIABLE_INLIERS).

    good_matches
        Number of matches passing the ratio test.  Values below 8
        indicate the overlap is NOT reliable (UI displays "?").

    inlier_ratio (0…1)
        Fraction of good matches that are RANSAC inliers.
    """
    h1, w1 = gray1.shape
    h2, w2 = gray2.shape

    best_overlap = 0.0
    best_inliers = 0
    best_good = 0

    # --- Strategy 1: SIFT (primary) -----------------------------------
    sift = cv2.SIFT_create(nfeatures=n_features)
    kp1, des1 = sift.detectAndCompute(gray1, None)
    kp2, des2 = sift.detectAndCompute(gray2, None)

    if des1 is not None and des2 is not None and len(des1) >= 4 and len(des2) >= 4:
        ov, inl, good = _overlap_from_kpts(
            kp1, des1, kp2, des2, h1, w1, h2, w2,
            ratio_thresh=ratio_thresh, ransac_thresh=ransac_px,
        )
        best_overlap, best_inliers, best_good = ov, inl, good

    # --- Strategy 2: AKAZE fallback (if SIFT gave < 20 good matches) --
    if best_good < 20:
        try:
            akaze = cv2.AKAZE_create()
        except AttributeError:
            akaze = None  # OpenCV build without AKAZE (non-free module)

        if akaze is not None:
            akp1, ades1 = akaze.detectAndCompute(gray1, None)
            akp2, ades2 = akaze.detectAndCompute(gray2, None)

            if ades1 is not None and ades2 is not None and len(ades1) >= 4 and len(ades2) >= 4:
                ov, inl, good = _overlap_from_kpts(
                    akp1, ades1, akp2, ades2, h1, w1, h2, w2,
                    ratio_thresh=max(0.80, ratio_thresh),  # AKAZE: looser ratio
                    ransac_thresh=ransac_px,
                )
                if inl > best_inliers:
                    best_overlap, best_inliers, best_good = ov, inl, good

    # --- Reliability gate ---------------------------------------------
    if best_inliers < MIN_RELIABLE_INLIERS:
        return 0.0, best_good, (best_inliers / best_good) if best_good > 0 else 0.0

    inlier_ratio = best_inliers / best_good if best_good > 0 else 0.0
    return min(1.0, best_overlap), best_good, inlier_ratio


def detect_sift_keypoints(
    gray: np.ndarray,
    n_features: int = 5000,
) -> tuple[np.ndarray, np.ndarray]:
    """
    Detect SIFT keypoints and descriptors on a grayscale image.

    Returns
    -------
    (keypoints_xy, descriptors)
        keypoints_xy: Nx2 float32 array of (x, y) positions
        descriptors: Nx128 float32 array (or None if no features)
    """
    sift = cv2.SIFT_create(nfeatures=n_features)
    kps, des = sift.detectAndCompute(gray, None)
    if des is None or len(des) == 0:
        return np.empty((0, 2), dtype=np.float32), None
    kpts_xy = np.array([kp.pt for kp in kps], dtype=np.float32)
    return kpts_xy, des
