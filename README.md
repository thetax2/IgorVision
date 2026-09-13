# IgorVision – Image Quality Inspector

Bokeh-bewusster, modularer Bildqualitäts-Checker im Stil von
**Agisoft Metashape „Check image quality"**: erkennt unscharfe Bilder und
schlechte Belichtung – und klassifiziert Bilder mit **scharfem Objekt
und Bokeh-Hintergrund** korrekt als scharf.

```
python main.py
```

## Funktionsumfang

- Ordner- oder Dateiauswahl, rekursives Scannen optional
- CPU-Kerne-Slider (Multiprocessing via `ProcessPoolExecutor`)
- Fortschrittsbalken + Abbruch
- Ergebnistabelle mit Thumbnails, farbiger Score, Status
  (🟢 Sharp / 🔴 Blurry / 🟡 Soft (Motion) · Exposure · Clipping · Few Features
  · Duplicate · Expo drift · WB drift · Flat · Hi-ISO · Aperture drift), sortierbar
- **Photogrammetrie-Checks (SfM-Readiness)**:
  - **Feature-Dichte** (Harris-Kanten) – erkennt „scharf, aber ohne
    Struktur“ (Himmel, Wand, Wasser) wie Metashapes „no features found“
  - **Clipping** – überbelichtete Highlights / zugeknickte Schatten
  - **Duplikate** (MD5) – jede Datei nach der ersten der Gruppe wird
    markiert und verweist auf das Original
  - **Langzeiter-Bestätigung** aus EXIF (Verschlusszeit > 1/Brennweite)
- **Formate**: JPEG, PNG, **16-bit TIFF**, **RAW** (`.dng .cr2 .nef .arw …`,
  optional `rawpy`), **HEIC/HEIF** (optional `pillow-heif`)
- **EXIF**: Kamera, Blende, Verschluss, ISO, Brennweite, GPS, Datum –
  im Info-Panel + CSV; EXIF-Orientierung wird einmal angewendet
- Qualitäts-Filter (Schwellwert-Slider, „Blurry Only")
- **Batch-Statistik-Panel**: Status-Zählung, 24-Bin-Score-Histogram mit
  Threshold-Marker, Best/Worst-Bild (Klick → in Tabelle auswählen)
- **Preview-Paneel in voller Auflösung** mit Zoom-Steuerung:
  - `+` / `−` Zoom-Buttons, Mausrad-Zoom (unter dem Cursor)
  - `Zentrieren` (Bild zentrieren) und `Passend` (übersicht)
  - Zoom-Dropdown mit Presets **25 % / 50 % / 100 % / 200 % / 300 %**
    (100 % = 1 Bild-Pixel = 1 Bildschirm-Pixel)
- Preview-Paneel (Zoom/Shift-Scroll), Doppelklick = Vollbild-Dialog
- „Move to Keep/Reject Folder", „Open in Explorer", CSV-Export
  (55 Spalten inkl. aller Photogrammetrie-, Belichtung/WB- + EXIF-Felder)

## Screenshots

_TODO: 1–2 App-Screenshots (z. B. `docs/screenshot.png`) einfügen und hier
referenzieren – wichtig für die Produktseite._

## Architektur

| Datei | Rolle |
|---|---|
| `main.py` | Entry-Point, `QApplication` + `MainWindow` (UI wird lazy importiert – Worker-Prozesse laden kein PyQt) |
| `config.py` | **Alle** Tuning-Parameter (`AnalysisConfig`) |
| `models.py` | Datenklassen (`BlockMetrics`, `ImageQualityMetrics`), gemeinsamer `status_text` |
| `imageio.py` | **Ingestion** (Phase 1): Loader-Dispatch (cv2/pillow-heif/rawpy), EXIF-Parsing, 16-bit/RAW-Normalisierung, MD5, reduziertes Decode |
| `metrics.py` | Downsampling, Block-Sharpness (ein Laplacian-Pass), Motion-Blur-Merkmale, **Feature-Dichte** (Harris) + **Clip-Stats** (Phase 2), **Belichtung/WB** (Kelvin, EV, White-Point-Gain, Phase 3), globale Info-Metriken |
| `scoring.py` | Bokeh-bewusstes, **absolutes** Scoring (auflösungs-normalisiert) |
| `workers.py` | Multiprocessing-Pipeline, Duplikat-Markierung, Slow-Shutter, **Set-Konsistenz: Expo/WB/ISO/Blende** (Phase 3), Prognose-Callback, Stop-Event |
| `utils.py` | Thumbnails, Dateisammlung |
| `ui/main_window.py` | Hauptfenster, Worker-QThread, Aktionen, `build_export_rows`, CSV-Export, Stats-Wiring |
| `ui/table_model.py` | Ergebnistabelle (Farbcodierung, Status via `status_text`) |
| `ui/stats_panel.py` | **Batch-Statistik** (Phase 2): Zählung + 24-Bin-Histogram + Best/Worst |
| `ui/image_viewer.py` | Preview-Viewer (volle Auflösung, `zoom_in`/`zoom_out`/`zoom_to`/`center_image`/`reset_view`, `zoom_changed`-Signal) |
| `ui/preview_dialog.py` | Vollbild-Preview |

## Wie das Scoring funktioniert

1. Das Bild wird auf `analysis_dim` (Default **2048 px** lange Kante)
   heruntergerechnet.
2. Auf dem Graustufenbild wird **einmal** der Laplacian berechnet.
3. Das Bild wird in ein `grid_size × grid_size`-Raster (Default 8×8 =
   64 Blöcke) aufgeteilt; pro Block wird die **Laplacian-Varianz**
   gemessen (Scharfe = hohe Varianz).
4. **Subject Sharpness** = Mittelwert der scharfsten
   `topk_fraction`-Blöcke (Default: Top 10 % ≈ 6 Blöcke) – so trägt
   nur das scharfe Objekt zum Score bei, nicht der Bokeh-Hintergrund.
5. Der Wert wird auf die Referenzauflösung normisiert
   (Laplacian-Varianz skaliert ~ mit dem Quadrat der Auflösung) und
   log-linear auf 0…1 abgebildet:
   - `blur_floor` (Default 30)  → Score 0.0
   - `sharp_ref` (Default 600)  → Score 1.0
6. Leichter Noise-Penalty (Sensor-Rauschen ist Hochfrequenz und würde
   sonst als „Scharfe" zählen).
7. **Motion-Blur-Check (Kamerawackeln)** – zwei sich ergänzende,
   richtungs-agnostische Hinweise, die **beide** zutreffen müssen (AND):
   - `delta_conc` (spektral): Bewegungsunschärfe streicht die
     multi-richtigen Feindetails aus dem HF-Winkelprofil, während die
     texturalignierte Richtung bleibt → das HF-Band wird
     *konzentrierter* als das TF-Band.
   - `lag_aniso` (spatial): in der verwaschenen Richtung gehen
     HF-Energie in den scharfsten Blöcken verloren.
   Scharfe, stark texturierte Bilder (Stroh, Rinde, Schilf) können in
   *einem* Extrem liegen (Stroh → hohe `delta_conc`; Schilf → niedrige
   `lag_aniso`), aber nie in beiden – auf 4.241 scharfen Referenzbildern
   (Galli Colmap/Interior/Exterior + EichenHain) kalibriert:
   Nur beides zusammen → Penalty (bis zu `motion_blur_penalty_max`
   = 95 %).
8. Score < `blur_threshold` (Default 0.5) → **⚠️ Blurry**.
   - **Soft (Motion)** 🟡 – derselbe Motion-Blur-Detektor meldet *echte*
     (beide Hinweise, AND) aber noch zu schwache Unschärfe, um den Score
     unter `blur_threshold` zu drücken → das Bild würde sonst als „scharf“
     durchgehen. Genau die „minimal verrissen“-Aufnahmen. **Warnung (🟡),
     keine Ablehnung.** Schwellwert `soft_mb_floor` (Default **0.20**),
     Schalter `motion_blur_soft_check` (Default an).
9. Zusätzlich: Belichtungs-Check (Helligkeit/Kontrast) →
   **⚠️ Exposure** – wie bei Metashape.

Wichtig: Der Score ist **absolut** und batch-unabhängig – dasselbe Bild
bekommt in jedem Lauf denselben Wert (anders als bei relativer
Batch-Normalisierung).

## Photogrammetrie-Checks (SfM-Readiness)

Scharfe Bilder sind nicht automatisch gute SfM-Eingaben. Die Checks in
Phase 2 ergänzen den Scharfe-Score um die typischen „no features found“/
„overexposed“-Fehlerquellen von Metashape – unabhängig vom Scharfe-Score:

- **Feature-Dichte** – Anzahl nutzbbarer Harris-Kanten pro 1 000 Pixel
  (absolutes Antwort-Schwellen `HARRIS_FEATURE_THRESHOLD`). Ein scharfes
  Bild von Himmel, glatter Wand, Wasser, Schnee oder Glas hat fast keine
  matchbare Struktur und wird als **Few Features** markiert
  (`min_feature_density`, Default **2.0** / 1k px).
- **Clipping** – Anteil der Pixel mit verlorenem Informationsgehalt:
  - Highlights (ein Kanal ≥ 253) > `max_clip_high` (**2 %**) → Flag.
    Überbelichtete Drone-Backlight-Aufnahmen sind der Klassiker.
  - Schatten (alle Kanäle ≤ 2) > `max_clip_low` (**25 %**) → Flag. Die
    höhere Grenze verhindert False-Positives bei dunklen Materialien
    (Reet, Rinde, Nacht), die 10–20 % „crushed shadow“ ohne
    Unterbelichtung tragen.
- **Duplikate** – gleiche Datei (MD5) kommt mehrfach im Batch vor → jede
  Datei nach der ersten (pfad-sortiert) zeigt **Duplicate** + Original-Pfad.
- **Langzeiter-Bestätigung** – EXIF-Verschlusszeit länger als
  `1 / Brennweite` (z. B. 1/15 bei 24 mm) wird im Status als „· slow
  shutter“ ergänzt, wenn das Bild ohnehin unscharf ist. Kein eigenes
  Verdict (Stativ + 1/8 s ist in Ordnung) – nur eine zusätzliche Evidenz.

Alle vier Flags fließen in die **Status-Spalte** (`models.status_text`):
```
🟢 Sharp · 🔴 Blurry (+ Clipping / Few Features / Duplicate / · slow shutter) · 🟡 Soft (Motion) / …
```

## Belichtung & Weißabgleich (Phase 3)

Der häufigste echte SfM-Pain ist selten ein einzelnes „zu dunkles" Bild –
sondern ein **Satz**, dessen Bilder **nicht übereinstimmen**:
Auto-Belichtung / Auto-WB driftet zwischen überlappenden Aufnahmen →
Fugen, Farbsprünge, Photometric-Match-Failures. Deshalb wird pro Bild die
**Rohsignatur** gemessen und das *Outlier-Flag* vergleicht jedes Bild mit
dem robusten **Set-Median** (Median statt Mittelwert – unempfindlich gegen
die Ausreißer, die man ohnehin finden will).

Alle fünf Checks sind **Warnungen (🟡)**, keine harten Ablehnungen – und
die **Rohwerte werden immer exportiert** (CSV + Info-Panel), damit Sie die
Schwellen gegen Metashape / Ihre eigenen Sets kalibrieren können.

- **Expo drift** – Belichtungsdrift relativ zum Set.
  - Primär: **EXIF-EV (EV100, exakt)** mit `EV = log2(N² / (t · ISO/100))`.
    Gleiche Szene ⇒ gleiches EV100, Drift ist damit ein direkter
    Auto-Exposure-Signal. Braucht ≥ 3 Bilder mit EXIF.
  - Fallback (ohne EXIF): Luminanz-Median vs. Set-Median.
  - Schwellen: `exposure_outlier_ev` (**0.5** Stops) /
    `exposure_outlier_luma` (**0.25** relativ).
- **WB drift** – Weißabgleichsdrift via **White-Point-Gain-Vektor**
  (R/G, B/G aus dem 95.-Perzentil je Kanal – robust gegen einzelne
  überbelichtete Highlights). Euklidische Abweichung vom Set-Median >
  `wb_gain_dev_max` (**0.15**).
  - **Kelvin-Schätzung** (CIE-1931-Chromatizität + McCamy) dient als
    *Richtungskontrolle* – eine Näherung, kein Labor-Wert. Genau genug
    für Konsistenz, nicht für absolute Farbtemperatur.
- **Flat** – „used" Tonspanne (Luminanz p95−p5) unter
  `min_dynamic_range` (**55**) → ausgewaschen.
- **Hi-ISO** – ISO mehr als `iso_dev_stops_max` (**2.0** Stops) über dem
  Set-Median (Rauschen + Drift).
- **Aperture drift** – Blende mehr als `aperture_dev_stops_max` (**0.5**
  Stop) vom Set-Median (Schärfentiefe-Änderung → Fokus-/Bokeh-Mismatch).

Alle Toggles + Schwellen stehen in [`config.py`](config.py):
`exposure_consistency`, `wb_consistency`, `low_dynamic_range_check`,
`iso_consistency`, `aperture_consistency`.

## Kalibrierung (gegen Metashape)

Alle Werte stehen in [`config.py`](config.py) (`DEFAULT_CONFIG`).
Empfohlenes Vorgehen:

1. Einen Ordner mit 20–30 eigenen Fotos prüfen, die Sie selbst als
   scharf/unscharf einstufen (oder Metashapes Ergebnis als Referenz
   nehmen).
2. Die Rohwerte „Subject Sharpness (norm.)" im Info-Panel bzw. in der
   CSV-Spalte `peak_sharpness` vergleichen.
3. `blur_floor` / `sharp_ref` so setzen, dass Ihre Grenzen stimmen:
   - Werte scharfer Fotos liegen typisch bei **hundreds…thousands**
     (auflösungs-normalisiert).
   - Werte sicher unscharfer Fotos: **< 10–30**.
4. `blur_threshold` ist die Score-Grenze für das ⚠️-Flag.
5. `grid_size` (6/8/10) und `topk_fraction` (0.05–0.2) nur grob
   anpassen – sie ändern die Stabilität, nicht die Skala.
6. **Motion-Blur-Schwellen** (`mb_*`): gelten nur, wenn Ihr Material
   viel stark gerichtete Textur hat (Stroh, Rinde, Schilf). Die
   kalibrierten Defaults (verwackelt: `delta_conc` 23 % / `lag_aniso`
   0.14…0.15; nächstes scharfes Bild: 18 % / 0.21 – die Ramps liegen
   in dieser Lücke) sind:

   | Parameter | Default | Bedeutung |
   |---|---|---|
   | `mb_delta_good` | 0.15 | darunter: keine spektrale Evidenz |
   | `mb_delta_bad` | 0.20 | darüber: volle spektrale Evidenz |
   | `mb_lag_good` | 0.21 | darüber: keine richtungs-Evidenz |
   | `mb_lag_bad` | 0.10 | darunter: volle richtungs-Evidenz |
   | `motion_blur_penalty_max` | 0.95 | max. Score-Reduktion (× 1 − Wert) |
   | `motion_blur_soft_check` | `True` | Schalter für die „Soft (Motion)“-Warnung |
   | `soft_mb_floor` | 0.20 | `mb_penalty` ≥ Wert → **Soft (Motion)** 🟡 (Warnung, keine Ablehnung) |

   Der Penalty greift nur im **AND** beider Hinweise (Produkt der
   beiden Evidenz-Anteile). Rohwerte (`mb_delta_conc`, `mb_lag_aniso`)
   stehen im Info-Panel und in den CSV-Spalten – damit lassen sich
   eigene Schwellen gegen Metashape kalibrieren.

## Performance-Notizen

- 1 Laplacian-Pass + 64 günstige Varianz-Reduktionen pro Bild
  (statt 64 Kernel-Pässen).
- Analyse auf 2048 px statt 4096 px → ~4× weniger Pixelarbeit in den
  globalen Metriken (Sobel, Canny, HSV).
- Worker lesen direkt von Platte (kein RAM-Cache), Pfade mit
  Umlauten/Sonderzeichen werden korrekt decodiert
  (`np.fromfile` + `cv2.imdecode`).

## Abhängigkeiten

```bash
pip install -r requirements.txt
```

**Optionale Format-Backends** (Phase 1 – ohne sie laufen JPEG/PNG/TIFF
weiterhin, die betroffenen Formate zeigen nur einen freundlichen
„install …“-Hinweis statt einer Crash):

```bash
pip install rawpy pillow-heif
```

| Backend | Formate | Effekt |
|---|---|---|
| `rawpy` | `.dng .cr2 .nef .arw .orf …` | RAW-Decode + EXIF aus `raw.other`/`raw.lens` |
| `pillow-heif` | `.heic .heif` | iPhone/Android-Still-Decode |

## Repo-Struktur

| Ordner | Inhalt |
|---|---|
| `tools/` | Standalone-Kalibrierungs-/Diagnose-Skripte (nicht Teil der App) |
| `scratch/` | Lokale Kalibrierungs-Dumps & Debug-PNGs (nicht versioniert) |
| `legacy/` | Alte monolithische Version, nur noch zur Referenz |

## Lizenz & Versionshistorie

MIT – siehe [`LICENSE`](LICENSE). Änderungen in [`CHANGELOG.md`](CHANGELOG.md).
