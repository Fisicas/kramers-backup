"""
make_screenshots.py -- README screenshots with demo data (no personal paths).

    python -X utf8 tools/make_screenshots.py

Needs an interpreter with sv-ttk and Pillow, on Windows, with a desktop session
(the window is shown for a moment and captured). Writes docs/images/
screenshot-dark.png, screenshot-light.png and screenshot-restore.png.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import time
from datetime import datetime, timedelta
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
IMAGES = REPO / "docs" / "images"
sys.path.insert(0, str(REPO))

DEMO_HOME = r"C:\Users\you"
DEMO_SOURCES = {
    "desktop": r"C:\Users\you\Desktop",
    "downloads": r"C:\Users\you\Downloads",
    "endnote": r"C:\Users\you\Documents\EndNote",
    "claude": "4 locations — .claude, .claude.json, Claude app data, …",
    "codex": "1 location — .codex",
    "environments": "6 locations — science, pymol, Python312, …",
}
DEMO_SIZES = {"desktop": (89.6e9, 323_000), "downloads": (275e6, 45), "endnote": (5.8e9, 2522),
              "claude": (1.4e9, 15_000), "codex": (3.7e9, 3220), "environments": (9.9e9, 230_000)}
DEMO_LOG = [
    "=== Kramers backup 2026-10-13_20-35-02 ===",
    "[Desktop] 312 new, 41 changed, 0 to remove, 322 640 unchanged; 611,402,118 bytes to write.",
    "[EndNote] 0 new, 2 changed, 0 to remove, 2 520 unchanged; 18,980,864 bytes to write.",
    "[Codex › ~/.codex] 0 new, 11 changed, 0 to remove, 3 209 unchanged; 410,233,906 bytes to write.",
    "[Python environments] not due until 2026-10-20 18:00; skipped (right-click the set to back it up now).",
    "Done. Copied 326, updated 54, removed 0, archived 0, in use 0, gone 0, warnings 0, errors 0. Elapsed 93.1s.",
]


def capture(win, path: Path) -> None:
    from PIL import ImageGrab
    win.update()
    time.sleep(0.6)
    win.update()
    x, y = win.winfo_rootx(), win.winfo_rooty()
    ImageGrab.grab((x, y, x + win.winfo_width(), y + win.winfo_height())).save(path)
    print("wrote", path)


def run(theme: str, restore: bool) -> None:
    tmp = Path(tempfile.mkdtemp(prefix="kramers-shots-"))
    os.environ["KRAMERS_CONFIG"] = str(tmp / "config.json")
    from kramers import config, gui, winshell
    winshell.volume_info = lambda path: ("1A2B-3C4D", "Backup")
    winshell.bitlocker_state = lambda path: "on"
    gui.App._source_text = lambda self, job: DEMO_SOURCES.get(job["id"], "")
    cfg = json.loads(json.dumps(config.DEFAULTS))
    cfg.update(dest_root=r"K:\Backups", theme=theme, jobs=config.default_jobs())
    for job in cfg["jobs"]:
        job["enabled"] = True
    next(j for j in cfg["jobs"] if j["id"] == "environments")["every_days"] = 7
    config.save_config(cfg)
    last = (datetime.now() - timedelta(minutes=12)).isoformat(timespec="seconds")
    config.save_state({"jobs": {k: {"size_bytes": int(b), "files": n, "last_run": last, "status": "ok"}
                                for k, (b, n) in DEMO_SIZES.items()}, "last_run": last,
                       "tray_hint_shown": True})

    import tkinter as tk
    root = tk.Tk()
    root.withdraw()
    app = gui.App(root, config.load_config(), None)
    app._log_many(DEMO_LOG)
    app.phase_var.set("Finished")
    app.pbar.configure(value=100)
    app.pct_var.set("100%")
    app.stats_var.set("copied 326 · updated 54 · removed 0 · archived 0 · in use 0 · gone 0 · "
                      "warnings 0 · errors 0 · 648.7 MB of 648.7 MB · 93 s")
    root.geometry("1060x900")
    root.deiconify()
    root.attributes("-topmost", True)

    def go():
        if restore:
            from kramers.dialogs import RestoreDialog
            job = next(j for j in app.cfg["jobs"] if j["id"] == "endnote")
            dlg = RestoreDialog(app, job)
            home = os.path.expanduser("~")
            for iid in dlg.tree.get_children():            # demo paths only
                vals = [str(v).replace(home, DEMO_HOME) for v in dlg.tree.item(iid, "values")]
                dlg.tree.item(iid, values=vals)
            dlg.attributes("-topmost", True)
            capture(dlg, IMAGES / "screenshot-restore.png")
            dlg.destroy()
        else:
            capture(root, IMAGES / f"screenshot-{theme}.png")
        if app.tray:
            app.tray.stop()
        root.destroy()
    root.after(1500, go)
    root.mainloop()


if __name__ == "__main__":
    import subprocess
    IMAGES.mkdir(parents=True, exist_ok=True)
    if len(sys.argv) > 1:                       # child: one screenshot per process (one Tk theme each)
        run(sys.argv[1], sys.argv[2] == "restore")
    else:
        for theme, what in (("dark", "main"), ("light", "main"), ("dark", "restore")):
            subprocess.run([sys.executable, "-X", "utf8", __file__, theme, what], check=True)
