# Changelog

All notable changes to IgorVision. Format loosely follows
[Keep a Changelog](https://keepachangelog.com/) – date + short entries.

## Unreleased

### Tab structure – Overlap promoted to a top-level tab

- **Overlap is now a top-level tab** (`ui/main_window.py`): the
  sub-tab row inside the Quality page (Quality | Overlap) is gone.
  Top-level tabs are now **Quality · Overlap · Compare · Rename ·
  Sort · Metashape**.
- Quality and Overlap **share one analysis page** (toolbar, stats,
  preview, info tabs, settings panel) – only the left results view
  swaps.  Selecting an Overlap row still shows the image in the
  preview (keypoint visualisation included), and the settings panel
  still follows the active tab (quality parameters vs. SIFT /
  matching parameters).
- Implemented with `QTabBar` + `QStackedWidget` (a widget cannot live
  in two `QTabWidget` tabs at once); the tab bar reuses the existing
  `QTabBar` styling in `styles.py` (flat, accent underline).

### Settings panel – live tuning of all config parameters

- **New tab-aware settings panel** (`ui/settings_panel.py`) to the right
  of the image preview: the right side is now a horizontal splitter
  (preview | settings, both resizable + persisted). The panel content
  follows the active left tab:
  - **Quality** – all 28 numeric `config.py` parameters as slider +
    value-field rows, grouped (Resolution & Grid, Sharpness, Noise,
    Exposure, Motion Blur, Features & Vignetting, Clipping, Exposure &
    WB Consistency, Ingestion) + 10 boolean toggles as checkboxes.
  - **Overlap** – `sift_n_features` / `sift_ratio` / `sift_ransac_px`
    as plain value fields (same ranges as before).
- **Live apply** – every change is written into `DEFAULT_CONFIG`
  immediately (workers read it at run time); changing `blur_threshold`
  also moves the stats histogram threshold tick
  (`StatsPanel.set_threshold`).
- **Persistence** – values stored under `ui/config/<name>` in QSettings;
  *Reset to Defaults* restores the `AnalysisConfig` factory defaults
  (captured from a fresh instance, immune to live mutations).
- **Old SIFT parameter bar above the preview removed** – the same
  controls now live on the Overlap settings page (single source of
  truth); the redundant `DEFAULT_CONFIG.sift_*` push in
  `_start_analysis` is gone.
- `overlap_check` deliberately stays as the toolbar "Overlap-Check"
  checkbox (not duplicated in the panel).
- Sliders inherit the existing `QSlider { background: transparent; }`
  fix from `styles.py` – no gray box behind the new sliders.
- **`QScrollArea` theme fix** (`styles.py`): the scroll-area viewport
  (plain QWidget) was painted with the light Windows palette – a light
  box in the dark theme. Viewport + page are now `transparent` (scoped
  to `QScrollArea`, no global `QWidget` rule) so the tab-pane theme
  background shows through.

### GUI polish – sliders & dark-mode table readability

- **Slider "gray box" fixed** (`styles.py`): a gray rectangle was painted
  behind every `QSlider` (CPU Cores, Quality Threshold, Metashape
  threshold) in both themes. Root cause: without a `background`
  declaration on the `QSlider` widget itself, Qt paints the whole widget
  rectangle with the native Windows base color (`#efefef`). Setting
  `QSlider { background: transparent; }` makes the widget area truly
  transparent so the toolbar background shows through; the track is then
  defined purely by the sub-control rules (6 px groove, accent sub-page,
  theme-colored add-page, 14 px handle). Verified by pixel probes
  (above/below the track = fully transparent in dark and light).
- **Dark-mode table readability** (`ui/table_model.py`, `ui/main_window.py`):
  quality/status/overlap cells use light pastel fills (green/yellow/red)
  but inherited white dark-theme text. They now get explicit dark text
  (`#1c1f24`) so they are readable in both themes.

### English localization

- **Full German → English port** of all user-facing text, log messages,
  dialogs, status values, and code comments/docstrings across the whole
  project (UI tabs, workers, engines, `config.py` / `metrics.py` /
  `scoring.py`, `README.md`, `CHANGELOG.md`, `styles.py`).
- **Status values** unified to English and kept consistent between engine
  and UI: `missing` · `present` · `copied` · `moved` · `skipped` ·
  `unknown`; media type `photo` / `video`; date source `exif` /
  `filesystem`.
- German identifiers/comments cleaned up (e.g. local variable
  `aufnahme_files` → `shooting_files`, layout `pfade` → `paths_lay`).
- `differenzen_report.csv` filename kept (referenced in code + docs);
  only its CSV header row is now English.

### IGOR integration – file tools & design system

- **Four new top-level tabs** (integrated from the IGOR project, ported
  PySide6 → PyQt5):
  - **Compare** – Shooting days vs. Ingest_Backup reconciliation: missing
    media detection (name + size, optional SHA-256), EXIF metadata via
    ExifTool (chunked batches, **SQLite-cached** for incremental
    rescans), subfolder planning (date/focal/model/lens/media), rename
    with prefix/suffix/EXIF name parts, *Refresh* (replan without
    rescanning), manual Copy/Move transfer with CSV + JSON reports.
  - **Rename** – RAW→JPG name matching by camera base ID, analyze +
    execute (dry-run, overwrite).
  - **Sort** – Reality-Capture `.imagelist` → per-component folders
    (copy or move).
  - **Metashape** – `.psx` quality-score filter (threshold slider,
    trash/move/delete) + orphaned-RAW cleanup.
- **Design system** (`styles.py`): darktable-inspired **dark theme**
  (default) + light variant, applied app-wide; switchable under
  *Settings → Dark/Light Theme*, persisted via QSettings.
- New modules: `tools/` package (engines + workers), `ui/compare_tab.py`,
  `ui/rename_tab.py`, `ui/sort_tab.py`, `ui/metashape_tab.py`,
  `ui/log_panel.py`, `ui/path_row.py`.
- `send2trash` added as optional dependency (Metashape trash mode;
  falls back to a direct delete when missing).
- Fixed inherited `PathRow.set_path()` bug in the Metashape tab
  (now `set_value()`).

### Phase 1 – Ingestion & EXIF

- **RAW decoding** (`.dng .cr2 .nef .arw …`) via `rawpy` (optional dep,
  `postprocess()` → sRGB uint8; EXIF from `raw.other` / `raw.lens`).
- **HEIC/HEIF** via `pillow-heif` (optional dep); graceful "install …"
  fallback note when the backend is missing or the file is corrupt.
- **16-bit TIFF/RAW** normalised with `>> 8` so brightness/exposure
  semantics stay correct.
- **EXIF parsing** (Pillow, header-only): camera, aperture, **shutter**,
  ISO, focal length, GPS, datetime – surfaced in the info panel and CSV.
- **EXIF orientation applied** exactly once (portrait photos analysed
  upright); `cv2.IMREAD_IGNORE_ORIENTATION` added to prevent the double
  rotation OpenCV ≥ 4 otherwise performs on JPEGs.
- **Slow-shutter corroboration**: flags `shutter > 1 / focal_length`
  to sharpen motion-blur evidence (shown when a photo is also blurry).
- **Fast decode** of very large files via `cv2.IMREAD_REDUCED_COLOR_*`
  (50 MP JPEGs decode ~4–16× faster; pipeline only needs `analysis_dim`).
- **MD5** computed from the decoded bytes (no extra read on the cv2 path)
  → duplicate detection across the batch.

### Phase 2 – SfM / photogrammetry checks

- **Feature-density check** (Harris corners / 1 k px above an absolute
  response threshold) – catches "sharp but feature-poor" images (sky,
  plain wall, water, snow) that are useless for the reconstruction.
- **Clipping check** – fraction of clipped highlights (any channel ≥ 253)
  and crushed shadows (all channels ≤ 2).
- **Duplicate (MD5) detection** – every file after the first of a hash
  group is flagged and points at the original path.
- **Batch statistics panel** – status counts, 24-bin score histogram with
  threshold tick, and best/worst image (click → select in table).
- **Status column** now combines blur + exposure + clipping + few features
  + duplicate + slow shutter (shared `models.status_text`).
- `build_export_rows()` extracted (Qt-free, testable) and shared by the
  CSV export and the upcoming CLI/JSON output; CSV now carries all
  quality, photogrammetry and EXIF fields (39 columns).
- Tuning: `max_clip_low` raised 5 % → **25 %** (dark materials like thatch
  carry 10–20 % crushed shadow without being underexposed);
  `max_clip_high` stays 2 %.

### Preview & zoom

- Preview now loads the **full-resolution** file (was: 128 px table
  thumbnail) so zooming stays sharp; very large files are decoded with a
  soft cap to keep memory/decode time sane.
- Zoom toolbar above the preview: `+` / `−` buttons, **Center**
  (re-centre), **Fit** (fit to view), and a zoom **dropdown** with
  presets **25 % / 50 % / 100 % / 200 % / 300 %** (100 % = 1:1 pixels).
- `ImageViewer` gains `zoom_in` / `zoom_out` / `zoom_to` / `center_image`
  / `reset_view` / `current_zoom_percent` and a `zoom_changed` signal that
  keeps the combo in sync; mouse-wheel zoom stays anchored under the cursor.

### Phase 3 – Exposure & White Balance (Exposure & WB)

- **Exposure consistency** (`Expo drift`): per-image EXIF **EV100**
  (`EV = log2(N² / (t · ISO/100))`) vs. the set's robust median (≥ 3
  images with EXIF), with a **luminance-median fallback** when EXIF is
  missing. Direct auto-exposure drift signal.
- **White-balance consistency** (`WB drift`): white-point gain vector
  (R/G, B/G from the per-channel 95th percentile – robust to clipped
  highlights) vs. the set median; plus a **Kelvin estimate** (CIE-1931
  chromaticity + McCamy) as a *directional* sanity check – an estimate,
  not a lab value.
- **Low dynamic range** (`Flat`): used tonal span (luma p95−p5) below
  `min_dynamic_range` → washed out.
- **ISO / aperture drift** (`Hi-ISO`, `Aperture drift`): relative to the
  set median, in stops (noise + DoF/bokeh mismatch).
- All five are **warnings (🟡)**, not hard rejects; **raw values always
  exported** (CSV + info panel) for calibration. CSV grows to **54 columns**.
- New `status_text` flags: Expo drift · WB drift · Flat · Hi-ISO · Aperture
  drift; stats panel gains `expoΔ` / `wbΔ` / `isoΔ` chips; table tooltip
  adds `≈ K · Δexpo · ΔWB` and the warn-background now covers the new flags.
- Config: `exposure_consistency` / `wb_consistency` /
  `low_dynamic_range_check` / `iso_consistency` / `aperture_consistency`
  + thresholds (`exposure_outlier_ev` 0.5, `exposure_outlier_luma` 0.25,
  `min_dynamic_range` 55, `wb_gain_dev_max` 0.15, `iso_dev_stops_max` 2.0,
  `aperture_dev_stops_max` 0.5).

### Phase 4 – "Soft (Motion)" warning tier

- New **additive "Soft (Motion)" 🟡 warning** for images the motion-blur
  detector flags as *real* (both cues, AND) but not strong enough to push
  the score below `blur_threshold` – captures that are **"slightly
  smeared"** (slight camera shake at capture) and would otherwise still
  read "Sharp". **Warning only, never a reject.**
- Calibrated on the Staatsgalerie Raum13 set (536 Canon 45 MP images):
  genuine slight-blur fires ~0.47 while weak directional-texture fires
  (herringbone floors, windows, radiators) stay at 0.15 or below – the
  floor sits in that gap, so exactly one image is surfaced and the 27
  genuinely-blurry images are unchanged.
- Config: `motion_blur_soft_check` (default `True`) + `soft_mb_floor`
  (default **0.20**). Lower the floor (e.g. `0.10`) to surface more
  candidates; raise it to tighten.
- UI: status column shows "Soft (Motion)" (🟡); the info panel shows it on
  the motion-blur line; the stats panel gains a `soft` chip and excludes
  soft images from the clean count; table rows get the warn background and
  the tooltip now carries the MB penalty. CSV gains a `soft_motion_blur`
  column (55 columns total); the raw `mb_*` values are unchanged.

### Phase 5 – Green Channel & Gradient Diagnostics

- **Green-channel analysis**: all sharpness/blur/gradient metrics now use
  the green channel (`bgr[:, :, 1]`) instead of the ITU-R BT.601 luminance
  blend. The green channel carries 2× the Bayer spatial samples, the best
  SNR, and is immune to chromatic-aberration edge smearing that can
  falsely flatten high-contrast edges in the luminance conversion.
- **Sobel anisotropy** (`sobel_aniso`): X/Y Sobel-variance ratio
  (1 = isotropic/sharp, 0 = fully directional). Shown in the Quality tab
  (Motion Blur section, with a ⚠️ directional warning below 0.2), the
  Global tab, the preview dialog, the table tooltip, and the CSV export.
  Complements the existing `lag_aniso` with a Sobel-based measure.
- **Tenengrad** (`tenengrad`): mean Sobel gradient energy (Gx² + Gy²),
  the classic focus measure that Agisoft Metashape's "Estimate Image
  Quality" approximates. Shown in the Quality tab (Sharpness section)
  and Global tab; CSV export.
- **Gradient p95** (`gradient_p95`): 95th-percentile gradient magnitude –
  robust peak-detail metric (unlike variance, unaffected by a few extreme
  outlier pixels). Quality tab + Global tab + CSV.
- **Sobel X/Y variance** (`sobel_var_x`, `sobel_var_y`): raw per-axis
  gradient variance for diagnosis and calibration. Global tab + CSV.
- CSV export grows to **60 columns**.

### Phase 6 – Extended SfM Checks

- **Feature distribution uniformity** (`feature_uniformity`): min/max ratio
  of Harris corner counts across a 4×4 spatial grid (1.0 = perfectly even).
  Low values indicate features concentrated in one area (e.g. textured subject
  in the centre, empty corners) – bad for SfM coverage even when the total
  count is fine. Shown in the SfM-Checks tab; "Uneven Features" warning
  below `min_feature_uniformity` (default 0.05).
- **Vignetting** (`vignetting`): corner/centre brightness ratio
  (1.0 = flat, < 0.5 = strong vignetting).  Reduces the effective SfM image
  area – dark corners carry fewer usable features and the photometric
  mismatch can confuse Bundle Adjustment. Shown in the SfM-Checks tab;
  "Vignetting" warning below `min_vignetting` (default 0.50).
- Both are **warnings (🟡)**, not hard rejects; raw values always exported
  (CSV + info panel) for calibration.
- `compute_feature_density` now returns `(density, uniformity)` from a
  single Harris pass (no duplicate computation).
- CSV export grows to **64 columns**.

### Phase 7 – Pair-wise Overlap Estimation

- **Geometric overlap** (`overlap_next`, `overlap_matches`,
  `overlap_inlier_ratio`): SIFT feature matching (2000 features,
  FLANN KDTREE, analysis-resolution green channel) + Lowe ratio test
  (0.75) + RANSAC homography (5 px) → **12×12 grid-sampling** overlap.
  The fraction of warped grid points falling inside the target image
  is the overlap measure.  SIFT over ORB: far more stable keypoints
  on real-world scenes (bark, grass, architecture) where ORB yields
  too few matches for a reliable homography.  Last frame = n/a.
- **Use case**: film/frame sequences (e.g. 25 fps drone capture) where
  consecutive frames carry > 90 % overlap.  The metric feeds a
  separate tool that selects a subset at a target overlap (e.g. 30 %).
- **Diagnostic only** – no score impact, no status flag.  Shown in
  the SfM-Checks tab, the preview dialog info bar, the table tooltip,
  and the CSV export (67 columns).
- **Two-phase progress**: quality metrics run first (parallel, progress
  bar 0→100 %), then the overlap check (sequential, progress bar resets
  to 0→100 % with "Overlap: n/total" label).  The overlap checkbox in
  the toolbar toggles the second phase independently.
- **Overlap tab** (left panel): new tab next to "Quality" showing all
  frames in capture (path-sorted) order with columns: #, Filename,
  Overlap %, Matches, Inlier %.  Overlap cells are colour-coded:
  red < 30 % (too little for SfM), green 30–90 % (sweet spot),
  blue > 90 % (highly redundant).  Row selection loads the image into
  the preview + info panel.
- **Left panel tabs**: the table area is now a `QTabWidget` with
  "Quality" (existing quality-sorted table + filters) and "Overlap"
  (capture-ordered sequence).  Both tabs share the right-side preview.
- **Performance**: ~350–400 ms per pair (SIFT + FLANN) → ~75–85 s
  for 210 frames, ~2 min for 1500 frames.  Sequential, at most two
  images in memory.  Disabled via `overlap_check = False` in `config.py`
  or by unchecking the toolbar checkbox.
- Config: `overlap_check` (default `True`); UI state persisted via
  QSettings (`ui/overlap_check`).

### Repo hygiene & settings

- Repo hygiene: debug scripts → `tools/`, calibration dumps & crop PNGs →
  `scratch/`, legacy monolith → `legacy/ImageCheck.py`.
- Removed unused `psutil` dependency (only the legacy file used it).
- Added `.gitignore`, `LICENSE`, `CHANGELOG.md`.
- Settings persistence: window geometry, splitter sizes, CPU cores,
  quality threshold, filter mode, recursive toggle, and the last used
  folder survive an app restart (QSettings).

## 5.0.0 – 2026-09-12

- Bokeh-aware **absolute** scoring (top-k block sharpness,
  resolution-normalised, log-linear 0…1 mapping).
- Motion-blur (camera shake) detection via AND of spectral
  (`delta_conc`) and spatial (`lag_aniso`) cues; calibrated on
  4.241 reference images (Galli Colmap/Interior/Exterior + EichenHain).
- Noise penalty, Metashape-style exposure check (brightness/contrast).
- Multiprocessing pipeline (`ProcessPoolExecutor`) with progress,
  cancellation, per-image error rows.
- PyQt5 UI: sortable results table with thumbnails, quality filter,
  zoom/pan preview, fullscreen dialog, Keep/Reject move, CSV export.
- Non-ASCII (umlaut) path support via `np.fromfile` + `cv2.imdecode`.
