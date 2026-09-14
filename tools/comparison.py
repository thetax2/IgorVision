"""
core/comparison.py
==================
Business logic: scanning, indexing, comparing, copying.
No GUI dependencies. Pure functions + dataclasses.
"""

from __future__ import annotations

import csv
import hashlib
import json
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable, Generator, Optional
import tempfile

from tools.db import ScanDB, scan_key_for

# ── Constants ─────────────────────────────────────────────────────────────────

DEFAULT_EXTENSIONS = frozenset({
    ".cr3", ".cr2", ".arw", ".nef", ".dng", ".raf", ".orf", ".rw2",
    ".jpg", ".jpeg", ".mp4", ".mov",
})

VIDEO_EXTENSIONS = frozenset({".mp4", ".mov"})

_CHUNK = 4 * 1024 * 1024  # 4 MB
_INVALID_CHARS = '<>:"/\\|?*'

# Order = priority. First tag with a valid value wins.
_EXIF_DATE_TAGS = ["-DateTimeOriginal", "-CreateDate", "-MediaCreateDate"]
_EXIF_META_TAGS = ["-Model", "-LensModel", "-LensID", "-LensSpec", "-FocalLength"]


# ── Data structure ────────────────────────────────────────────────────────────

@dataclass
class FileEntry:
    path: Path
    name_lower: str
    size: int
    sha256: Optional[str] = None


@dataclass
class MetaInfo:
    date_str: str          # YYYY-MM-DD (EXIF oder mtime)
    date_source: str       # "exif" | "filesystem" | "unknown"
    focal: str = ""        
    model: str = ""        
    lens: str = ""         
    media: str = ""        


@dataclass
class SubfolderConfig:
    date: bool = False
    focal: bool = False
    model: bool = False
    lens: bool = False
    media: bool = False    


@dataclass
class NamePartConfig:
    date: bool = False      
    model: bool = False     
    lens: bool = False      
    focal: bool = False     
    separator: str = "_"    


@dataclass
class DiffResult:
    """A missing file + its metadata + destination path."""
    entry: FileEntry
    meta: MetaInfo
    dest_path: Path
    status: str  # "missing" | "present" | "copied" | "moved" | "skipped"


# ── Errors ────────────────────────────────────────────────────────────────────

class CancelledError(Exception):
    pass


# ── File traversal ────────────────────────────────────────────────────────────

def iter_media_files(
    root: Path,
    extensions: frozenset[str],
) -> Generator[Path, None, None]:
    """Yield all media files under `root`, recursively."""
    for p in root.rglob("*"):
        if not p.is_file():
            continue
        if p.suffix.lower() not in extensions:
            continue
        yield p


def is_within(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        pass
    pp = path.parts
    np_ = parent.parts
    if len(pp) < len(np_):
        return False
    return [x.lower() for x in pp[: len(np_)]] == [x.lower() for x in np_]


# ── Hashing ───────────────────────────────────────────────────────────────────

def sha256_of(path: Path, cancel_flag: Optional[object] = None) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(_CHUNK):
            if cancel_flag is not None and cancel_flag.is_set():
                raise CancelledError()
            h.update(chunk)
    return h.hexdigest()


def build_index(
    files: list[Path],
    use_hash: bool,
    cancel_flag: Optional[object],
    progress_cb: Optional[Callable[[int, int], None]] = None,
    db: Optional[ScanDB] = None,
    scan_key: Optional[str] = None,
    tree: str = "import",
) -> tuple[dict[str, list[FileEntry]], int]:
    """Index: lowercased-filename → [FileEntry, …] → (index, reused_hashes).

    With `db`, the index runs incrementally: files whose size and mtime
    are unchanged and for which a SHA-256 already exists in the index
    are not re-hashed. At the end, the DB state is brought up to date
    with the disk state (new/changed rows are upserted, deleted ones removed).
    """
    index: dict[str, list[FileEntry]] = {}
    total = len(files)
    reused = 0
    cached: dict[str, dict] = {}
    if db is not None and scan_key:
        cached = db.load_tree(scan_key, tree)

    entries: list[tuple] = []
    for i, p in enumerate(files, 1):
        if cancel_flag is not None and cancel_flag.is_set():
            raise CancelledError()
        try:
            st = p.stat()
        except OSError:
            continue
        e = FileEntry(path=p, name_lower=p.name.lower(), size=st.st_size)
        old = cached.get(str(p))
        unchanged = (
            old is not None
            and old["size"] == st.st_size
            and old["mtime"] == st.st_mtime
        )
        if use_hash:
            if unchanged and old["sha256"]:
                e.sha256 = old["sha256"]
                reused += 1
            else:
                e.sha256 = sha256_of(p, cancel_flag)
        index.setdefault(e.name_lower, []).append(e)
        # DB state: keep the current hash, otherwise the old one (if hashing
        # is off now, the DB should retain it for a later hash scan).
        db_hash = e.sha256
        if db_hash is None and unchanged:
            db_hash = old["sha256"]
        entries.append((str(p), e.name_lower, e.size, st.st_mtime, db_hash))
        if progress_cb:
            progress_cb(i, total)

    if db is not None and scan_key:
        db.sync_tree(scan_key, tree, entries)

    return index, reused


# ── Match logic ───────────────────────────────────────────────────────────────

def matches_existing(
    backup: FileEntry,
    candidates: list[FileEntry],
    use_hash: bool,
) -> bool:
    for c in candidates:
        if c.size != backup.size:
            continue
        if use_hash and backup.sha256 != c.sha256:
            continue
        return True
    return False


# ── ExifTool (direct query without JSON overhead) ─────────────────────────────

def check_exiftool(binary: str) -> bool:
    try:
        subprocess.run([binary, "-ver"], capture_output=True, check=True, timeout=5)
        return True
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return False


def exiftool_version(binary: str) -> str:
    """Return the ExifTool version string (e.g. ``12.95``) or ``""``."""
    if not binary:
        return ""
    try:
        proc = subprocess.run(
            [binary, "-ver"], capture_output=True, text=True, timeout=5)
        if proc.returncode == 0:
            return proc.stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        pass
    return ""


def detect_exiftool() -> str:
    """Find ExifTool in PATH and in common install locations.

    Returns the full path or ``""`` when nothing was found.  ExifTool is
    a Perl script wrapper: on Windows the ``exiftool(-k).exe`` from the
    zip release is renamed to ``exiftool.exe`` and works standalone, so
    any of those locations is a valid candidate.
    """
    found = shutil.which("exiftool")
    if found:
        return found
    for cand in (
        r"C:\Tools\exiftool.exe",
        r"C:\Program Files\exiftool\exiftool.exe",
        r"C:\Program Files (x86)\exiftool\exiftool.exe",
    ):
        if Path(cand).is_file():
            return cand
    return ""


def sanitize_part(value: object) -> str:
    s = "".join("_" if c in _INVALID_CHARS else c for c in str(value or ""))
    return s.strip().rstrip(".")


def format_focal(raw: object) -> str:
    s = str(raw or "").strip().replace(",", ".")
    if not s:
        return ""
    s = re.sub(r"(?i)\s*mm$", "", s).strip()
    s = s.split()[0] if s.split() else ""
    try:
        f = float(s)
        num = str(int(f)) if f == int(f) else f"{f:g}"
        return f"{num}mm"
    except ValueError:
        return sanitize_part(s)


# Chunk size for ExifTool calls: a timeout thus affects at most ONE
# chunk and not the entire list – and the EXIF cache grows
# chunk by chunk, so a cancellation does not lose any already-read data.
_EXIFTOOL_CHUNK = 2000
_EXIFTOOL_TIMEOUT = 300  # seconds per chunk


def _exiftool_batch(
    paths: list[Path],
    exiftool_bin: str,
) -> tuple[Optional[dict[str, dict]], list[str]]:
    """One ExifTool call for a chunk.

    Returns (exif_meta, debug_lines). `exif_meta` is None if the
    call failed (timeout, error, empty output) – then the files of
    the chunk must NOT be cached, so the next scan tries again.
    """
    debug: list[str] = []
    tmp_path: Optional[str] = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", delete=False, encoding="utf-8", suffix=".txt") as tmp:
            for p in paths:
                tmp.write(str(p.resolve()) + "\n")
            tmp_path = tmp.name

        # -j (JSON) instead of -s3: ExifTool returns one object per file
        # with "SourceFile" + exactly the tags that actually exist.
        # No line counting/guessing needed, robust even with mixed
        # file types (some have CreateDate, some don't, etc.).
        proc = subprocess.run(
            [
                exiftool_bin, "-j", "-d", "%Y-%m-%d",
                *_EXIF_DATE_TAGS, *_EXIF_META_TAGS,
                "-@", tmp_path,
            ],
            capture_output=True, text=True, timeout=_EXIFTOOL_TIMEOUT,
            encoding="utf-8", errors="replace",
        )
    except (subprocess.TimeoutExpired, OSError) as exc:
        debug.append(f"[ExifTool] Exception: {exc}")
        return None, debug
    finally:
        if tmp_path:
            try:
                Path(tmp_path).unlink(missing_ok=True)
            except Exception:
                pass

    debug.append(f"[ExifTool] returncode={proc.returncode}")
    if proc.stderr.strip():
        debug.append(f"[ExifTool] stderr: {proc.stderr.strip()[:500]}")

    if not (proc.returncode in (0, 1) and proc.stdout.strip()):
        debug.append(
            f"[ExifTool] No usable stdout (returncode={proc.returncode}, "
            f"stdout empty={not proc.stdout.strip()})"
        )
        return None, debug

    try:
        entries = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        debug.append(f"[ExifTool] JSON error: {exc}")
        return None, debug

    debug.append(f"[ExifTool] {len(entries)} entries received for {len(paths)} files")
    if entries:
        debug.append(f"[ExifTool] Sample entry: {entries[0]}")

    exif_meta: dict[str, dict] = {}
    for entry in entries:
        src = entry.get("SourceFile")
        if not src:
            continue
        key = str(Path(src).resolve()).lower()
        exif_meta[key] = entry
    return exif_meta, debug


def _meta_from_exif(p: Path, entry: dict) -> MetaInfo:
    """Build a MetaInfo from an ExifTool entry (empty entry → mtime fallback)."""
    date_str = ""
    for tag in ("DateTimeOriginal", "CreateDate", "MediaCreateDate"):
        val = entry.get(tag)
        if not val:
            continue
        try:
            datetime.strptime(val, "%Y-%m-%d")
            date_str = val
            break
        except ValueError:
            continue
    date_source = "exif" if date_str else ""

    if not date_str:
        try:
            mtime = datetime.fromtimestamp(p.stat().st_mtime)
            date_str, date_source = mtime.strftime("%Y-%m-%d"), "filesystem"
        except OSError:
            date_str, date_source = "1970-01-01", "unknown"

    model = sanitize_part(entry.get("Model", ""))
    lens = sanitize_part(
        entry.get("LensModel") or entry.get("LensID") or entry.get("LensSpec") or ""
    )
    focal = format_focal(entry.get("FocalLength", ""))

    return MetaInfo(
        date_str=date_str,
        date_source=date_source,
        media="video" if p.suffix.lower() in VIDEO_EXTENSIONS else "photo",
        model=model,
        lens=lens,
        focal=focal,
    )


def batch_get_metadata(
    paths: list[Path],
    exiftool_bin: str,
    exiftool_available: bool,
    cancel_flag: Optional[object] = None,
    progress_cb: Optional[Callable[[int, int], None]] = None,
    log: Optional[Callable[[str], None]] = None,
    db: Optional[ScanDB] = None,
    scan_key: Optional[str] = None,
) -> dict[Path, MetaInfo]:
    """
    Determine metadata for `paths`.

    Order: EXIF cache (DB) → ExifTool in chunks → mtime fallback.
    Results are written to the DB cache chunk by chunk – a
    cancellation or a failed batch thus loses no already-read data.
    """
    result: dict[Path, MetaInfo] = {}
    if not paths:
        return result

    total = len(paths)
    done = 0

    def _tick() -> None:
        nonlocal done
        done += 1
        if progress_cb:
            progress_cb(done, total)

    def _cancelled() -> bool:
        return cancel_flag is not None and cancel_flag.is_set()

    # 1) Collect cache hits, prepare the rest for ExifTool
    to_read: list[tuple[Path, object]] = []
    for p in paths:
        if _cancelled():
            raise CancelledError()
        try:
            st = p.stat()
        except OSError:
            st = None
        hit = None
        if db is not None and scan_key and st is not None:
            hit = db.get_exif(scan_key, str(p), st.st_size, st.st_mtime)
        if hit is not None:
            result[p] = MetaInfo(
                date_str=hit["date_str"] or "1970-01-01",
                date_source=hit["date_source"] or "unknown",
                media=hit["media"] or "photo",
                model=hit["model"],
                lens=hit["lens"],
                focal=hit["focal"],
            )
        else:
            to_read.append((p, st))
        _tick()

    if to_read and log:
        log(f"[ExifTool] {len(to_read)} file(s) to read "
            f"({total - len(to_read)} from cache).")

    matched = 0
    # 2) ExifTool in chunks (only for files not yet read)
    if exiftool_available and to_read:
        chunks = [
            to_read[i:i + _EXIFTOOL_CHUNK]
            for i in range(0, len(to_read), _EXIFTOOL_CHUNK)
        ]
        for ci, chunk in enumerate(chunks, 1):
            if _cancelled():
                raise CancelledError()
            if log:
                log(f"[ExifTool] Batch {ci}/{len(chunks)} ({len(chunk)} files) …")
            exif_meta, debug_lines = _exiftool_batch(
                [p for p, _ in chunk], exiftool_bin
            )
            for line in debug_lines:
                if log:
                    log(line)

            if exif_meta is None:
                # Batch failed: mtime fallback for the chunk, but
                # do NOT cache – the next scan tries ExifTool again.
                for p, _ in chunk:
                    result[p] = _meta_from_exif(p, {})
                    _tick()
                continue

            cache_items: list[tuple] = []
            for p, st in chunk:
                if _cancelled():
                    raise CancelledError()
                entry = exif_meta.get(str(p.resolve()).lower(), {})
                meta = _meta_from_exif(p, entry)
                result[p] = meta
                if entry.get("DateTimeOriginal") or entry.get("CreateDate") \
                        or entry.get("MediaCreateDate"):
                    matched += 1
                if st is not None and db is not None and scan_key:
                    cache_items.append((str(p), st.st_size, st.st_mtime, {
                        "date_str": meta.date_str,
                        "date_source": meta.date_source,
                        "model": meta.model,
                        "lens": meta.lens,
                        "focal": meta.focal,
                        "media": meta.media,
                    }))
                _tick()
            if db is not None and scan_key and cache_items:
                db.upsert_exif_many(scan_key, cache_items)

    # 3) ExifTool not available → mtime fallback (no caching)
    if not exiftool_available:
        for p, _ in to_read:
            if _cancelled():
                raise CancelledError()
            result[p] = _meta_from_exif(p, {})
            _tick()

    if log:
        log(f"[ExifTool] {matched} of {total} file(s) matched with an EXIF date.")

    return result


# ── Destination path & copying ────────────────────────────────────────────────

def resolve_destination(
    dest_dir: Path,
    new_stem: str,
    suffix: str,
    source_size: int,
) -> tuple[Path, bool]:
    natural = dest_dir / f"{new_stem}{suffix}"
    if natural.exists():
        try:
            if natural.stat().st_size == source_size:
                return natural, True
        except OSError:
            pass

    candidate = natural
    n = 1
    while candidate.exists():
        candidate = dest_dir / f"{new_stem}_{n}{suffix}"
        n += 1
    return candidate, False


def build_subfolder_parts(meta: MetaInfo, cfg: CompareConfig) -> list[str]:
    sf = cfg.subfolders
    parts: list[str] = []
    if sf.date: parts.append(meta.date_str)
    if sf.focal: parts.append(sanitize_part(meta.focal) or "unknown")
    if sf.model: parts.append(sanitize_part(meta.model) or "unknown")
    if sf.lens: parts.append(sanitize_part(meta.lens) or "unknown")
    if sf.media: parts.append(meta.media or "unknown")
    return parts


def build_new_name(stem: str, meta: MetaInfo, cfg: CompareConfig) -> str:
    """Build a new filename from EXIF parts, prefix, original name and suffix.

    The separator (``NamePartConfig.separator``) is automatically inserted
    between <b>all</b> name parts – including around prefix and suffix.
    Manually typed separators at the start/end of prefix/suffix are
    removed, so no double separators are created
    (e.g. suffix "_backup" + separator "_" → "backup").
    """
    np_ = cfg.name_parts
    sep = np_.separator

    def _trim_sep(text: str) -> str:
        if not sep:
            return text
        s = text
        while s.startswith(sep):
            s = s[len(sep):]
        while s.endswith(sep):
            s = s[:-len(sep)]
        return s

    parts: list[str] = []
    if np_.date and meta.date_str:
        parts.append(meta.date_str)
    prefix = _trim_sep(cfg.rename_prefix)
    if prefix:
        parts.append(prefix)
    parts.append(stem)
    suffix = _trim_sep(cfg.rename_suffix)
    if suffix:
        parts.append(suffix)
    if np_.model and meta.model:
        parts.append(meta.model)
    if np_.lens and meta.lens:
        parts.append(meta.lens)
    if np_.focal and meta.focal:
        parts.append(meta.focal)

    name = sep.join(parts)
    return sanitize_part(name) or stem


def plan_destination(
    cfg: CompareConfig,
    entry: FileEntry,
    meta: MetaInfo,
) -> tuple[Path, bool]:
    base = cfg.output_dir
    for part in build_subfolder_parts(meta, cfg):
        base = base / part
    new_stem = build_new_name(entry.path.stem, meta, cfg)
    return resolve_destination(base, new_stem, entry.path.suffix, entry.size)


def copy_file(src: Path, dst: Path, cancel_flag: Optional[object]) -> None:
    tmp = dst.with_suffix(dst.suffix + ".part")
    try:
        with open(src, "rb") as fsrc, open(tmp, "wb") as fdst:
            while chunk := fsrc.read(_CHUNK):
                if cancel_flag is not None and cancel_flag.is_set():
                    raise CancelledError()
                fdst.write(chunk)
        shutil.copystat(src, tmp)
        tmp.replace(dst)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


# ── Main flow ────────────────────────────────────────────────────────────────

@dataclass
class CompareConfig:
    import_root: Path   
    backup_dir: Path    
    output_dir: Path
    extensions: frozenset[str]
    use_hash: bool
    exiftool_bin: str
    rename_prefix: str = ""   
    rename_suffix: str = ""   
    subfolders: SubfolderConfig = field(default_factory=SubfolderConfig)
    name_parts: NamePartConfig = field(default_factory=NamePartConfig)
    db_path: Optional[Path] = None  # SQLite index; None = no persistence (legacy)


def run_comparison(
    cfg: CompareConfig,
    *,
    log: Callable[[str], None],
    progress: Callable[[str, int, int], None],
    cancel_flag: Optional[object],
) -> list[DiffResult]:
    backup_dir = cfg.backup_dir
    if not cfg.import_root.is_dir():
        raise ValueError(f"Shooting days folder not found: {cfg.import_root}")
    if not backup_dir.is_dir():
        raise ValueError(f"Ingest_Backup folder not found: {backup_dir}")

    log(f"Shooting days:  {cfg.import_root}")
    log(f"Ingest_Backup: {backup_dir}")
    log(f"Output:        {cfg.output_dir}")
    log(f"Hash matching: {'on' if cfg.use_hash else 'off'}")
    log("─" * 50)

    progress("Scanning shooting days …", 0, 1)
    shooting_files = [p for p in iter_media_files(cfg.import_root, cfg.extensions) if not is_within(p, backup_dir)]
    progress("Scanning backup …", 0, 1)
    backup_files = list(iter_media_files(backup_dir, cfg.extensions))
    log(f"  Shooting days: {len(shooting_files)} files")
    log(f"  Backup:       {len(backup_files)} files")

    # Optional SQLite index: incremental scans (unchanged files are not
    # re-hashed) and no data loss on cancellation.
    # One connection per thread – always closed again in the finally block.
    db: Optional[ScanDB] = None
    scan_key: Optional[str] = None
    if cfg.db_path is not None:
        scan_key = scan_key_for(cfg.import_root, backup_dir)
        db = ScanDB(cfg.db_path)
        log(f"SQLite-Index: {db.db_path}")

    try:
        log("Indexing …")
        known, reused_a = build_index(
            shooting_files, cfg.use_hash, cancel_flag,
            progress_cb=lambda i, t: progress("Indexing shooting days", i, t),
            db=db, scan_key=scan_key, tree="import",
        )
        if reused_a:
            log(f"  (reused hashes: {reused_a})")

        exif_ok = bool(cfg.exiftool_bin) and check_exiftool(cfg.exiftool_bin)
        log(f"ExifTool: {'found' if exif_ok else 'not found → mtime fallback'}")

        backup_index, reused_b = build_index(
            backup_files, cfg.use_hash, cancel_flag,
            progress_cb=lambda i, t: progress("Indexing backup", i, t),
            db=db, scan_key=scan_key, tree="backup",
        )
        if reused_b:
            log(f"  (reused hashes: {reused_b})")

        missing: list[FileEntry] = []
        backup_entries = [e for lst in backup_index.values() for e in lst]
        for i, e in enumerate(backup_entries, 1):
            if cancel_flag is not None and cancel_flag.is_set():
                raise CancelledError()
            if not matches_existing(e, known.get(e.name_lower, []), cfg.use_hash):
                missing.append(e)
            progress("Comparing", i, len(backup_entries))

        log(f"  → {len(missing)} file(s) missing from the shooting days.")
        if not missing:
            log("All in sync. ✓")
            return []

        log("Determining capture dates …")
        meta_map = batch_get_metadata(
            [e.path for e in missing], cfg.exiftool_bin, exif_ok, cancel_flag,
            progress_cb=lambda i, t: progress("Capture dates", i, t),
            log=log,
            db=db, scan_key=scan_key,
        )

        results: list[DiffResult] = []
        for idx, entry in enumerate(missing, 1):
            if cancel_flag is not None and cancel_flag.is_set():
                raise CancelledError()
            meta = meta_map.get(entry.path) or MetaInfo("1970-01-01", "unknown")
            dest, already = plan_destination(cfg, entry, meta)
            status = "present" if already else "missing"
            results.append(DiffResult(entry, meta, dest, status))
            progress("Planning transfer", idx, len(missing))

        log(f"Done: inventory report with {len(missing)} files created. ✓")

        if results:
            try:
                cfg.output_dir.mkdir(parents=True, exist_ok=True)
                debug_report_path = cfg.output_dir / "debug_scan_report.json"
                debug_data = [
                    {
                        "file": r.entry.path.name,
                        "path": str(r.entry.path),
                        "date_str": r.meta.date_str,
                        "date_source": r.meta.date_source,
                        "dest": str(r.dest_path),
                    }
                    for r in results
                ]
                with open(debug_report_path, "w", encoding="utf-8") as f:
                    json.dump(debug_data, f, indent=2, ensure_ascii=False)
                log(f"Debug report saved: {debug_report_path}")
            except Exception as exc:
                log(f"Could not save debug report: {exc}")

        return results
    finally:
        if db is not None:
            db.close()


def transfer_results(
    results: list[DiffResult],
    cfg: CompareConfig,
    mode: str,
    *,
    log: Callable[[str], None],
    progress: Callable[[str, int, int], None],
    cancel_flag: Optional[object] = None,
) -> list[DiffResult]:
    if mode not in ("copy", "move"):
        raise ValueError(f"Unknown mode: {mode!r}")

    pending = [r for r in results if r.status == "missing"]
    if not pending:
        log("Nothing to transfer.")
        return results

    cfg.output_dir.mkdir(parents=True, exist_ok=True)
    log(f"Transferring {len(pending)} file(s): {'copy' if mode == 'copy' else 'move'} …")

    for idx, r in enumerate(pending, 1):
        if cancel_flag is not None and cancel_flag.is_set():
            raise CancelledError()

        entry = r.entry
        dest, already = plan_destination(cfg, entry, r.meta)

        if already and cfg.use_hash:
            progress(f"Checking hash: {dest.name}", idx, len(pending))
            source_hash = entry.sha256 if entry.sha256 else sha256_of(entry.path, cancel_flag)
            dest_hash = sha256_of(dest, cancel_flag)
            if source_hash != dest_hash:
                already = False
                n = 1
                while dest.exists():
                    dest = dest.parent / f"{dest.stem}_{n}{dest.suffix}"
                    n += 1

        if already:
            r.status = "skipped"
            r.dest_path = dest
            log(f"  [SKIP] {entry.path.name} → already present")
        elif mode == "copy":
            dest.parent.mkdir(parents=True, exist_ok=True)
            copy_file(entry.path, dest, cancel_flag)
            r.status = "copied"
            r.dest_path = dest
            log(f"  [OK] {entry.path.name} → {dest.parent.relative_to(cfg.output_dir).as_posix()}/{dest.name}")
        else:
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(entry.path), str(dest))
            r.status = "moved"
            r.dest_path = dest
            log(f"  [MOVE] {entry.path.name} → {dest.parent.relative_to(cfg.output_dir).as_posix()}/{dest.name}")

        progress("Transferring", idx, len(pending))

    csv_path = cfg.output_dir / "differenzen_report.csv"
    with open(csv_path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["original", "date", "source", "destination", "status"])
        for r in results:
            w.writerow([str(r.entry.path), r.meta.date_str, r.meta.date_source, str(r.dest_path), r.status])
    log(f"Report: {csv_path}")
    return results
