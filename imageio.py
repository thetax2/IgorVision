"""
IgorVision – Image loading & EXIF
==================================

Backend dispatch by file type:

* ``.heic`` / ``.heif`` → **pillow-heif** (optional dependency)
* camera RAW (``.cr2 .nef .arw .dng …``) → **rawpy** (optional dependency)
* everything else → **OpenCV** (``np.fromfile`` + ``cv2.imdecode`` –
  non-ASCII path safe on Windows)

Guarantees
----------

* Always returns a ``uint8`` BGR array (16-bit TIFFs/RAW are
  normalised with ``>> 8`` so exposure semantics stay correct).
* EXIF orientation is applied (portrait photos are analysed upright).
* EXIF metadata is extracted (camera, aperture, **shutter**, ISO,
  focal length, GPS, datetime) and returned as a plain dict.
* The file's **MD5** is computed from the same bytes that are decoded
  (no extra disk read for the common cv2 path) → duplicate detection.
* Very large files are decoded at reduced resolution
  (``cv2.IMREAD_REDUCED_*``) – the pipeline only needs ``analysis_dim``
  anyway, so 50 MP+ JPEGs decode ~4–16× faster.
"""
from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass, field
from typing import Optional

import numpy as np

from config import DEFAULT_CONFIG

# ---------------------------------------------------------------------------
# result container
# ---------------------------------------------------------------------------


@dataclass
class LoadedImage:
    """Outcome of :func:`load_image` (never raises)."""

    bgr: Optional[np.ndarray] = None   # uint8 BGR, EXIF-rotated, or None
    exif: dict = field(default_factory=dict)
    backend: str = "cv2"               # cv2 | pillow-heif | rawpy
    note: str = ""                     # human note / fallback hint
    md5: str = ""                      # file hash ("" on hard failure)
    original_size: tuple[int, int] = (0, 0)  # (w, h) as stored in the file


# ---------------------------------------------------------------------------
# format tables
# ---------------------------------------------------------------------------

HEIC_EXTENSIONS = frozenset({".heic", ".heif"})

RAW_EXTENSIONS = frozenset({
    ".raw", ".dng",
    ".cr2", ".cr3", ".crw",          # Canon
    ".nef", ".nrw",                  # Nikon
    ".arw",                          # Sony
    ".orf",                          # Olympus
    ".rw2",                          # Panasonic
    ".raf",                          # Fuji
    ".srw",                          # Samsung
    ".pef",                          # Pentax
    ".mrw",                          # Minolta
    ".x3f",                          # Sigma
    ".3fr",                          # Hasselblad
    ".ari",                          # ARRI
    ".rfs",                          # Phase One
})


def is_raw(path: str) -> bool:
    return os.path.splitext(path)[1].lower() in RAW_EXTENSIONS


def is_heic(path: str) -> bool:
    return os.path.splitext(path)[1].lower() in HEIC_EXTENSIONS


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _md5_bytes(data: np.ndarray) -> str:
    return hashlib.md5(data.tobytes()).hexdigest()


def _md5_file(path: str) -> str:
    h = hashlib.md5()
    try:
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        return h.hexdigest()
    except OSError:
        return ""


def _to_uint8(img: np.ndarray) -> np.ndarray:
    """Normalise any common OpenCV dtype to 8-bit BGR.

    16-bit values are shifted right by 8 (full 16-bit range maps onto
    the 8-bit range) so brightness/exposure semantics stay meaningful.
    """
    if img.dtype == np.uint8:
        return img
    if img.dtype == np.uint16:
        return (img >> 8).astype(np.uint8)
    if img.ndim == 2:
        img = np.stack([img] * 3, axis=-1)
    lo, hi = int(img.min()), int(img.max())
    if hi - lo <= 0:
        return np.zeros(img.shape, dtype=np.uint8)
    scaled = (img.astype(np.float32) - lo) / (hi - lo) * 255.0
    return np.clip(scaled, 0, 255).astype(np.uint8)


def _visible_size(size: tuple[int, int], exif: dict) -> tuple[int, int]:
    """Report the *visible* (upright) photo dimensions.

    Stored JPEG/HEIC dimensions are pre-rotation; orientations 5–8 swap
    the axes, so we report what the user actually sees.
    """
    if not size or not size[0] or not size[1]:
        return (0, 0)
    if int(exif.get("orientation", 1) or 1) in (5, 6, 7, 8):
        return (size[1], size[0])
    return (size[0], size[1])


def _apply_orientation(img: np.ndarray, exif: dict) -> np.ndarray:
    """Rotate/flip according to EXIF orientation tag (1…8)."""
    o = int(exif.get("orientation", 1) or 1)
    if o == 1:
        return img
    import cv2
    if o == 2:
        return cv2.flip(img, 0)
    if o == 3:
        return cv2.rotate(img, cv2.ROTATE_180)
    if o == 4:
        return cv2.flip(img, 1)
    if o == 5:
        return cv2.flip(cv2.transpose(img), 1)
    if o == 6:
        return cv2.rotate(img, cv2.ROTATE_90_CLOCKWISE)
    if o == 7:
        return cv2.flip(cv2.rotate(img, cv2.ROTATE_180), 1)
    if o == 8:
        return cv2.rotate(img, cv2.ROTATE_90_COUNTERCLOCKWISE)
    return img


# ---------------------------------------------------------------------------
# EXIF parsing (Pillow)
# ---------------------------------------------------------------------------

def _num(v) -> Optional[float]:
    """EXIF values are often Fractions/IFDRational – normalise to float."""
    try:
        if v is None:
            return None
        f = float(v)
        return f if f > 0 else None
    except (TypeError, ValueError):
        return None


def _str(v) -> Optional[str]:
    if v is None:
        return None
    s = str(v).strip()
    return s or None


def _gps_coord(ref, val) -> Optional[float]:
    try:
        if ref is None or val is None:
            return None
        d = float(val[0])
        m = float(val[1])
        s = float(val[2])
        c = d + m / 60.0 + s / 3600.0
        return -c if str(ref).upper().startswith("S") or str(ref).upper().startswith("W") else c
    except (TypeError, ValueError, IndexError):
        return None


def parse_exif(exif) -> dict:
    """Extract the useful photogrammetry fields from a Pillow Exif object."""
    d: dict = {}
    if exif is None:
        return d
    try:
        d["make"] = _str(exif.get(0x010F))          # Make
        d["model"] = _str(exif.get(0x0110))         # Model
        d["orientation"] = int(exif.get(0x0112, 1) or 1)
    except Exception:
        pass
    try:
        ifd = exif.get_ifd(0x8769) or {}            # Exif IFD

        def _get(tag):
            # Some tools write these tags in the top-level dict instead
            # of the Exif IFD – fall back to the flat entry.
            return ifd.get(tag, exif.get(tag))

        d["aperture"] = _num(_get(0x829D))          # FNumber
        d["shutter"] = _num(_get(0x829A))           # ExposureTime (s)
        d["iso"] = _num(_get(0x8827))               # ISOSpeedRatings
        d["focal_length"] = _num(_get(0x920A))      # FocalLength (mm)
        dt = _get(0x9003)
        if dt:
            d["datetime"] = str(dt)
    except Exception:
        pass
    try:
        gps = exif.get_ifd(0x8825)
        if gps:
            lat = _gps_coord(gps.get(1), gps.get(2))
            lon = _gps_coord(gps.get(3), gps.get(4))
            if lat is not None and lon is not None:
                d["gps"] = (round(lat, 6), round(lon, 6))
    except Exception:
        pass
    return {k: v for k, v in d.items() if v not in (None, "")}


def _exif_via_pil(path: str) -> tuple[tuple[int, int], dict]:
    """Header-only read: original size + EXIF (fast, no full decode)."""
    try:
        from PIL import Image
        with Image.open(path) as im:
            size = im.size  # (w, h)
            exif_obj = im.getexif()
            exif = parse_exif(exif_obj) if exif_obj else {}
        return size, exif
    except Exception:
        return (0, 0), {}


# ---------------------------------------------------------------------------
# backends
# ---------------------------------------------------------------------------

def _load_cv2(path: str) -> LoadedImage:
    import cv2
    try:
        data = np.fromfile(path, dtype=np.uint8)
    except Exception:
        return LoadedImage(note="File not readable")
    if data.size == 0:
        return LoadedImage(note="Empty or unreadable file")

    md5 = _md5_bytes(data)
    size, exif = _exif_via_pil(path)

    # reduced decode for very large files (analysis needs analysis_dim only)
    longest = max(size) if size[0] and size[1] else 0
    flag = cv2.IMREAD_COLOR
    if DEFAULT_CONFIG.reduced_decode:
        if longest > 4 * DEFAULT_CONFIG.analysis_dim:
            flag = cv2.IMREAD_REDUCED_COLOR_4
        elif longest > 2 * DEFAULT_CONFIG.analysis_dim:
            flag = cv2.IMREAD_REDUCED_COLOR_2

    # OpenCV >= 4 auto-applies EXIF orientation while decoding JPEGs –
    # suppress that so _apply_orientation() below does it exactly once
    # (otherwise portrait photos end up double-rotated).
    if hasattr(cv2, "IMREAD_IGNORE_ORIENTATION"):
        flag |= cv2.IMREAD_IGNORE_ORIENTATION

    img = cv2.imdecode(data, flag)
    if img is None:
        return LoadedImage(note="OpenCV could not decode the image", md5=md5)

    img = _to_uint8(img)
    img = _apply_orientation(img, exif)
    return LoadedImage(
        bgr=img, exif=exif, backend="cv2", md5=md5,
        original_size=_visible_size(size, exif) or (img.shape[1], img.shape[0]),
    )


def _load_heic(path: str) -> LoadedImage:
    try:
        import pillow_heif
        pillow_heif.register_heif_opener()
    except ImportError:
        return LoadedImage(
            backend="pillow-heif",
            note="HEIC/HEIF: install pillow-heif  (pip install pillow-heif)",
        )
    try:
        from PIL import Image
        with Image.open(path) as im:
            size = im.size
            exif_obj = im.getexif()
            exif = parse_exif(exif_obj) if exif_obj else {}
            rgb = np.asarray(im.convert("RGB"))
    except Exception as exc:
        return LoadedImage(backend="pillow-heif", note=f"HEIC decode failed: {exc}")

    bgr = cv2_from_rgb(rgb)
    bgr = _apply_orientation(bgr, exif)
    return LoadedImage(
        bgr=bgr, exif=exif, backend="pillow-heif",
        note="HEIC (pillow-heif)", md5=_md5_file(path),
        original_size=_visible_size(size, exif) or (bgr.shape[1], bgr.shape[0]),
    )


def _load_raw(path: str) -> LoadedImage:
    try:
        import rawpy
    except ImportError:
        return LoadedImage(
            backend="rawpy",
            note="RAW: install rawpy  (pip install rawpy)",
        )
    try:
        with rawpy.imread(path) as raw:
            # rawpy >= 0.10 API (0.27.x): metadata in .other/.lens,
            # decode via postprocess() → uint8 array.
            other = getattr(raw, "other", None)
            lens = getattr(raw, "lens", None)
            exif = {
                "make": _str(getattr(lens, "make", None) or getattr(raw, "camera_make", None)),
                "model": _str(getattr(lens, "model", None) or getattr(raw, "camera_model", None)),
                "aperture": _num(getattr(other, "aperture", None) or getattr(raw, "aperture", None)),
                "shutter": _num(getattr(other, "shutter_speed", None) or getattr(raw, "shutter_speed", None)),
                "iso": _num(getattr(other, "iso_speed", None) or getattr(raw, "iso_speed", None)),
                "focal_length": _num(getattr(other, "focal_length", None) or getattr(raw, "focal_length", None)),
            }
            ts = getattr(other, "timestamp", None)
            if ts is not None:
                try:
                    exif["datetime"] = str(ts)
                except Exception:
                    pass
            if hasattr(raw, "postprocess"):
                img = raw.postprocess(
                    use_camera_wb=True,
                    output_color=rawpy.ColorSpace.sRGB,
                )
            else:  # rawpy < 0.10 fallback
                img = raw.read_raw(output_color=rawpy.ColorSpace.sRGB)
    except Exception as exc:
        return LoadedImage(backend="rawpy", note=f"RAW decode failed: {exc}")

    bgr = _to_uint8(img)
    return LoadedImage(
        bgr=bgr,
        exif={k: v for k, v in exif.items() if v not in (None, "")},
        backend="rawpy",
        note="RAW (rawpy)",
        md5=_md5_file(path),
        original_size=(bgr.shape[1], bgr.shape[0]),
    )


def cv2_from_rgb(rgb: np.ndarray) -> np.ndarray:
    """RGB→BGR without a hard cv2 import here (avoids circular surprises)."""
    import cv2
    return cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)


# ---------------------------------------------------------------------------
# public API
# ---------------------------------------------------------------------------

def load_image(path: str) -> LoadedImage:
    """
    Load *path* into a uint8 BGR array with EXIF metadata.

    Never raises – failures are reported via ``bgr is None`` + ``note``.
    """
    if is_heic(path):
        return _load_heic(path)
    if is_raw(path):
        return _load_raw(path)
    return _load_cv2(path)
