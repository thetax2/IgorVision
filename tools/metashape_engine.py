"""
core/metashape_engine.py
========================
Config + logic for the Metashape quality filter & RAW cleanup.
"""

from __future__ import annotations

import zipfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path

# ── Extensionen ──────────────────────────────────────────────────────────

RAW_EXTENSIONS: list[str] = [
    ".arw", ".dng", ".cr2", ".cr3", ".nef", ".raf", ".orf",
    ".rw2", ".sr2", ".pef", ".x3f", ".gpr", ".kdc",
]

JPG_EXTENSIONS: list[str] = [".jpg", ".jpeg"]


# ── Config ───────────────────────────────────────────────────────────────

@dataclass
class MetashapeConfig:
    """Configuration for a Metashape worker run."""
    mode: str = "load"          # "load" | "filter" | "cleanup"
    psx_path: str = ""          # Phase 1: .psx project file
    jpg_dir: str = ""           # Phase 1+2: JPG folder
    raw_dir: str = ""           # Phase 2: RAW folder
    threshold: float = 0.50     # Phase 1: quality threshold
    filter_mode: str = "trash"  # Phase 1: "trash" | "move" | "delete"


# ── Quality Reader ───────────────────────────────────────────────────────

@dataclass
class QualityEntry:
    camera_id: str
    filename: str
    quality: float


def read_quality_scores(project_path: str) -> list[QualityEntry]:
    """
    Reads Image/Quality from all frame.zip in <project>.files.
    Returns a list sorted by quality (ascending).
    """
    psx = Path(project_path)
    files_dir = psx.parent / (psx.stem + ".files")
    if not files_dir.exists():
        raise FileNotFoundError(f".files folder not found: {files_dir}")

    entries: list[QualityEntry] = []

    for fz in sorted(files_dir.rglob("frame.zip")):
        try:
            with zipfile.ZipFile(fz, "r") as zf:
                doc = next(
                    (n for n in zf.namelist() if n.endswith("doc.xml")), None
                )
                if not doc:
                    continue
                root = ET.fromstring(zf.read(doc))
        except Exception:
            continue

        for cam in root.findall(".//camera"):
            photo_el = cam.find("photo")
            path = photo_el.get("path", "") if photo_el is not None else ""

            quality = None
            for meta in cam.findall("meta"):
                for prop in meta.findall("property"):
                    if prop.get("name") == "Image/Quality":
                        try:
                            quality = float(prop.get("value"))
                        except (ValueError, TypeError):
                            pass
                        break
                if quality is not None:
                    break

            if quality is not None and path:
                entries.append(QualityEntry(
                    camera_id=cam.get("camera_id", "?"),
                    filename=Path(path).name,
                    quality=quality,
                ))

    entries.sort(key=lambda e: e.quality)
    return entries


# ── Orphan Detection ─────────────────────────────────────────────────────

def find_orphaned_raws(jpg_dir: str, raw_dir: str) -> list[Path]:
    """
    Returns RAW files for which no matching JPG exists
    (equality of the stems).
    """
    jpg_p = Path(jpg_dir)
    raw_p = Path(raw_dir)

    jpg_stems: set[str] = {
        f.stem for f in jpg_p.iterdir()
        if f.is_file() and f.suffix.lower() in JPG_EXTENSIONS
    }

    orphans: list[Path] = [
        f for f in raw_p.iterdir()
        if f.is_file() and f.suffix.lower() in RAW_EXTENSIONS
        and f.stem not in jpg_stems
    ]
    orphans.sort(key=lambda p: p.name)
    return orphans


# ── Trash / Move / Delete ────────────────────────────────────────────────

def remove_file(path: Path, mode: str) -> None:
    """Removes a file: trash | move (→ _removed/) | delete."""
    if mode == "trash":
        try:
            from send2trash import send2trash
            send2trash(str(path))
            return
        except ImportError:
            pass  # Fallback ↓

    if mode == "move":
        target_dir = path.parent / "_removed"
        target_dir.mkdir(exist_ok=True)
        target = target_dir / path.name
        # Avoid name conflict
        if target.exists():
            counter = 1
            while target.exists():
                target = target_dir / f"{path.stem}_{counter}{path.suffix}"
                counter += 1
        path.rename(target)
        return

    # mode == "delete" or fallback when send2trash is missing
    path.unlink()
