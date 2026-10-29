"""
config.py -- Kramers settings, backup sets ("jobs"), and the run orchestration
shared by the GUI and the headless/scheduled runner.

Settings live in %APPDATA%\\Kramers\\config.json (override with the
KRAMERS_CONFIG environment variable). On first start a seed config.json next
to the app -- a v2 file, or a v1 file from Desktop Mirror Backup -- is
imported once and renamed to config.json.imported.
"""
from __future__ import annotations

import fnmatch
import json
import os
import re
import shutil
import threading
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable, Optional

from . import APP_NAME, __version__, engine, presets, recipes, winshell

APP_DIR = Path(__file__).resolve().parent.parent
RESOURCES = Path(__file__).resolve().parent / "resources"
TASK_NAME = "Kramers"
SCHEMA = 2
MARKER = ".kramers-target.json"

MODE_LABELS = [
    ("mirror", "Exact mirror (remove deleted files from the backup)"),
    ("versioned", "Versioned (archive removed and overwritten files)"),
    ("add_update", "Add & update only (never remove anything)"),
]

DEFAULTS = {
    "schema": SCHEMA,
    "dest_root": "",
    "dest_volume_serial": "",
    "default_mode": "mirror",
    "workers": 4,
    "low_priority": True,                # copy in Windows background mode (yields CPU/disk)
    "max_mb_per_s": 0,                   # 0 = no speed limit
    "guard": {"fraction": 0.20, "min_files": 50, "max_files": 5000},
    "jobs": [],
    "auto_sync_enabled": False,
    "auto_sync_minutes": 30,
    "schedule_freq": "DAILY",            # DAILY | HOURLY | EVERY_N_HOURS | ONLOGON
    "schedule_time": "18:00",
    "schedule_every_hours": 4,
    "theme": "system",                   # system | light | dark
    "close_to_tray": True,               # closing the window keeps Kramers in the notification area
    "archive_keep_days": 30,             # versioned mode: delete archives older than this (0 = keep)
    "notify_scheduled": "problems",      # scheduled runs: always | problems | never
    "unattended_removals": "apply",      # unattended runs past the guard: apply | skip
    "warn_unencrypted": False,           # main-window reminder when BitLocker is off
    "column_widths": {},                 # backup-sets table columns the user resized, in pixels
}


# --------------------------------------------------------------------------- #
# Load / save / migrate
# --------------------------------------------------------------------------- #

def config_path() -> Path:
    env = os.environ.get("KRAMERS_CONFIG")
    if env:
        return Path(env)
    return Path(os.environ.get("APPDATA", str(Path.home()))) / APP_NAME / "config.json"


def config_dir() -> Path:
    return config_path().parent


def new_job(job_id: str, name: str, preset: str, *, source: str = "", subdir: str = "",
            enabled: bool = True, mode: Optional[str] = None) -> dict:
    return {"id": job_id, "name": name, "preset": preset, "source": source,
            "subdir": subdir or name, "enabled": enabled, "mode": mode,
            "excludes": [], "disabled_roots": [], "every_days": 0}


def default_jobs() -> list:
    jobs = [new_job("desktop", "Desktop", "folder", source="{Known:Desktop}"),
            new_job("downloads", "Downloads", "folder", source="{Known:Downloads}"),
            new_job("endnote", "EndNote", "endnote", source=presets.ENDNOTE_DEFAULT,
                    enabled=presets.detected("endnote"))]
    for pid, title in (("claude", "Claude"), ("codex", "Codex"),
                       ("environments", "Python environments")):
        jobs.append(new_job(pid, title, pid, subdir="Environments" if pid == "environments" else title,
                            enabled=presets.detected(pid)))
    return jobs


def migrate_v1(old: dict) -> dict:
    """Desktop Mirror Backup (v1: one source/dest pair) -> v2."""
    cfg = json.loads(json.dumps(DEFAULTS))
    dest = os.path.normpath(old.get("dest", "") or "")
    cfg["dest_root"] = os.path.dirname(dest) if dest else ""
    cfg["jobs"] = default_jobs()
    desk = cfg["jobs"][0]
    desk["source"] = old.get("source") or desk["source"]
    desk["subdir"] = os.path.basename(dest) or "Desktop"
    if old.get("mode") in engine.MODES and old["mode"] != cfg["default_mode"]:
        desk["mode"] = old["mode"]
    for k in ("auto_sync_enabled", "auto_sync_minutes", "schedule_freq", "schedule_time",
              "schedule_every_hours"):
        if k in old:
            cfg[k] = old[k]
    return cfg


def _normalise(raw: dict) -> dict:
    if raw.get("schema", 1) < 2:
        raw = migrate_v1(raw)
    cfg = json.loads(json.dumps(DEFAULTS))
    if "low_priority" not in raw and raw.get("workers") == 6:
        raw = dict(raw, workers=4)       # 2.0.0's default was 6; gentler from 2.0.1
    cfg.update(raw)
    cfg["guard"] = {**DEFAULTS["guard"], **(raw.get("guard") or {})}
    for job in cfg["jobs"]:
        for k, v in new_job("", "", "").items():
            job.setdefault(k, v)
    return cfg


def load_config() -> dict:
    path = config_path()
    if path.exists():
        try:
            return _normalise(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError):
            pass
    seed = APP_DIR / "config.json"
    if seed.exists() and not os.environ.get("KRAMERS_CONFIG"):
        try:
            cfg = _normalise(json.loads(seed.read_text(encoding="utf-8")))
            save_config(cfg)
            seed.replace(seed.with_suffix(".json.imported"))
            return cfg
        except (OSError, json.JSONDecodeError):
            pass
    cfg = json.loads(json.dumps(DEFAULTS))
    cfg["jobs"] = default_jobs()
    return cfg


def save_config(cfg: dict) -> None:
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
    tmp.replace(path)


def load_state() -> dict:
    try:
        return json.loads((config_dir() / "state.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"jobs": {}}


def save_state(state: dict) -> None:
    p = config_dir() / "state.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(state, indent=2), encoding="utf-8")


# --------------------------------------------------------------------------- #
# Jobs -> engine roots
# --------------------------------------------------------------------------- #

def job_preset(job: dict) -> Optional[presets.Preset]:
    return presets.get_preset(job["preset"], job.get("source", ""))


def job_roots(cfg: dict, job: dict) -> list:
    """[(RootSpec, engine.Root, enabled)] for one job."""
    preset = job_preset(job)
    if preset is None or not cfg.get("dest_root"):
        return []
    dest_root = Path(cfg["dest_root"])
    base = dest_root / job["subdir"]
    mode = job.get("mode") or cfg["default_mode"]
    out = []
    for spec, src in presets.resolve_roots(preset):
        dest = base / spec.subdir if spec.subdir else base
        label = job["name"] if not spec.label else f"{job['name']} › {spec.label}"
        root = engine.Root(
            source=src, dest=str(dest), label=label, job=job["id"], mode=mode,
            archive_dir=str(dest_root / "_archive" / job["id"] / (spec.subdir or "")),
            excludes=tuple(spec.excludes) + tuple(job.get("excludes") or ()),
            only=tuple(spec.only), sqlite_snapshot=spec.sqlite_snapshot,
            redact_json=tuple(spec.redact_json), optional=spec.optional)
        out.append((spec, root, spec.key not in job.get("disabled_roots", [])))
    return out


def unattended_confirm(cfg: dict):
    """confirm_deletes for a run nobody is watching: go ahead, or withhold (None)."""
    return engine.approve_unattended if cfg.get("unattended_removals", "apply") == "apply" else None


def jobs_due(cfg: dict, now: Optional[datetime] = None) -> tuple:
    """(due job ids, {skipped job id: next due datetime}) honouring each set's every_days."""
    now = now or datetime.now()
    jobs_state = load_state().get("jobs", {})
    due, later = [], {}
    for job in cfg["jobs"]:
        if not job.get("enabled"):
            continue
        days = int(job.get("every_days") or 0)
        st = jobs_state.get(job["id"], {})
        try:
            last = datetime.fromisoformat(st["last_run"]) if st.get("status") in ("ok", "warning") else None
        except (KeyError, TypeError, ValueError):
            last = None
        if days > 0 and last is not None and now - last < timedelta(days=days):
            later[job["id"]] = last + timedelta(days=days)
        else:
            due.append(job["id"])
    return due, later


def build_roots(cfg: dict, job_ids: Optional[list] = None) -> list:
    roots = []
    for job in cfg["jobs"]:
        if not job.get("enabled") or (job_ids is not None and job["id"] not in job_ids):
            continue
        roots += [r for _, r, on in job_roots(cfg, job) if on]
    return roots


def _inside(child: str, parent: str) -> bool:
    try:
        c, p = os.path.normcase(os.path.abspath(child)), os.path.normcase(os.path.abspath(parent))
        return os.path.commonpath([c, p]) == p
    except ValueError:
        return False


def validate(cfg: dict) -> list:
    """Human-readable problems that must be fixed before a run."""
    problems = []
    dest_root = cfg.get("dest_root", "")
    if not dest_root:
        return ["Choose a backup destination folder."]
    for root in build_roots(cfg):
        if _inside(dest_root, root.source):
            problems.append(f"The destination is inside '{root.label}' ({root.source}).")
        elif _inside(root.source, dest_root):
            problems.append(f"'{root.label}' ({root.source}) is inside the destination.")
    return problems


# --------------------------------------------------------------------------- #
# Destination identity, marker, restore guide
# --------------------------------------------------------------------------- #

def read_marker(folder: str) -> Optional[dict]:
    try:
        data = json.loads((Path(folder) / MARKER).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) and data.get("app") == APP_NAME else None
    except (OSError, ValueError):
        return None


def find_backup_folder(path: str) -> Optional[str]:
    """`path` itself or a folder directly inside it that holds a Kramers backup (its marker)."""
    if not path or not os.path.isdir(path):
        return None
    if read_marker(path):
        return os.path.normpath(path)
    try:
        children = sorted(e.path for e in os.scandir(path) if e.is_dir(follow_symlinks=False))
    except OSError:
        return None
    found = [c for c in children if read_marker(c)]
    return os.path.normpath(found[0]) if len(found) == 1 else None


def is_drive_root(path: str) -> bool:
    drive, rest = os.path.splitdrive(os.path.normpath(path))
    return bool(drive) and rest in ("", "\\", "/")


def suggest_destination(path: str) -> tuple:
    """Where to back up when the user picks `path`: (folder, reason).

    A folder holding an existing Kramers backup (the picked one or one directly inside it,
    such as L:\\ -> L:\\Backups) is used as is, so a drive root never gets a second full copy.
    A drive root without one becomes <drive>\\Backups. reason is '' when nothing changed."""
    path = os.path.normpath(path)
    existing = find_backup_folder(path)
    if existing and os.path.normcase(existing) != os.path.normcase(path):
        return existing, f"Found your existing Kramers backup in {existing}; using it."
    if existing:
        return path, ""
    if is_drive_root(path):
        target = os.path.join(path, "Backups")
        return target, f"Backups go into {target} rather than the root of the drive."
    return path, ""


def resolve_destination(cfg: dict) -> str:
    """Keep dest_root pointing at the backup even when Windows gives the drive another letter.

    The drive is recognised by its volume serial (recorded on the first backup), or -- if that
    serial is mounted nowhere -- by its label together with the Kramers marker in the backup
    folder. If dest_root names a drive root that holds the Kramers backup folder, that folder
    is used. Updates cfg in place; returns a human-readable note when something changed."""
    root = cfg.get("dest_root", "")
    if not root or os.name != "nt":
        return ""
    serial, label = cfg.get("dest_volume_serial", ""), cfg.get("dest_volume_label", "")
    drive, rest = os.path.splitdrive(os.path.normpath(root))
    new = root
    notes = []
    if serial and winshell.volume_info(root)[0] != serial:
        vols = winshell.mounted_volumes()
        hit = next((r for r, s, _ in vols if s == serial), None)
        if hit is None and label:                 # same name, and our marker is there
            hit = next((r for r, s, lab in vols if lab == label
                        and (read_marker(os.path.join(r, rest.lstrip("\\"))) or {})
                        .get("volume_serial") == serial), None)
        if hit:
            new = os.path.join(hit, rest.lstrip("\\")) if rest.strip("\\") else hit
            notes.append(f"The backup drive{(' ' + repr(label)) if label else ''} is now "
                         f"{hit[:2]} (it was {drive}); Kramers follows it.")
    if is_drive_root(new):
        inside = find_backup_folder(new)
        if inside and os.path.normcase(inside) != os.path.normcase(new):
            notes.append(f"Using the existing Kramers backup folder {inside}.")
            new = inside
    if os.path.normcase(os.path.normpath(new)) == os.path.normcase(os.path.normpath(root)):
        return ""
    cfg["dest_root"] = os.path.normpath(new)
    return " ".join(notes)


def check_destination(cfg: dict) -> tuple:
    """(status, message, serial, label); status is 'ok' | 'offline' | 'wrong-drive'."""
    dest_root = cfg.get("dest_root", "")
    drive = os.path.splitdrive(os.path.abspath(dest_root))[0] + os.sep if dest_root else ""
    if drive == os.sep:                       # no drive letters: the mount point must exist
        drive = os.path.dirname(os.path.abspath(dest_root))
    if not dest_root or not os.path.exists(drive):
        return "offline", f"The backup drive {drive or dest_root} is not connected.", "", ""
    serial, label = winshell.volume_info(dest_root)
    expected = cfg.get("dest_volume_serial", "")
    if expected and serial and serial != expected:
        return ("wrong-drive",
                f"{drive} is a different drive ('{label}', serial {serial}); Kramers expects "
                f"serial {expected}. Nothing was written. If you replaced the backup drive, "
                "choose 'Use this drive' in the app.", serial, label)
    return "ok", "", serial, label


def write_marker(cfg: dict, serial: str, label: str) -> None:
    p = Path(cfg["dest_root"]) / MARKER
    data = {"app": APP_NAME, "version": __version__, "volume_serial": serial,
            "volume_label": label, "updated": datetime.now().isoformat(timespec="seconds")}
    p.write_text(json.dumps(data, indent=2), encoding="utf-8")


def write_restore_guide(cfg: dict) -> None:
    lines = [
        f"# Restoring from this {APP_NAME} backup",
        "",
        f"Generated {datetime.now():%Y-%m-%d %H:%M} by {APP_NAME} {__version__}. "
        "Each section lists where a backed-up folder came from. Copy it back to that "
        "location to restore it.",
        "",
        "General rules:",
        "- Close the app whose data you restore (Claude, Codex) before copying.",
        "- Login tokens were deliberately not backed up: sign in again after restoring.",
        "- Python environments contain absolute paths. Restore them to the same paths "
        "(same Windows user name), and restore `python-base` before the venvs. If the paths "
        "differ, rebuild from `Environments\\recipes` instead.",
        "",
    ]
    for job in cfg["jobs"]:
        if not job.get("enabled"):
            continue
        preset = job_preset(job)
        lines += [f"## {job['name']}", ""]
        if preset and preset.description and job["preset"] != "folder":
            lines += [preset.description, ""]
        lines += ["| Backup folder | Restore to |", "|---|---|"]
        for spec, root, on in job_roots(cfg, job):
            if on and os.path.exists(root.source):
                rel = os.path.relpath(root.dest, cfg["dest_root"])
                lines.append(f"| `{rel}` | `{root.source}` |")
        lines.append("")
    (Path(cfg["dest_root"]) / "RESTORE.md").write_text("\n".join(lines), encoding="utf-8")


# --------------------------------------------------------------------------- #
# Run
# --------------------------------------------------------------------------- #

def run_jobs(cfg: dict, job_ids: Optional[list] = None, *, dry_run: bool = False,
             progress_cb=None, cancel: Optional[threading.Event] = None,
             log: Optional[Callable[[str], None]] = None,
             confirm_deletes=None, pause: Optional[threading.Event] = None) -> engine.RunResult:
    """Check the destination, export recipes, run the engine, write logs and state."""
    ui_log = log or (lambda _m: None)
    stamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    moved = resolve_destination(cfg)
    if moved:
        save_config(cfg)
    status, message, serial, label = check_destination(cfg)
    log_dir = (Path(cfg["dest_root"]) / "_logs") if status == "ok" else (config_dir() / "logs")
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
        logf = open(log_dir / f"kramers_{stamp}{'_preview' if dry_run else ''}.log", "a",
                    encoding="utf-8")
    except OSError:
        logf = None
    lock = threading.Lock()
    run_lock = None

    def log_line(msg: str) -> None:
        with lock:
            if logf:
                logf.write(f"[{datetime.now():%H:%M:%S}] {msg}\n")
                logf.flush()
        ui_log(msg)

    try:
        if status != "ok":
            log_line(("SKIPPED: " if status == "offline" else "STOPPED: ") + message)
            phase = "done" if status == "offline" else "error"
            return engine.RunResult(progress=engine.Progress(phase=phase), plans=[], stamp=stamp,
                                    dest_offline=True, message=message)
        if moved:
            log_line(moved)
        if serial and (not cfg.get("dest_volume_serial") or cfg.get("dest_volume_label") != label):
            if not cfg.get("dest_volume_serial"):
                log_line(f"Backup drive recorded: '{label}' (serial {serial}).")
            cfg["dest_volume_serial"], cfg["dest_volume_label"] = serial, label
            save_config(cfg)
        problems = validate(cfg)
        if problems:
            for p in problems:
                log_line("STOPPED: " + p)
            return engine.RunResult(progress=engine.Progress(phase="error"), plans=[], stamp=stamp,
                                    message=problems[0])
        if not dry_run:
            run_lock = _acquire_run_lock(Path(cfg["dest_root"]) / "_logs" / ".kramers-run.lock")
            if run_lock is None:
                msg = "Another Kramers backup to this drive is already running; this one was skipped."
                log_line("SKIPPED: " + msg)
                return engine.RunResult(progress=engine.Progress(phase="done"), plans=[], stamp=stamp,
                                        message=msg)

        if job_ids is None:                   # "back up everything that is due"
            job_ids, later = jobs_due(cfg)
            names = {j["id"]: j["name"] for j in cfg["jobs"]}
            for jid, when in later.items():
                log_line(f"[{names[jid]}] not due until {when:%Y-%m-%d %H:%M}; skipped "
                         "(right-click the set to back it up now).")

        envs = next((j for j in cfg["jobs"] if j["preset"] == "environments"), None)
        if (not dry_run and envs and envs.get("enabled")
                and (job_ids is None or envs["id"] in job_ids)):
            try:
                recipes.export(Path(cfg["dest_root"]) / envs["subdir"] / "recipes", log_line, cancel)
            except Exception as exc:          # recipes are a bonus; never block the backup
                log_line(f"WARNING: environment recipes not exported: {exc}")

        result = engine.run(
            build_roots(cfg, job_ids), progress_cb=progress_cb, cancel=cancel, dry_run=dry_run,
            log=log_line, confirm_deletes=confirm_deletes,
            guard=engine.Guard(**cfg["guard"]), workers=int(cfg.get("workers", 4)),
            low_priority=bool(cfg.get("low_priority", True)),
            max_mb_per_s=float(cfg.get("max_mb_per_s", 0) or 0), pause=pause)

        if result.progress.phase == "done":
            _record_sizes(result)

        if not dry_run and result.progress.phase == "done":
            try:
                write_marker(cfg, serial, label)
                write_restore_guide(cfg)
            except OSError as exc:
                log_line(f"WARNING: could not write marker/RESTORE.md: {exc}")
            from . import folder_icons
            if folder_icons.apply(cfg, job_preset, RESOURCES):
                log_line("Folder icons set on the backup drive.")
            removed = prune_archives(cfg)
            if removed:
                log_line(f"Archive clean-up: removed {removed} archive folder(s) older than "
                         f"{cfg.get('archive_keep_days')} days.")
            state = load_state()
            now = datetime.now().isoformat(timespec="seconds")
            for jid, st in result.job_status().items():
                state.setdefault("jobs", {}).setdefault(jid, {}).update(last_run=now, status=st)
            state["last_run"] = now
            try:
                save_state(state)
            except OSError:
                pass
        return result
    finally:
        if run_lock is not None:
            run_lock.close()                  # closing the handle releases the lock
        if logf:
            logf.close()


STAMP_RE = re.compile(r"^\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}$")


def prune_archives(cfg: dict, now: Optional[datetime] = None) -> int:
    """Versioned mode: delete timestamped archive folders older than archive_keep_days."""
    days = int(cfg.get("archive_keep_days") or 0)
    base = Path(cfg.get("dest_root", "")) / "_archive"
    if days <= 0 or not base.is_dir():
        return 0
    cutoff = (now or datetime.now()) - timedelta(days=days)
    removed = 0
    for dirpath, dirnames, _files in os.walk(base):
        for d in list(dirnames):
            if STAMP_RE.match(d):
                dirnames.remove(d)                     # never descend into an archive
                try:
                    if datetime.strptime(d, "%Y-%m-%d_%H-%M-%S") < cutoff:
                        shutil.rmtree(os.path.join(dirpath, d), ignore_errors=True)
                        removed += 1
                except ValueError:
                    pass
    return removed


def restore_roots(cfg: dict, job: dict, root_keys: Optional[list] = None,
                  target: Optional[str] = None, overwrite: bool = False) -> list:
    """Engine roots that copy a set from the backup back to its original locations, or
    into `target`. Never deletes. Files that were stored redacted (login tokens removed)
    are not restored over the originals, so a restore never signs you out."""
    roots = []
    job_dir = Path(cfg["dest_root"]) / job["subdir"]
    for spec, root, on in job_roots(cfg, job):
        if root_keys is not None and spec.key not in root_keys:
            continue
        if not os.path.exists(root.dest):
            continue
        if target:
            rel = os.path.relpath(root.dest, job_dir)
            dest = str(Path(target) / job["subdir"] / ("" if rel == "." else rel))
        else:
            dest = root.source
        excludes = tuple(root.excludes)
        if not target and root.redact_json:
            globs = tuple(g for g, _ in root.redact_json)
            if os.path.isfile(root.dest) and any(fnmatch.fnmatch(os.path.basename(root.dest), g)
                                                 for g in globs):
                continue                          # a single redacted file: never put it back
            excludes += globs
        roots.append(engine.Root(
            source=root.dest, dest=dest, label=f"Restore {root.label}", job=job["id"],
            mode="add_update", excludes=excludes, only=root.only, update_existing=overwrite))
    return roots


def run_restore(cfg: dict, job_id: str, root_keys: Optional[list] = None,
                target: Optional[str] = None, overwrite: bool = False, *, dry_run: bool = False,
                progress_cb=None, cancel: Optional[threading.Event] = None,
                log: Optional[Callable[[str], None]] = None,
                pause: Optional[threading.Event] = None) -> engine.RunResult:
    ui_log = log or (lambda _m: None)
    stamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    job = next(j for j in cfg["jobs"] if j["id"] == job_id)
    if resolve_destination(cfg):
        save_config(cfg)
    status, message, serial, label = check_destination(cfg)
    if status != "ok":
        ui_log("STOPPED: " + message)
        return engine.RunResult(progress=engine.Progress(phase="error"), plans=[], stamp=stamp,
                                dest_offline=status == "offline", message=message)
    if target and _inside(target, cfg["dest_root"]):
        msg = "The restore folder must not be inside the backup."
        ui_log("STOPPED: " + msg)
        return engine.RunResult(progress=engine.Progress(phase="error"), plans=[], stamp=stamp,
                                message=msg)
    log_dir = Path(cfg["dest_root"]) / "_logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    lock = None if dry_run else _acquire_run_lock(log_dir / ".kramers-run.lock")
    if not dry_run and lock is None:
        msg = "A Kramers backup to this drive is running; try the restore again when it ends."
        ui_log("STOPPED: " + msg)
        return engine.RunResult(progress=engine.Progress(phase="error"), plans=[], stamp=stamp,
                                message=msg)
    with open(log_dir / f"kramers_{stamp}_restore{'_preview' if dry_run else ''}.log", "a",
              encoding="utf-8") as logf:
        def log_line(msg: str) -> None:
            logf.write(f"[{datetime.now():%H:%M:%S}] {msg}\n")
            logf.flush()
            ui_log(msg)
        try:
            where = target or "the original locations"
            log_line(f"Restoring '{job['name']}' to {where} "
                     f"({'missing and changed' if overwrite else 'missing files only'}; nothing is deleted).")
            return engine.run(restore_roots(cfg, job, root_keys, target, overwrite),
                              progress_cb=progress_cb, cancel=cancel, dry_run=dry_run, log=log_line,
                              workers=int(cfg.get("workers", 4)),
                              low_priority=bool(cfg.get("low_priority", True)),
                              max_mb_per_s=float(cfg.get("max_mb_per_s", 0) or 0), pause=pause)
        finally:
            if lock is not None:
                lock.close()


def _record_sizes(result: engine.RunResult) -> None:
    """Remember each set's source size and file count (shown in the app)."""
    totals: dict = {}
    for p in result.plans:
        if p.skip_reason:
            continue
        t = totals.setdefault(p.root.job, [0, 0])
        t[0] += p.source_bytes
        t[1] += p.scanned_source_files
    if not totals:
        return
    state = load_state()
    now = datetime.now().isoformat(timespec="seconds")
    for jid, (size, files) in totals.items():
        entry = state.setdefault("jobs", {}).setdefault(jid, {})
        entry.update(size_bytes=size, files=files, size_at=now)
    try:
        save_state(state)
    except OSError:
        pass


def _acquire_run_lock(path: Path):
    """An exclusive lock on the backup drive, so the app and the scheduled task
    never back up at the same time. Returns the open file, or None if taken."""
    f = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        f = open(path, "a+")
        if os.name == "nt":
            import msvcrt
            f.seek(0)
            msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return f
    except OSError:
        if f is not None:
            f.close()
        return None
