# IgorVision – Image Quality Inspector

Bokeh-aware, modular image quality checker for photogrammetry workflows.
Detects blurry images, poor exposure, and misaligned sets – while
correctly classifying images with a **sharp subject and bokeh
background** as sharp.

```
python main.py
```

## Features

- Folder or file selection, recursive scanning (optional)
- CPU-core slider (multiprocessing via `ProcessPoolExecutor`)
- Progress bar + abort
- Results table with thumbnails, colour-coded score, status
  (🟢 Sharp / 🔴 Blurry / 🟡 Soft (Motion) · Exposure · Clipping · Few Features
  · Duplicate · Expo drift · WB drift · Flat · Hi-ISO · Aperture drift), sortable
- **Photogrammetry checks (SfM-readiness)**:
  - **Feature density** (Harris edges) – detects "sharp but structureless"
    images (sky, wall, water, snow) that would fail feature matching
  - **Clipping** – overexposed highlights / crushed shadows
  - **Duplicates** (MD5) – every file after the first in a group is
    flagged and linked back to the original
  - **Slow-shutter flag** from EXIF (shutter speed > 1/focal-length)
- **Formats**: JPEG, PNG, **16-bit TIFF**, **RAW** (`.dng .cr2 .nef .arw …`,
  optional `rawpy`), **HEIC/HEIF** (optional `pillow-heif`)
- **EXIF**: camera, aperture, shutter, ISO, focal length, GPS, date –
  in info panel + CSV; EXIF orientation applied once
- Quality filters (threshold slider, "Blurry Only")
- **Batch statistics panel**: status counts, 24-bin score histogram with
  threshold marker, best/worst image (click → select in table)
- **Full-resolution preview panel** with zoom controls:
  - `+` / `−` zoom buttons, mouse-wheel zoom (anchored under cursor)
  - **Centre** (re-centre image) and **Fit** (overview)
  - Zoom dropdown with presets **25 % / 50 % / 100 % / 200 % / 300 %**
    (100 % = 1 image pixel = 1 screen pixel)
- Preview panel (zoom / Shift-scroll), double-click = full-screen dialog
- "Move to Keep / Reject Folder", "Open in Explorer", CSV export
  (55 columns including all photogrammetry, exposure/WB, and EXIF fields)

## Screenshots

_TODO: add 1–2 app screenshots (e.g. `docs/screenshot.png`) and reference them here._

## Architecture

| File | Role |
|---|---|
| `main.py` | Entry point, `QApplication` + `MainWindow` (UI is lazily imported – worker processes don't load PyQt) |
| `config.py` | **All** tuning parameters (`AnalysisConfig`) |
| `models.py` | Data classes (`BlockMetrics`, `ImageQualityMetrics`), shared `status_text` |
| `imageio.py` | **Ingestion** (Phase 1): loader dispatch (cv2 / pillow-heif / rawpy), EXIF parsing, 16-bit/RAW normalisation, MD5, reduced decode |
| `metrics.py` | Downsampling, block sharpness (single Laplacian pass), motion-blur features, **feature density** (Harris) + **clip stats** (Phase 2), **exposure / WB** (Kelvin, EV, white-point gain, Phase 3), global info metrics |
| `scoring.py` | Bokeh-aware, **absolute** scoring (resolution-normalised) |
| `workers.py` | Multiprocessing pipeline, duplicate marking, slow-shutter flag, **set consistency: exposure / WB / ISO / aperture** (Phase 3), progress callback, stop event |
| `utils.py` | Thumbnails, file collection |
| `ui/main_window.py` | Main window, worker QThread, actions, `build_export_rows`, CSV export, stats wiring |
| `ui/table_model.py` | Results table (colour coding, status via `status_text`) |
| `ui/stats_panel.py` | **Batch statistics** (Phase 2): counts + 24-bin histogram + best/worst |
| `ui/image_viewer.py` | Preview viewer (full resolution, `zoom_in` / `zoom_out` / `zoom_to` / `center_image` / `reset_view`, `zoom_changed` signal) |
| `ui/preview_dialog.py` | Full-screen preview |

## How the Scoring Works

1. The image is downsampled to `analysis_dim` (default **2048 px** longest side).
2. A **single** Laplacian pass is computed on the greyscale image.
3. The image is split into a `grid_size × grid_size` grid (default 8×8 =
   64 blocks); the **Laplacian variance** is measured per block
   (sharp = high variance).
4. **Subject sharpness** = mean of the sharpest `topk_fraction` blocks
   (default: top 10 % ≈ 6 blocks) – only the sharp subject contributes
   to the score, not the bokeh background.
5. The value is normalised to the reference resolution
   (Laplacian variance scales ~ with the square of the resolution) and
   mapped log-linearly to 0…1:
   - `blur_floor` (default 30)  → score 0.0
   - `sharp_ref` (default 600)  → score 1.0
6. Light noise penalty (sensor noise is high-frequency and would
   otherwise count as "sharpness").
7. **Motion-blur check (camera shake)** – two complementary,
   direction-agnostic indicators, **both** must be present (AND):
   - `delta_conc` (spectral): motion blur smears multi-directional
     fine detail out of the HF angle profile while the texture-aligned
     direction remains → the HF band becomes *more concentrated* than
     the TF band.
   - `lag_aniso` (spatial): in the blur direction, HF energy is lost
     in the sharpest blocks.
   Sharp, strongly textured images (straw, bark, reeds) can sit at one
   extreme (straw → high `delta_conc`; reeds → low `lag_aniso`) but
   never at both – calibrated on 4,241 sharp reference images
   (Galli Colmap/Interior/Exterior + EichenHain):
   only both together → penalty (up to `motion_blur_penalty_max`
   = 95 %).
8. Score < `blur_threshold` (default 0.5) → **⚠️ Blurry**.
   - **Soft (Motion)** 🟡 – the same motion-blur detector reports *genuine*
     (both indicators, AND) but not-yet-strong-enough blur to push the
     score below `blur_threshold` → the image would otherwise pass as
     "sharp". These are the "slightly missed" shots. **Warning (🟡),
     not a rejection.** Threshold `soft_mb_floor` (default **0.20**),
     toggle `motion_blur_soft_check` (default on).
9. Additionally: exposure check (brightness / contrast) →
   **⚠️ Exposure**.

Important: the score is **absolute** and batch-independent – the same image
gets the same value in every run (unlike relative batch normalisation).

## Photogrammetry Checks (SfM-Readiness)

Sharp images are not automatically good SfM input. The Phase 2 checks
complement the sharpness score with typical "no features found" /
"overexposed" failure modes – independent of the sharpness score:

- **Feature density** – count of usable Harris edges per 1 000 pixels
  (absolute response threshold `HARRIS_FEATURE_THRESHOLD`). A sharp image
  of sky, smooth wall, water, snow, or glass has almost no matchable
  structure and is flagged as **Few Features**
  (`min_feature_density`, default **2.0** / 1k px).
- **Clipping** – fraction of pixels with lost information:
  - Highlights (any channel ≥ 253) > `max_clip_high` (**2 %**) → flag.
    Overexposed drone backlight shots are the classic case.
  - Shadows (all channels ≤ 2) > `max_clip_low` (**25 %**) → flag. The
    higher threshold prevents false positives on dark materials
    (thatch, bark, night scenes) that naturally carry 10–20 %
    "crushed shadow" without underexposure.
- **Duplicates** – identical file (MD5) appears multiple times in the
  batch → every file after the first (path-sorted) shows **Duplicate**
  + original path.
- **Slow-shutter flag** – EXIF shutter speed longer than
  `1 / focal_length` (e.g. 1/15 at 24 mm) is appended as "· slow
  shutter" in the status if the image is already flagged as blurry.
  No separate verdict (tripod + 1/8 s is fine) – just additional evidence.

All four flags flow into the **status column** (`models.status_text`):
```
🟢 Sharp · 🔴 Blurry (+ Clipping / Few Features / Duplicate / · slow shutter) · 🟡 Soft (Motion) / …
```

## Exposure & White Balance (Phase 3)

The most common real-world SfM issue is rarely a single "too dark" image –
it's a **set** whose images **don't agree**:
auto-exposure / auto-WB drifts between overlapping shots → seams, colour
jumps, photometric match failures. For each image the **raw signature**
is measured and the *outlier flag* compares it against the robust
**set median** (median instead of mean – resilient to the very outliers
you're trying to find).

All five checks are **warnings (🟡)**, not hard rejections – and the
**raw values are always exported** (CSV + info panel) so you can
calibrate thresholds against your own sets.

- **Expo drift** – exposure drift relative to the set.
  - Primary: **EXIF EV (EV100, exact)** via `EV = log2(N² / (t · ISO/100))`.
    Same scene ⇒ same EV100, so drift is a direct auto-exposure signal.
    Requires ≥ 3 images with EXIF.
  - Fallback (no EXIF): luminance median vs. set median.
  - Thresholds: `exposure_outlier_ev` (**0.5** stops) /
    `exposure_outlier_luma` (**0.25** relative).
- **WB drift** – white-balance drift via **white-point gain vector**
  (R/G, B/G from the 95th percentile per channel – robust against
  individual overexposed highlights). Euclidean deviation from set
  median > `wb_gain_dev_max` (**0.15**).
  - **Kelvin estimate** (CIE-1931 chromaticity + McCamy) serves as a
    *direction check* – an approximation, not a lab value. Accurate
    enough for consistency, not for absolute colour temperature.
- **Flat** – "used" tonal range (luminance p95−p5) below
  `min_dynamic_range` (**55**) → washed out.
- **Hi-ISO** – ISO more than `iso_dev_stops_max` (**2.0** stops) above
  the set median (noise + drift).
- **Aperture drift** – aperture more than `aperture_dev_stops_max` (**0.5**
  stop) from the set median (depth-of-field change → focus / bokeh mismatch).

All toggles + thresholds are in [`config.py`](config.py):
`exposure_consistency`, `wb_consistency`, `low_dynamic_range_check`,
`iso_consistency`, `aperture_consistency`.

## Sequential Overlap Scan (SIFT / AKAZE)

For photogrammetry, consecutive images in a capture sequence should
overlap. IgorVision can scan an entire batch for **pair-wise geometric
overlap** – useful for verifying coverage before importing into an
SfM pipeline.

### How it works

1. Images are sorted by path and processed **sequentially** (image *i*
is compared against image *i−1*).
2. For each pair, the pipeline runs:
   - **SIFT** keypoint detection (default **5 000** features per image)
   - **Lowe ratio test** (default threshold **0.75**) to filter matches
   - **Bidirectional RANSAC homography** – both directions (1→2 and
     2→1) are estimated; the one with more inliers is used
   - **12 × 12 grid-sampling** of the homography to measure the
     fraction of image area geometrically covered by the other image
   - **AKAZE fallback** – if SIFT yields < 20 good matches, AKAZE
     is tried with a looser ratio threshold (0.80)
3. **Reliability gate**: fewer than **8 RANSAC inliers** → overlap is
   reported as **0.0** (UI displays "?") to avoid false positives on
   repetitive textures (bark, grass, thatch).

### Output per pair

| Value | Range | Meaning |
|---|---|---|
| `overlap_next` | 0.0 – 1.0 | Fraction of image area covered by the next image (1.0 ≈ near-duplicate) |
| `overlap_matches` | int | Good matches after ratio test (< 8 → unreliable) |
| `overlap_inlier_ratio` | 0.0 – 1.0 | Fraction of good matches that are RANSAC inliers |

### Live visualisation

While the scan runs, the UI preview shows **SIFT keypoints** (red dots
with white ring) on each image as it is processed, so you can see
where features were detected and how the overlap is computed.

### Configuration

All parameters are in [`config.py`](config.py):

| Parameter | Default | Description |
|---|---|---|
| `sift_n_features` | 5000 | Max SIFT keypoints per image |
| `sift_ratio` | 0.75 | Lowe ratio test threshold |
| `sift_ransac_px` | 5.0 | RANSAC reprojection threshold (pixels) |

## Calibration

All values are in [`config.py`](config.py) (`DEFAULT_CONFIG`).
Recommended workflow:

1. Run a folder of 20–30 of your own photos that you've rated as
   sharp / blurry yourself.
2. Compare the raw "Subject Sharpness (norm.)" values in the info
   panel or the `peak_sharpness` CSV column.
3. Set `blur_floor` / `sharp_ref` so your boundaries are right:
   - Sharp photos typically sit in the **hundreds…thousands**
     (resolution-normalised).
   - Definitely blurry photos: **< 10–30**.
4. `blur_threshold` is the score boundary for the ⚠️ flag.
5. `grid_size` (6/8/10) and `topk_fraction` (0.05–0.2) are coarse
   adjustments only – they change stability, not the scale.
6. **Motion-blur thresholds** (`mb_*`): only relevant if your material
   has lots of strongly directional texture (straw, bark, reeds). The
   calibrated defaults (blurry: `delta_conc` 23 % / `lag_aniso`
   0.14…0.15; next sharpest image: 18 % / 0.21 – the ramps sit in
   that gap) are:

   | Parameter | Default | Meaning |
   |---|---|---|
   | `mb_delta_good` | 0.15 | below: no spectral evidence |
   | `mb_delta_bad` | 0.20 | above: full spectral evidence |
   | `mb_lag_good` | 0.21 | above: no direction evidence |
   | `mb_lag_bad` | 0.10 | below: full direction evidence |
   | `motion_blur_penalty_max` | 0.95 | max. score reduction (× 1 − value) |
   | `motion_blur_soft_check` | `True` | toggle for the "Soft (Motion)" warning |
   | `soft_mb_floor` | 0.20 | `mb_penalty` ≥ value → **Soft (Motion)** 🟡 (warning, not rejection) |

   The penalty only applies when **both** indicators fire (product of
   the two evidence fractions). Raw values (`mb_delta_conc`,
   `mb_lag_aniso`) are in the info panel and CSV columns – use them
   to calibrate your own thresholds.

## Performance Notes

- 1 Laplacian pass + 64 cheap variance reductions per image
  (instead of 64 kernel passes).
- Analysis at 2048 px instead of 4096 px → ~4× less pixel work in the
  global metrics (Sobel, Canny, HSV).
- Workers read directly from disk (no RAM cache); paths with
  umlauts / special characters are decoded correctly
  (`np.fromfile` + `cv2.imdecode`).

## Dependencies

```bash
pip install -r requirements.txt
```

**Optional format backends** (Phase 1 – without them JPEG/PNG/TIFF
still work; the affected formats show a friendly "install …" hint
instead of crashing):

```bash
pip install rawpy pillow-heif
```

| Backend | Formats | Effect |
|---|---|---|
| `rawpy` | `.dng .cr2 .nef .arw .orf …` | RAW decode + EXIF from `raw.other` / `raw.lens` |
| `pillow-heif` | `.heic .heif` | iPhone/Android still decode |

## License & Version History

MIT – see [`LICENSE`](LICENSE). Changes in [`CHANGELOG.md`](CHANGELOG.md).