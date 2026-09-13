"""
IgorVision – Utility helpers
============================
"""
from __future__ import annotations

import os
from io import BytesIO
from pathlib import Path

import cv2
import numpy as np
from PIL import Image


# ---------------------------------------------------------------------------
# thumbnail
# ---------------------------------------------------------------------------

def create_thumbnail(bgr: np.ndarray, size: int = 128) -> bytes:
    """
    Create a PNG thumbnail from a BGR image.

    Returns ``b""`` on any failure (never raises).
    """
    try:
        rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
        pil = Image.fromarray(rgb)
        pil.thumbnail((size, size), Image.LANCZOS)
        buf = BytesIO()
        pil.save(buf, format="PNG", optimize=True)
        return buf.getvalue()
    except Exception:
        return b""


# ---------------------------------------------------------------------------
# file collection
# ---------------------------------------------------------------------------

IMAGE_EXTENSIONS: frozenset[str] = frozenset({
    ".jpg", ".jpeg", ".png", ".tif", ".tiff",
    ".bmp", ".gif", ".webp",
})


def collect_image_paths(
    selected_paths: list[str],
    recursive: bool = True,
) -> list[str]:
    """
    Expand *selected_paths* (files and/or folders) into a flat, de-duplicated
    list of image file paths.
    """
    seen: set[str] = set()
    paths: list[str] = []

    for p in selected_paths:
        obj = Path(p)
        candidates: list[Path] = []

        if obj.is_file():
            if obj.suffix.lower() in IMAGE_EXTENSIONS:
                candidates = [obj]
        elif obj.is_dir():
            pattern = "**/*" if recursive else "*"
            candidates = [
                f for f in obj.glob(pattern)
                if f.is_file() and f.suffix.lower() in IMAGE_EXTENSIONS
            ]

        for c in candidates:
            key = str(c.resolve()).lower()
            if key not in seen:
                seen.add(key)
                paths.append(str(c))

    return paths
