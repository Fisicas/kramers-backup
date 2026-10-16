"""
engine.py -- Kramers' one-way mirror engine.

A *root* pairs one source (a folder or a single file) with one destination.
A run scans every root first, applies the safety guards, then copies
(in parallel) and finally removes what no longer exists in the source.

Modes (per root):

    "mirror"      Exact mirror. Changed files are overwritten, removed files
                  are deleted from the backup. (Default.)
    "versioned"   Mirror, but overwritten/removed backup files are first moved
                  to a timestamped archive folder.
    "add_update"  Copy new and changed files; never delete from the backup.

Safety guards (all modes that delete):

    * A missing source is skipped -- its backup is never touched.
    * An empty source with a non-empty backup never deletes.
    * Mass deletions (see Guard) need confirmation; unattended runs skip them.

Special copies:

    * SQLite databases (roots with sqlite_snapshot) are copied with SQLite's
      online-backup API, so databases held open by a running app (WAL mode)
      are captured consistently.
    * JSON files matched by redact_json are written with matching keys
      removed (used to keep login tokens out of the backup). A file that
      cannot be parsed is skipped -- never copied unredacted.

Standard library only; imports and runs on non-Windows for testing.
"""
from __future__ import annotations

import fnmatch
import json
import os
import re
import shutil
import sqlite3
import stat
import threading
import time
from concurrent.futures import CancelledError, ThreadPoolExecutor, as_completed
from contextlib import closing
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable, Iterable, Optional

IS_WINDOWS = os.name == "nt"

# FAT/exFAT store mtimes at 2 s granularity and cross-filesystem copies drift.
MTIME_TOLERANCE_SECONDS = 2.0

# exFAT/FAT cannot store timestamps before 1980; asking for one fails with
# WinError 87. 1980-01-02 UTC keeps clear of any time-zone offset.
FAT_EPOCH = 315_619_200.0

COPY_CHUNK_BYTES = 1024 * 1024
PARTIAL_SUFFIX = ".kramers-partial"
LEGACY_PARTIAL_SUFFIX = ".dmb_partial"

MODES = ("mirror", "versioned", "add_update")

# Never mirrored or purged, at any depth.
DEFAULT_EXCLUDES = (
    "$RECYCLE.BIN",
    "System Volume Information",
    "Thumbs.db",
    "~$*",               # Office lock files
    ".writer.lock",      # GammaKeeper's run lock (held open while recording)
    "*.partial",         # other tools' work in progress (e.g. a release being deployed)
    "*" + PARTIAL_SUFFIX,
    "*" + LEGACY_PARTIAL_SUFFIX,
)

# Folders the tool keeps at the top of a destination; never treated as data.
TOOL_DIRS = ("_archive", "_logs", "_BackupTool", "_kramers")

SQLITE_PATTERNS = ("*.sqlite", "*.sqlite3", "*.db", "*.eni", "*.enl")   # .eni/.enl: EndNote
SQLITE_SIDECARS = tuple(f"{p}-{s}" for p in SQLITE_PATTERNS for s in ("wal", "shm", "journal"))
SQLITE_MAGIC = b"SQLite format 3\x00"


# --------------------------------------------------------------------------- #
# Data structures
# --------------------------------------------------------------------------- #

@dataclass
class Guard:
    """When a root's planned deletions need confirmation."""
    fraction: float = 0.20     # ask when more than this share of the backup goes
    min_files: int = 50        # ...but never for fewer deletions than this
    max_files: int = 5000      # always ask at or above this many deletions

    def triggered(self, n_delete: int, n_dest: int) -> bool:
        if n_delete < self.min_files:
            return False
        if n_delete >= self.max_files:
            return True
        return n_dest > 0 and n_delete / n_dest > self.fraction


@dataclass
class Root:
    """One source (folder or file) mirrored to one destination."""
    source: str
    dest: str
    label: str = ""
    job: str = ""                    # owning job id (for per-job status)
    mode: str = "mirror"
    archive_dir: str = ""            # versioned mode: <archive_dir>/<stamp>/<rel>
    excludes: tuple = ()
    only: tuple = ()                 # top-level allow-list (globs); empty = all
    sqlite_snapshot: bool = False
    redact_json: tuple = ()          # ((rel_glob, (key_regex, ...)), ...)
    optional: bool = False           # a missing source is normal, not a warning
    follow_reparse: bool = False
    update_existing: bool = True     # False: only copy files missing at the destination


@dataclass
class Action:
    kind: str                        # "copy" | "update" | "delete"
    rel: str                         # posix path relative to the root
    size: int = 0
    is_dir: bool = False
    special: str = ""                # "" | "sqlite" | "redact"
    mtime: float = 0.0               # source mtime to stamp on the copy


@dataclass
class RootPlan:
    root: Root
    is_file_root: bool = False
    copies: list = field(default_factory=list)
    updates: list = field(default_factory=list)
    deletes: list = field(default_factory=list)       # files
    dir_deletes: list = field(default_factory=list)
    unchanged_files: int = 0
    scanned_source_files: int = 0
    scanned_dest_files: int = 0
    stale_partials: list = field(default_factory=list)
    warnings: list = field(default_factory=list)      # (path, message)
    skip_reason: str = ""            # whole root skipped
    deletes_blocked: str = ""        # deletions withheld, and why
    guard_hit: bool = False
    error_count: int = 0
    source_bytes: int = 0            # total size of the (included) source files

    @property
    def bytes_to_write(self) -> int:
        return sum(a.size for a in self.copies) + sum(a.size for a in self.updates)


@dataclass
class Progress:
    phase: str = "idle"   # scanning | copying | deleting | done | cancelled | error
    label: str = ""       # current root label
    current: str = ""     # current file
    files_done: int = 0
    files_total: int = 0
    bytes_done: int = 0
    bytes_total: int = 0
    copied: int = 0
    updated: int = 0
    deleted: int = 0
    archived: int = 0
    skipped: int = 0      # in use / locked / unreadable-for-redaction
    vanished: int = 0     # deleted from the source while the run was going
    warnings: int = 0
    errors: int = 0
    started_at: float = 0.0
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False, compare=False)

    @property
    def elapsed(self) -> float:
        return (time.time() - self.started_at) if self.started_at else 0.0

    def add(self, **counts) -> None:
        with self._lock:
            for k, v in counts.items():
                setattr(self, k, getattr(self, k) + v)

    def snapshot(self) -> dict:
        d = {k: getattr(self, k) for k in (
            "phase", "label", "current", "files_done", "files_total", "bytes_done",
            "bytes_total", "copied", "updated", "deleted", "archived", "skipped",
            "vanished", "warnings", "errors")}
        d["elapsed"] = self.elapsed
        return d


@dataclass
class RunResult:
    progress: Progress
    plans: list                      # RootPlan per root
    stamp: str = ""
    dest_offline: bool = False
    message: str = ""

    def job_status(self) -> dict:
        """job id -> 'ok' | 'warning' | 'error' | 'skipped'."""
        out: dict = {}
        rank = {"ok": 0, "skipped": 1, "warning": 2, "error": 3}
        for p in self.plans:
            s = "ok"
            if p.skip_reason and not p.root.optional:
                s = "skipped"
            if p.warnings or p.deletes_blocked:
                s = "warning"
            if p.error_count:
                s = "error"
            j = p.root.job or p.root.label
            if j not in out or rank[s] > rank[out[j]]:
                out[j] = s
        return out


ProgressCB = Callable[[Progress], None]


def approve_unattended(plan: "RootPlan") -> bool:
    """confirm_deletes for unattended runs (scheduled, or automatic while the window is
    open) whose settings say large removals should go ahead without asking."""
    return True
LogFn = Callable[[str], None]
ConfirmFn = Callable[[RootPlan], bool]


# --------------------------------------------------------------------------- #
# Path helpers
# --------------------------------------------------------------------------- #

def _long(path: str) -> str:
    """Prefix a Windows path so it works beyond MAX_PATH (no-op elsewhere)."""
    if not IS_WINDOWS or path.startswith("\\\\?\\"):
        return path
    ap = os.path.abspath(path)
    if ap.startswith("\\\\"):
        return "\\\\?\\UNC\\" + ap[2:]
    return "\\\\?\\" + ap


class _Matcher:
    """Glob patterns: plain names match at any depth; patterns containing '/'
    match the root-relative path ('/cache' = top-level 'cache' only)."""

    def __init__(self, patterns: Iterable[str]):
        pats = [p.replace("\\", "/") for p in patterns if p]
        self.names = [p for p in pats if "/" not in p]
        self.paths = [p.strip("/") for p in pats if "/" in p]

    def __call__(self, name: str, rel: str) -> bool:
        return (any(fnmatch.fnmatch(name, p) for p in self.names)
                or any(fnmatch.fnmatch(rel, p) for p in self.paths))

    def __bool__(self) -> bool:
        return bool(self.names or self.paths)


def _join(base: str, rel: str) -> str:
    return os.path.normpath(os.path.join(base, rel)) if rel else os.path.normpath(base)


def _src_path(plan: RootPlan, rel: str) -> str:
    return os.path.normpath(plan.root.source) if plan.is_file_root else _join(plan.root.source, rel)


def _dst_path(plan: RootPlan, rel: str) -> str:
    return os.path.normpath(plan.root.dest) if plan.is_file_root else _join(plan.root.dest, rel)


def _same_mtime(src: float, dst: float) -> bool:
    if abs(src - dst) <= MTIME_TOLERANCE_SECONDS:
        return True
    # A pre-1980 source time is stored clamped on FAT/exFAT backups.
    return src < FAT_EPOCH and abs(dst - FAT_EPOCH) <= MTIME_TOLERANCE_SECONDS


def _is_locked(exc: OSError) -> bool:
    return getattr(exc, "winerror", None) in (32, 33) or isinstance(exc, PermissionError)


# --------------------------------------------------------------------------- #
# Scan / plan
# --------------------------------------------------------------------------- #

def _walk(root_dir: str, excludes: _Matcher, only: _Matcher, follow_reparse: bool,
          warnings: list, partials: Optional[list] = None):
    """Yield (rel_posix, stat, is_dir) for everything under root_dir."""
    root_dir = os.path.abspath(root_dir)
    tool = _Matcher("/" + d for d in TOOL_DIRS)
    stack = [(root_dir, "")]
    while stack:
        current, crel = stack.pop()
        try:
            with os.scandir(_long(current)) as it:
                entries = list(it)
        except OSError as exc:
            warnings.append((current, f"cannot list folder: {exc}"))
            continue
        for entry in entries:
            name = entry.name
            rel = f"{crel}/{name}" if crel else name
            if partials is not None and (name.endswith(PARTIAL_SUFFIX)
                                         or name.endswith(LEGACY_PARTIAL_SUFFIX)):
                partials.append(rel)
                continue
            if not crel and (tool(name, rel) or (only and not only(name, rel))):
                continue
            if excludes(name, rel):
                continue
            full = os.path.join(current, name)
            try:
                st = entry.stat(follow_symlinks=False)    # cached by scandir on Windows
                reparse = entry.is_symlink() or bool(getattr(st, "st_file_attributes", 0) & 0x400)
                if reparse and not follow_reparse:
                    continue
                if entry.is_dir(follow_symlinks=False):
                    yield rel, st, True
                    stack.append((full, rel))
                else:
                    yield rel, st, False
            except OSError as exc:
                warnings.append((full, f"cannot read: {exc}"))


def _special_kind(root: Root, rel: str) -> str:
    if root.redact_json and any(fnmatch.fnmatch(rel, g) for g, _ in root.redact_json):
        return "redact"
    name = rel.rsplit("/", 1)[-1]
    if root.sqlite_snapshot and any(fnmatch.fnmatch(name, p) for p in SQLITE_PATTERNS):
        return "sqlite"
    return ""


def _effective_mtime(src_full: str, st: os.stat_result, special: str) -> float:
    """A WAL database changes without touching its main file's mtime."""
    m = st.st_mtime
    if special == "sqlite":
        try:
            m = max(m, os.stat(_long(src_full + "-wal")).st_mtime)
        except OSError:
            pass
    return m


def _classify(plan: RootPlan, rel: str, sst: os.stat_result,
              dst: Optional[os.stat_result]) -> None:
    special = _special_kind(plan.root, rel)
    mtime = _effective_mtime(_src_path(plan, rel), sst, special)
    act = Action("copy", rel, sst.st_size, special=special, mtime=mtime)
    if dst is None:
        plan.copies.append(act)
    elif not plan.root.update_existing:
        plan.unchanged_files += 1
    elif (not special and sst.st_size != dst.st_size) or not _same_mtime(mtime, dst.st_mtime):
        act.kind = "update"
        plan.updates.append(act)
    else:
        plan.unchanged_files += 1


def build_plan(root: Root, progress: Optional[Progress] = None,
               progress_cb: Optional[ProgressCB] = None,
               cancel: Optional[threading.Event] = None,
               pause: Optional[threading.Event] = None) -> RootPlan:
    """Compare one root's source and destination."""
    plan = RootPlan(root=root)
    progress = progress or Progress()
    src = os.path.normpath(root.source)

    if not os.path.exists(_long(src)):
        plan.skip_reason = "source not found"
        return plan

    if os.path.isfile(_long(src)):
        plan.is_file_root = True
        sst = os.stat(_long(src))
        try:
            dst = os.stat(_long(os.path.normpath(root.dest)))
        except OSError:
            dst = None
        plan.scanned_source_files = 1
        plan.source_bytes = sst.st_size
        plan.scanned_dest_files = 1 if dst else 0
        _classify(plan, os.path.basename(src), sst, dst)
        return plan

    excludes = _Matcher(DEFAULT_EXCLUDES + tuple(root.excludes)
                        + (SQLITE_SIDECARS if root.sqlite_snapshot else ()))
    only = _Matcher(root.only)

    src_index: dict = {}
    src_dirs: set = set()
    for rel, st, is_dir in _walk(src, excludes, only, root.follow_reparse, plan.warnings):
        if cancel is not None and cancel.is_set():
            return plan
        if is_dir:
            src_dirs.add(rel)
        else:
            src_index[rel] = st
            plan.scanned_source_files += 1
            plan.source_bytes += st.st_size
            if plan.scanned_source_files % 500 == 0 and pause is not None:
                _wait_while_paused(pause, cancel)
            if progress_cb and plan.scanned_source_files % 500 == 0:
                progress.current = rel
                progress_cb(progress)

    dst_index: dict = {}
    dst_dirs: set = set()
    dest = os.path.normpath(root.dest)
    if os.path.isdir(_long(dest)):
        for rel, st, is_dir in _walk(dest, excludes, only, root.follow_reparse,
                                     plan.warnings, plan.stale_partials):
            if cancel is not None and cancel.is_set():
                return plan
            if is_dir:
                dst_dirs.add(rel)
            else:
                dst_index[rel] = st
                plan.scanned_dest_files += 1

    norm = (lambda s: s.lower()) if IS_WINDOWS else (lambda s: s)
    dst_by_norm = {norm(k): v for k, v in dst_index.items()}
    for rel, sst in src_index.items():
        _classify(plan, rel, sst, dst_by_norm.get(norm(rel)))

    if root.mode in ("mirror", "versioned"):
        src_norm = {norm(k) for k in src_index}
        src_dirs_norm = {norm(d) for d in src_dirs}
        plan.deletes = [Action("delete", r) for r in dst_index if norm(r) not in src_norm]
        plan.dir_deletes = [Action("delete", d, is_dir=True)
                            for d in dst_dirs if norm(d) not in src_dirs_norm]
        if plan.scanned_source_files == 0 and plan.scanned_dest_files > 0:
            plan.deletes_blocked = "source folder is empty; backup left untouched"
    return plan


# --------------------------------------------------------------------------- #
# Being a good neighbour: background priority, speed limit, pause
# --------------------------------------------------------------------------- #

def set_background_mode(on: bool) -> None:
    """Windows background mode for the calling thread: low CPU, disk-I/O and
    memory priority, so a backup yields to whatever the user is doing."""
    if not IS_WINDOWS:
        return
    try:
        import ctypes
        k = ctypes.windll.kernel32
        k.SetThreadPriority(k.GetCurrentThread(), 0x00010000 if on else 0x00020000)
    except (AttributeError, OSError):
        pass


class RateLimiter:
    """Caps the combined write speed of all copy threads (bytes per second)."""

    def __init__(self, bytes_per_s: float):
        self.rate = float(bytes_per_s)
        self.lock = threading.Lock()
        self.next_free = time.monotonic()

    def consume(self, n: int, cancel: Optional[threading.Event] = None) -> None:
        if self.rate <= 0 or n <= 0:
            return
        with self.lock:
            now = time.monotonic()
            start = max(now, self.next_free)
            self.next_free = start + n / self.rate
            wait = self.next_free - now
        while wait > 0 and not (cancel is not None and cancel.is_set()):
            time.sleep(min(wait, 0.25))
            wait -= 0.25


def _wait_while_paused(pause: Optional[threading.Event], cancel: Optional[threading.Event]) -> None:
    while pause is not None and pause.is_set() and not (cancel is not None and cancel.is_set()):
        time.sleep(0.2)


_limiter: Optional[RateLimiter] = None
_pause: Optional[threading.Event] = None


# --------------------------------------------------------------------------- #
# Copy helpers
# --------------------------------------------------------------------------- #

def _make_writable(path: str) -> None:
    try:
        st = os.stat(path)
        if not st.st_mode & stat.S_IWRITE:
            os.chmod(path, st.st_mode | stat.S_IWRITE)
    except OSError:
        pass


def _stream_copy(src: str, tmp: str, progress: Progress,
                 progress_cb: Optional[ProgressCB],
                 cancel: Optional[threading.Event]) -> None:
    with open(_long(src), "rb") as fsrc, open(_long(tmp), "wb") as fdst:
        while True:
            _wait_while_paused(_pause, cancel)
            if cancel is not None and cancel.is_set():
                raise InterruptedError("cancelled")
            chunk = fsrc.read(COPY_CHUNK_BYTES)
            if not chunk:
                break
            if _limiter is not None:
                _limiter.consume(len(chunk), cancel)
            fdst.write(chunk)
            progress.add(bytes_done=len(chunk))
            if progress_cb:
                progress_cb(progress)


def _is_sqlite(path: str) -> bool:
    try:
        with open(_long(path), "rb") as f:
            return f.read(16) == SQLITE_MAGIC
    except OSError:
        return False


def _sqlite_snapshot(src: str, tmp: str) -> None:
    uri = Path(src).absolute().as_uri() + "?mode=ro"
    with closing(sqlite3.connect(uri, uri=True, timeout=15)) as s, \
            closing(sqlite3.connect(tmp)) as d:
        s.backup(d)
    for side in ("-wal", "-shm", "-journal"):
        try:
            os.remove(tmp + side)
        except OSError:
            pass


def _redact(obj, patterns: list):
    if isinstance(obj, dict):
        return {k: _redact(v, patterns) for k, v in obj.items()
                if not any(p.search(k) for p in patterns)}
    if isinstance(obj, list):
        return [_redact(v, patterns) for v in obj]
    return obj


def _redact_copy(src: str, tmp: str, key_regexes: Iterable[str]) -> None:
    """Raises ValueError if the file is not valid JSON (never copy it raw)."""
    with open(_long(src), "r", encoding="utf-8") as f:
        data = json.load(f)
    pats = [re.compile(r) for r in key_regexes]
    with open(_long(tmp), "w", encoding="utf-8") as f:
        json.dump(_redact(data, pats), f, indent=2, ensure_ascii=False)


def _apply_times(path: str, st: os.stat_result, mtime: float) -> Optional[str]:
    """Stamp times; clamp pre-1980 values (exFAT). Returns a warning or None."""
    m = max(mtime, FAT_EPOCH)
    a = st.st_atime if st.st_atime >= FAT_EPOCH else m
    try:
        os.utime(_long(path), (a, m))
        return None
    except OSError as exc:
        return f"could not set file times: {exc}"


def _archive_existing(plan: RootPlan, stamp: str, rel: str, progress: Progress) -> None:
    src = _dst_path(plan, rel)
    if not os.path.exists(_long(src)):
        return
    target = _join(os.path.join(plan.root.archive_dir, stamp), rel)
    os.makedirs(os.path.dirname(_long(target)), exist_ok=True)
    final, n = target, 1
    while os.path.exists(_long(final)):
        base, ext = os.path.splitext(target)
        final, n = f"{base} ({n}){ext}", n + 1
    _make_writable(_long(src))
    shutil.move(_long(src), _long(final))
    progress.add(archived=1)


def _copy_one(plan: RootPlan, act: Action, stamp: str, progress: Progress,
              progress_cb: Optional[ProgressCB], cancel: Optional[threading.Event],
              log: LogFn) -> None:
    src, dst = _src_path(plan, act.rel), _dst_path(plan, act.rel)
    tmp = dst + PARTIAL_SUFFIX
    progress.current = f"{plan.root.label}/{act.rel}" if not plan.is_file_root else plan.root.label
    sst = os.stat(_long(src))
    os.makedirs(os.path.dirname(_long(dst)), exist_ok=True)
    if act.kind == "update" and plan.root.mode == "versioned":
        _archive_existing(plan, stamp, act.rel, progress)
    try:
        if act.special == "sqlite" and _is_sqlite(src):
            try:
                _sqlite_snapshot(src, tmp)
                progress.add(bytes_done=act.size)
            except sqlite3.Error as exc:
                log(f"NOTE: {plan.root.label}/{act.rel}: SQLite snapshot failed ({exc}); plain copy used")
                _stream_copy(src, tmp, progress, progress_cb, cancel)
        elif act.special == "redact":
            patterns = next(k for g, k in plan.root.redact_json if fnmatch.fnmatch(act.rel, g))
            _redact_copy(src, tmp, patterns)
            progress.add(bytes_done=act.size)
        else:
            _stream_copy(src, tmp, progress, progress_cb, cancel)
        warn = _apply_times(tmp, sst, act.mtime or sst.st_mtime)
        if warn:
            progress.add(warnings=1)
            log(f"WARNING: {plan.root.label}/{act.rel}: {warn}")
        if os.path.exists(_long(dst)):
            _make_writable(_long(dst))
        os.replace(_long(tmp), _long(dst))
        if act.special == "sqlite":
            # A stale -wal beside a fresh snapshot would be replayed onto it when opened.
            for side in ("-wal", "-shm", "-journal"):
                try:
                    os.remove(_long(dst + side))
                except OSError:
                    pass
        if not sst.st_mode & stat.S_IWRITE:
            os.chmod(_long(dst), stat.S_IREAD)
    except BaseException:
        try:
            if os.path.exists(_long(tmp)):
                os.remove(_long(tmp))
        except OSError:
            pass
        raise


# --------------------------------------------------------------------------- #
# Run
# --------------------------------------------------------------------------- #

def run(roots: list, *, progress_cb: Optional[ProgressCB] = None,
        cancel: Optional[threading.Event] = None, dry_run: bool = False,
        log: Optional[LogFn] = None, confirm_deletes: Optional[ConfirmFn] = None,
        guard: Optional[Guard] = None, workers: int = 4, low_priority: bool = True,
        max_mb_per_s: float = 0, pause: Optional[threading.Event] = None) -> RunResult:
    """Scan every root, apply guards, then copy and delete."""
    global _limiter, _pause
    _limiter = RateLimiter(max_mb_per_s * 1024 * 1024) if max_mb_per_s and max_mb_per_s > 0 else None
    _pause = pause
    if low_priority:
        set_background_mode(True)
    try:
        return _run(roots, progress_cb, cancel, dry_run, log, confirm_deletes, guard, workers,
                    low_priority, pause)
    finally:
        if low_priority:
            set_background_mode(False)
        _limiter, _pause = None, None


def _run(roots, progress_cb, cancel, dry_run, log, confirm_deletes, guard, workers,
         low_priority, pause) -> RunResult:
    log = log or (lambda _m: None)
    guard = guard or Guard()
    progress = Progress(started_at=time.time())
    stamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    result = RunResult(progress=progress, plans=[], stamp=stamp)
    cb = progress_cb or (lambda _p: None)

    def finish(phase: str) -> RunResult:
        progress.phase = phase
        progress.current = ""
        cb(progress)
        return result

    log(f"=== Kramers {'preview' if dry_run else 'backup'} {stamp} ===")

    # ---- 1. scan --------------------------------------------------------- #
    progress.phase = "scanning"
    for root in roots:
        if root.mode not in MODES:
            log(f"ERROR: {root.label}: unknown mode {root.mode!r}")
            progress.add(errors=1)
            continue
        progress.label = root.label
        progress.current = ""
        cb(progress)
        plan = build_plan(root, progress, cb, cancel, pause)
        result.plans.append(plan)
        if cancel is not None and cancel.is_set():
            log("Cancelled during scan.")
            return finish("cancelled")
        if plan.skip_reason:
            if root.optional:
                log(f"[{root.label}] not present here; skipped.")
            else:
                progress.add(warnings=1)
                log(f"WARNING: [{root.label}] {plan.skip_reason}: {root.source} — backup left untouched.")
            continue
        for path, msg in plan.warnings:
            progress.add(warnings=1)
            log(f"SCAN WARNING: [{root.label}] {path}: {msg}")
        log(f"[{root.label}] {len(plan.copies)} new, {len(plan.updates)} changed, "
            f"{len(plan.deletes)} to remove, {plan.unchanged_files} unchanged; "
            f"{plan.bytes_to_write:,} bytes to write.")

    # ---- 2. guards ------------------------------------------------------- #
    for plan in result.plans:
        if plan.deletes_blocked:
            progress.add(warnings=1)
            log(f"GUARD: [{plan.root.label}] {plan.deletes_blocked}")
        elif plan.deletes and guard.triggered(len(plan.deletes), plan.scanned_dest_files):
            plan.guard_hit = True
            if dry_run:
                log(f"GUARD: [{plan.root.label}] {len(plan.deletes)} of "
                    f"{plan.scanned_dest_files} backup files would be removed; "
                    "a real run will ask first.")
                continue
            ok = bool(confirm_deletes and confirm_deletes(plan))
            if not ok:
                plan.deletes_blocked = (f"{len(plan.deletes)} deletions withheld "
                                        "(mass-deletion guard; not confirmed)")
                progress.add(warnings=1)
                log(f"GUARD: [{plan.root.label}] {plan.deletes_blocked}")
            elif confirm_deletes is approve_unattended:
                log(f"GUARD: [{plan.root.label}] {len(plan.deletes)} of {plan.scanned_dest_files} "
                    "backup files removed without asking (unattended run; see Options).")
            else:
                log(f"GUARD: [{plan.root.label}] {len(plan.deletes)} deletions confirmed.")
        if plan.deletes_blocked:
            plan.deletes, plan.dir_deletes = [], []

    copies = [(p, a) for p in result.plans for a in p.copies + p.updates]
    deletes = [(p, a) for p in result.plans for a in p.deletes]
    progress.files_total = len(copies) + len(deletes)
    progress.bytes_total = sum(a.size for _, a in copies)

    if dry_run:
        log(f"Preview complete: {progress.files_total} actions, "
            f"{progress.bytes_total:,} bytes. Nothing was changed.")
        return finish("done")

    # ---- 3. copy (parallel) ---------------------------------------------- #
    for plan in result.plans:
        for rel in plan.stale_partials:
            try:
                os.remove(_long(_dst_path(plan, rel)))
            except OSError:
                pass
    progress.phase = "copying"
    cancelled = False

    def task(plan: RootPlan, act: Action):
        _wait_while_paused(pause, cancel)
        if cancel is not None and cancel.is_set():
            raise InterruptedError("cancelled")
        _copy_one(plan, act, stamp, progress, cb, cancel, log)
        return plan, act

    init = (lambda: set_background_mode(True)) if low_priority else None
    with ThreadPoolExecutor(max_workers=max(1, workers), initializer=init) as pool:
        futures = {pool.submit(task, p, a): (p, a) for p, a in copies}
        for fut in as_completed(futures):
            plan, act = futures[fut]
            where = f"{plan.root.label}/{act.rel}" if not plan.is_file_root else plan.root.label
            try:
                fut.result()
                if act.kind == "copy":
                    progress.add(copied=1)
                else:
                    progress.add(updated=1)
            except (InterruptedError, CancelledError):
                cancelled = True
            except ValueError as exc:      # redaction could not parse the file
                progress.add(skipped=1)
                log(f"SKIPPED (not redactable, not copied): {where}: {exc}")
            except OSError as exc:
                if isinstance(exc, FileNotFoundError) and not os.path.exists(_long(_src_path(plan, act.rel))):
                    progress.add(vanished=1)
                    log(f"GONE (deleted from the source during the run): {where}")
                elif _is_locked(exc):
                    progress.add(skipped=1)
                    log(f"SKIPPED (in use): {where}")
                else:
                    progress.add(errors=1)
                    plan.error_count += 1
                    log(f"COPY ERROR: {where}: {exc}")
            progress.add(files_done=1)
            cb(progress)
            if cancelled or (cancel is not None and cancel.is_set()):
                cancelled = True
                for f in futures:
                    f.cancel()
    if cancelled:
        log("Cancelled during copy.")
        return finish("cancelled")

    # ---- 4. delete ------------------------------------------------------- #
    progress.phase = "deleting"
    for plan, act in deletes:
        _wait_while_paused(pause, cancel)
        if cancel is not None and cancel.is_set():
            log("Cancelled during removal.")
            return finish("cancelled")
        progress.current = f"{plan.root.label}/{act.rel}"
        try:
            if plan.root.mode == "versioned":
                _archive_existing(plan, stamp, act.rel, progress)
            else:
                path = _long(_dst_path(plan, act.rel))
                if os.path.exists(path):
                    _make_writable(path)
                    os.remove(path)
            progress.add(deleted=1)
        except OSError as exc:
            progress.add(errors=1)
            plan.error_count += 1
            log(f"DELETE ERROR: {plan.root.label}/{act.rel}: {exc}")
        progress.add(files_done=1)
        cb(progress)
    for plan in result.plans:
        for act in sorted(plan.dir_deletes, key=lambda a: a.rel.count("/"), reverse=True):
            path = _long(_dst_path(plan, act.rel))
            try:
                if os.path.isdir(path) and not os.listdir(path):
                    os.rmdir(path)
            except OSError as exc:
                log(f"RMDIR SKIP: {plan.root.label}/{act.rel}: {exc}")

    p = progress
    log(f"Done. Copied {p.copied}, updated {p.updated}, removed {p.deleted}, "
        f"archived {p.archived}, in use {p.skipped}, gone {p.vanished}, warnings {p.warnings}, "
        f"errors {p.errors}. Elapsed {p.elapsed:.1f}s.")
    return finish("done")
