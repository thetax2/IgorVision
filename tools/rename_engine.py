"""
core/rename_engine.py
=====================
Data structures for the rename worker.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class RenameConfig:
    jpg_dirs: list[Path] = field(default_factory=list)
    raw_dirs: list[Path] = field(default_factory=list)
    target_dir: Path = Path(".")
    dry_run: bool = False
    create_backup: bool = True
    overwrite: bool = False
    raw_extensions: list[str] = field(
        default_factory=lambda: [".cr2", ".cr3", ".nef", ".arw",
                                ".dng", ".orf", ".pef", ".rw2", ".raf"]
    )
    jpg_extensions: list[str] = field(
        default_factory=lambda: [".jpg", ".jpeg"]
    )
    mode: str = "analyze"  # "analyze" | "execute"


@dataclass
class RenameMapping:
    base_name: str
    raw_filename: str
    raw_path: Path
    new_filename: str
    status: str = "pending"
