"""
worker.py -- runs a backup in its own process.

Scanning and copying are CPU-heavy Python. In the window's process they would
compete with the user interface for Python's interpreter lock, and resizing
the window during a scan would crawl. In a separate process the window stays
responsive, and Windows can run the whole backup in background mode (low CPU,
disk and memory priority).

The window talks to the child through two queues and two events:
    events  child -> window: ("progress", snapshot) | ("log", text)
                             | ("confirm", request) | ("done", summary, serial)
    replies window -> child: True/False answers to "confirm"
    cancel, pause           set by the window, polled by the engine
"""
from __future__ import annotations

import multiprocessing as mp
import os
import time

from . import config, engine


def set_process_background(on: bool) -> None:
    """PROCESS_MODE_BACKGROUND_BEGIN: the whole process yields to foreground work."""
    if os.name != "nt" or not on:
        return
    try:
        import ctypes
        k = ctypes.windll.kernel32
        k.SetPriorityClass(k.GetCurrentProcess(), 0x00100000)
    except (AttributeError, OSError):
        pass


def summarize(result: engine.RunResult | None) -> dict | None:
    """A small, picklable picture of a run for the window (no per-file lists)."""
    if result is None:
        return None
    plans = [{
        "label": p.root.label, "job": p.root.job, "optional": p.root.optional,
        "new": len(p.copies), "changed": len(p.updates), "remove": len(p.deletes),
        "bytes": p.bytes_to_write, "skip_reason": p.skip_reason,
        "deletes_blocked": p.deletes_blocked, "guard_hit": p.guard_hit,
        "warnings": len(p.warnings), "delete_sample": [a.rel for a in p.deletes[:1000]],
    } for p in result.plans]
    return {"plans": plans, "progress": result.progress.snapshot(), "message": result.message,
            "dest_offline": result.dest_offline}


def confirm_request(plan: engine.RootPlan) -> dict:
    return {"label": plan.root.label, "n": len(plan.deletes), "total": plan.scanned_dest_files,
            "sample": [a.rel for a in plan.deletes[:2000]]}


def child_main(cfg, job_ids, dry_run, events, replies, cancel, pause, restore=None) -> None:
    set_process_background(bool(cfg.get("low_priority", True)))
    last = [0.0]

    def progress(p: engine.Progress) -> None:
        now = time.monotonic()
        if p.phase in ("done", "cancelled", "error") or now - last[0] >= 0.2:
            last[0] = now
            events.put(("progress", p.snapshot()))

    def confirm(plan: engine.RootPlan) -> bool:
        events.put(("confirm", confirm_request(plan)))
        return bool(replies.get())

    result = None
    try:
        if restore:
            result = config.run_restore(cfg, dry_run=dry_run, progress_cb=progress, cancel=cancel,
                                        log=lambda m: events.put(("log", m)), pause=pause,
                                        **restore)
        else:
            result = config.run_jobs(cfg, job_ids, dry_run=dry_run, progress_cb=progress,
                                     cancel=cancel, log=lambda m: events.put(("log", m)),
                                     confirm_deletes=confirm, pause=pause)
    except BaseException as exc:                     # report, never die silently
        events.put(("log", f"ERROR: {type(exc).__name__}: {exc}"))
    events.put(("done", summarize(result), cfg.get("dest_volume_serial", "")))


class BackupProcess:
    """One backup run in a child process."""

    def __init__(self, cfg: dict, job_ids, dry_run: bool, restore: dict | None = None):
        ctx = mp.get_context("spawn")
        self.events = ctx.Queue()
        self.replies = ctx.Queue()
        self.cancel = ctx.Event()
        self.pause = ctx.Event()
        self.dry_run = dry_run
        self.restore = restore
        self.finished = False
        self.proc = ctx.Process(target=child_main, name="kramers-backup", daemon=True,
                                args=(cfg, job_ids, dry_run, self.events, self.replies,
                                      self.cancel, self.pause, restore))

    def start(self) -> None:
        self.proc.start()

    def is_alive(self) -> bool:
        return self.proc.is_alive() or not self.finished

    def join(self, timeout: float) -> None:
        self.proc.join(timeout)
        if self.proc.is_alive():
            self.proc.terminate()       # last resort; partial files are cleaned up next run
