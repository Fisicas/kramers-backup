"""
runner.py -- headless backup for Windows Task Scheduler and silent runs.

    pythonw Kramers.pyw --run                 # back up every enabled set that is due
    python  Kramers.pyw --run --dry-run       # preview, printed to the console
    python  Kramers.pyw --run --jobs desktop,claude

Nobody is asked anything: removals past the mass-deletion guard go ahead, or are
withheld and logged, according to the "unattended_removals" setting (apply | skip).
Missing or empty sources never delete, whatever the setting. If the backup drive is not connected the run is skipped cleanly.
Afterwards a Windows notification reports the result, according to the
"notify_scheduled" setting (always | problems | never); clicking it opens
Kramers.
"""
from __future__ import annotations

import os
import subprocess
import sys
import time

from . import APP_ID, APP_NAME, config, engine


def summary(result: engine.RunResult) -> tuple:
    """(title, text, level, needs_attention) describing a finished run."""
    p = result.progress
    if result.dest_offline:
        return (f"{APP_NAME}: backup skipped", result.message or "The backup drive is not connected.",
                "info", False)
    if result.message:
        return f"{APP_NAME}: backup stopped", result.message, "warning", True
    if p.phase != "done":
        return f"{APP_NAME}: backup {p.phase}", "See the log for details.", "error", True
    text = (f"Copied {p.copied:,}, updated {p.updated:,}, removed {p.deleted:,}"
            + (f", {p.skipped:,} in use" if p.skipped else "")
            + (f", {p.warnings:,} warnings" if p.warnings else "")
            + (f", {p.errors:,} errors" if p.errors else "") + ".")
    if p.errors:
        return f"{APP_NAME}: backup finished with errors", text, "error", True
    if p.warnings:
        return f"{APP_NAME}: backup needs attention", text, "warning", True
    return f"{APP_NAME}: backup finished", text, "info", False


def record_outcome(title: str, text: str, level: str) -> None:
    """Remember how this unattended run ended -- including runs skipped because the drive
    was not connected, which write no backup state -- so an open window can show it."""
    from datetime import datetime
    try:
        state = config.load_state()
        state["last_scheduled"] = {"at": datetime.now().isoformat(timespec="seconds"),
                                   "title": title, "summary": text, "level": level}
        config.save_state(state)
    except OSError:
        pass


def should_notify(mode: str, needs_attention: bool) -> bool:
    return mode == "always" or (mode == "problems" and needs_attention)


def _open_app() -> None:
    from .shortcuts import launch_command
    exe, args = launch_command()
    subprocess.Popen([exe, *args], close_fds=True)


def notify(title: str, text: str, level: str, seconds: float = 12.0) -> None:
    """Show a notification from a short-lived tray icon (Windows only)."""
    if os.name != "nt":
        return
    from . import tray, winshell
    winshell.set_app_user_model_id(APP_ID)
    clicked = []
    icon = tray.TrayIcon(str(config.RESOURCES / "kramers.ico"), APP_NAME,
                         lambda key: clicked.append(key))
    icon.menu_items = [("open", "Open Kramers", True, True)]
    if not icon.start():
        return
    icon.notify(title, text, level)
    end = time.monotonic() + seconds
    while time.monotonic() < end and not clicked:
        time.sleep(0.2)
    icon.stop()
    if clicked:
        _open_app()


def main(argv: list) -> int:
    dry = "--dry-run" in argv or "-n" in argv
    jobs = None
    if "--jobs" in argv:
        i = argv.index("--jobs")
        if i + 1 < len(argv):
            jobs = [j.strip() for j in argv[i + 1].split(",") if j.strip()]
    console = sys.stdout is not None and sys.stdout.isatty()
    cfg = config.load_config()
    from .worker import set_process_background
    set_process_background(bool(cfg.get("low_priority", True)))
    result = config.run_jobs(cfg, jobs, dry_run=dry,
                             log=(lambda m: print(m, flush=True)) if console else None,
                             confirm_deletes=config.unattended_confirm(cfg))
    title, text, level, attention = summary(result)
    if not dry:
        record_outcome(title, text, level)
    if console:
        print(f"{title}: {text}")
    elif not dry and should_notify(cfg.get("notify_scheduled", "problems"), attention):
        notify(title, text, level)
    p = result.progress
    if result.dest_offline:
        return 0 if p.phase == "done" else 2
    return 0 if p.phase == "done" and p.errors == 0 else 1


def cli() -> int:
    """Console entry point (`kramers-run`)."""
    return main(sys.argv[1:])
