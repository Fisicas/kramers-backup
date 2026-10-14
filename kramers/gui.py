"""
gui.py -- the Kramers window.

Backup sets are listed with the icons Windows itself uses (special folders,
installed apps, drives); buttons use Segoe Fluent Icons glyphs. Theme is
Sun Valley (sv-ttk) following the Windows light/dark setting, with a native
ttk fallback if sv-ttk is not installed.
"""
from __future__ import annotations

import copy
import os
import queue
import shutil
import subprocess
import threading
import time
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from . import APP_ID, APP_NAME, __version__, config, engine, fastui, presets, shortcuts, winshell
from .worker import BackupProcess
from .splash import Splash

if os.name == "nt":
    from . import tray as tray_mod
else:                          # pragma: no cover
    tray_mod = None

try:
    import sv_ttk
except ImportError:            # the app still works with the native theme
    sv_ttk = None

from .dialogs import ConfirmDeletesDialog, Dialog, JobDialog, PreviewDialog, RestoreDialog
from .ui_common import (FREQ_LABELS, FREQ_TO_LABEL, LABEL_TO_FREQ,
                        LABEL_TO_MODE, MODE_TO_LABEL, STATUS_TEXT, Icons, human_bytes,
                        human_count, human_when, open_path)



# --------------------------------------------------------------------------- #
# Main window
# --------------------------------------------------------------------------- #

class App:
    def __init__(self, root: tk.Tk, cfg: dict, splash: Splash | None = None):
        self.root = root
        self.cfg = cfg
        self.state = config.load_state()
        self.worker: BackupProcess | None = None
        self._rate_samples: list = []
        self.cancel = threading.Event()
        self.pause = threading.Event()
        self.q: "queue.Queue[tuple]" = queue.Queue()
        self.auto_after = None
        self.last_preview: engine.RunResult | None = None

        theme = cfg.get("theme", "system")
        self.dark = winshell.system_uses_dark_mode() if theme == "system" else theme == "dark"
        if sv_ttk is None:
            self.dark = False                 # the native fallback theme is light-only
        self.flat = False
        if sv_ttk is not None:
            sv_ttk.set_theme("dark" if self.dark else "light")
            self.flat = fastui.install(root, self.dark)    # fast flat styles for big widgets
        self.text_bg = "#2b2b2b" if self.dark else "#ffffff"
        self.text_fg = "#e6e6e6" if self.dark else "#1a1a1a"
        self.text_border = "#3d3d3d" if self.dark else "#d0d0d0"

        if splash:
            splash.step("Loading Windows icons")
        self.icons = Icons(root, self.dark)

        root.title(APP_NAME)
        try:
            root.iconbitmap(default=str(config.RESOURCES / "kramers.ico"))
        except tk.TclError:
            pass
        root.geometry("1060x900")
        root.minsize(900, 700)
        ttk.Style().configure("Treeview", rowheight=self.icons.px + 12)

        self._build()
        self._load_jobs()
        if splash:
            splash.step("Checking backup drive")
        self._refresh_dest()
        if splash:
            splash.step("Checking Windows schedule")
        self._refresh_schedule_status()
        self._arm_autosync()
        self.tray = None
        self._tray_t = 0.0
        if tray_mod is not None:
            t = tray_mod.TrayIcon(str(config.RESOURCES / "kramers.ico"), APP_NAME,
                                  lambda key: self.q.put(("tray", key)))
            if t.start():
                self.tray = t
        self._update_tray()
        root.protocol("WM_DELETE_WINDOW", self._on_close)
        root.after(100, self._pump)
        self._state_seen = (self._state_mtime(), time.strftime("%Y-%m-%d"))
        root.after(30_000, self._watch_state)

    # ------------------------------------------------------------------ external runs
    @staticmethod
    def _state_mtime() -> float:
        try:
            return (config.config_dir() / "state.json").stat().st_mtime
        except OSError:
            return 0.0

    def _watch_state(self):
        """Pick up backups recorded by other Kramers processes (the scheduled task), and
        re-render relative dates ("Today" -> "Yesterday") after midnight."""
        seen = (self._state_mtime(), time.strftime("%Y-%m-%d"))
        if seen != self._state_seen and not (self.worker and self.worker.is_alive()):
            self._state_seen = seen
            self.state = config.load_state()
            self._load_jobs()
            self._update_tray()
        self.root.after(30_000, self._watch_state)

    # ------------------------------------------------------------------ layout
    def _build(self):
        I = self.icons
        main = ttk.Frame(self.root, padding=(16, 12, 16, 12))
        main.pack(fill="both", expand=True)
        self.main = main
        # While the user drags the window edge, show a light placeholder and redraw the
        # full window once when they stop: Tk repaints every widget on every resize step.
        self.cover = ttk.Frame(self.root)
        ttk.Label(self.cover, text=APP_NAME, foreground="#666",
                  font=("Segoe UI Semibold", 14)).place(relx=0.5, rely=0.4, anchor="center")
        self._frozen = False
        self._last_size = None
        self._thaw_job = None
        self.root.bind("<Configure>", self._on_root_configure, add="+")
        main.columnconfigure(0, weight=1)

        # header
        head = ttk.Frame(main)
        head.grid(row=0, column=0, sticky="ew")
        try:
            self._logo = tk.PhotoImage(master=self.root, file=str(config.RESOURCES / "kramers-64.png"))
            self._logo = self._logo.subsample(2)
            ttk.Label(head, image=self._logo).pack(side="left", padx=(0, 10))
        except tk.TclError:
            pass
        tf = ttk.Frame(head)
        tf.pack(side="left")
        ttk.Label(tf, text=APP_NAME, font=("Segoe UI Semibold", 18)).pack(anchor="w")
        ttk.Label(tf, text="Every file gets its degenerate partner.", foreground="#0ea5a4").pack(anchor="w")
        ttk.Label(head, text=f"v{__version__}", foreground="#888").pack(side="right", anchor="n")

        # backup sets
        sets, self.sets_title = self._section(main, 1, "Backup sets", "nsew", (12, 6))
        main.rowconfigure(1, weight=1)
        sets.columnconfigure(0, weight=1)
        sets.rowconfigure(0, weight=1)
        cols = ("on", "source", "backup", "size", "mode", "last")
        self.tree = ttk.Treeview(sets, columns=cols, height=7, selectmode="browse",
                                 style=self.sty("Treeview"))
        self.tree.heading("#0", text="Set")
        self.tree.column("#0", width=175, minwidth=150, stretch=False)
        for c, t, w, st in (("on", "Include", 60, False), ("source", "Source", 190, True),
                            ("backup", "Backed up to", 140, True), ("size", "Size", 145, False),
                            ("mode", "Mode", 145, False), ("last", "Last backup", 135, False)):
            self.tree.heading(c, text=t)
            self.tree.column(c, width=w, stretch=st, anchor={"on": "center", "size": "e"}.get(c, "w"))
        self.tree.grid(row=0, column=0, sticky="nsew")
        sb = ttk.Scrollbar(sets, orient="vertical", command=self.tree.yview,
                           style=self.sty("Vertical.TScrollbar"))
        sb.grid(row=0, column=1, sticky="ns")
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.bind("<Button-1>", self._tree_click)
        self.tree.bind("<Double-1>", self._tree_double)
        self.tree.bind("<Button-3>", self._tree_menu)
        self.tree.bind("<space>", lambda e: self._toggle_selected())
        bar = ttk.Frame(sets)
        bar.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(8, 0))
        ttk.Button(bar, text="Add folder…", image=I.glyph("add"), compound="left",
                   command=self._add_folder, style=self.sty("TButton")).pack(side="left")
        ttk.Button(bar, text="Edit…", image=I.glyph("edit"), compound="left",
                   command=self._edit_selected, style=self.sty("TButton")).pack(side="left", padx=6)
        ttk.Button(bar, text="Remove", image=I.glyph("remove"), compound="left",
                   command=self._remove_selected, style=self.sty("TButton")).pack(side="left")
        ttk.Button(bar, text="Restore…", image=I.glyph("history"), compound="left",
                   command=self._restore_selected, style=self.sty("TButton")).pack(side="left", padx=(18, 0))
        ttk.Label(bar, text="Double-click a set to edit it; right-click for more.",
                  foreground="#888").pack(side="right")

        # destination
        dest, _ = self._section(main, 2, "Destination", "ew", 6)
        dest.columnconfigure(2, weight=1)
        self.drive_icon = ttk.Label(dest)
        self.drive_icon.grid(row=0, column=0, padx=(0, 6))
        ttk.Label(dest, text="Back up to").grid(row=0, column=1, sticky="w", padx=(0, 8))
        self.dest_var = tk.StringVar(value=self.cfg.get("dest_root", ""))
        e = ttk.Entry(dest, textvariable=self.dest_var, style=self.sty("TEntry"))
        e.grid(row=0, column=2, sticky="ew")
        e.bind("<FocusOut>", lambda _e: self._dest_changed())
        e.bind("<Return>", lambda _e: self._dest_changed())
        ttk.Button(dest, text="Browse…", image=I.glyph("open"), compound="left",
                   command=self._pick_dest, style=self.sty("TButton")).grid(row=0, column=3, padx=6)
        ttk.Button(dest, text="Open", image=I.glyph("folder"), compound="left", style=self.sty("TButton"),
                   command=lambda: open_path(self.cfg.get("dest_root", ""))).grid(row=0, column=4)
        self.dest_info = ttk.Label(dest, text="")
        self.dest_info.grid(row=1, column=1, columnspan=3, sticky="w", pady=(6, 0))
        self.enc_frame = ttk.Frame(dest)             # optional reminder (Options), hidden by default
        self.enc_info = ttk.Label(self.enc_frame, text="")
        self.enc_info.pack(side="left")
        self._bitlocker: dict = {}
        self.opt_enc_label = None
        self.use_drive_btn = ttk.Button(dest, text="Use this drive", image=I.glyph("drive"),
                                        compound="left", command=self._adopt_drive, style=self.sty("TButton"))
        ttk.Label(dest, text="Default mode").grid(row=2, column=1, sticky="w", pady=(8, 0))
        self.mode_var = tk.StringVar(value=MODE_TO_LABEL[self.cfg["default_mode"]])
        mc = ttk.Combobox(dest, textvariable=self.mode_var, state="readonly",
                          values=[v for _, v in config.MODE_LABELS], style=self.sty("TCombobox"))
        mc.grid(row=2, column=2, columnspan=3, sticky="ew", pady=(8, 0))
        mc.bind("<<ComboboxSelected>>", lambda _e: self._mode_changed())

        # actions
        act = ttk.Frame(main)
        act.grid(row=3, column=0, sticky="ew", pady=(8, 4))
        run_img = I.glyph("sync", ((255, 255, 255) if not self.dark else (0, 0, 0))
                          if self.flat else None)      # on the blue accent button, or plain
        self.run_btn = ttk.Button(act, text="Back up now",
                                  image=(run_img, "disabled", I.glyph("sync", (130, 130, 130))),
                                  compound="left", style=self.sty("Accent.TButton"),
                                  command=lambda: self._start(False))
        self.run_btn.pack(side="left")
        self.preview_btn = ttk.Button(act, text="Preview", image=I.glyph("preview"), compound="left",
                                      command=lambda: self._start(True), style=self.sty("TButton"))
        self.preview_btn.pack(side="left", padx=6)
        self.cancel_btn = ttk.Button(act, text="Cancel", image=I.glyph("cancel"), compound="left",
                                     command=self._on_cancel, state="disabled", style=self.sty("TButton"))
        self.cancel_btn.pack(side="left")
        self.pause_btn = ttk.Button(act, text="Pause", image=I.glyph("pause"), compound="left",
                                    command=self._toggle_pause, state="disabled", style=self.sty("TButton"))
        self.pause_btn.pack(side="left", padx=6)
        ttk.Button(act, text="Options", image=I.glyph("settings"), compound="left",
                   command=self._open_options, style=self.sty("TButton")).pack(side="right", padx=(6, 0))
        ttk.Button(act, text="Restore guide", image=I.glyph("document"), compound="left",
                   style=self.sty("TButton"),
                   command=lambda: open_path(os.path.join(self.cfg.get("dest_root", ""), "RESTORE.md"))
                   ).pack(side="right")
        ttk.Button(act, text="Logs", image=I.glyph("history"), compound="left",
                   style=self.sty("TButton"),
                   command=lambda: open_path(os.path.join(self.cfg.get("dest_root", ""), "_logs"))
                   ).pack(side="right", padx=6)

        # progress
        prog, _ = self._section(main, 4, "Progress", "ew", 6)
        prog.columnconfigure(0, weight=1)
        self.phase_var = tk.StringVar(value="Ready")
        ttk.Label(prog, textvariable=self.phase_var, font=("Segoe UI Semibold", 11)).grid(
            row=0, column=0, sticky="w")
        self.pbar = ttk.Progressbar(prog, mode="determinate", maximum=100,
                                    style=self.sty("Horizontal.TProgressbar"))
        self.pbar.grid(row=1, column=0, sticky="ew", pady=6)
        self.pct_var = tk.StringVar()
        ttk.Label(prog, textvariable=self.pct_var, width=6, anchor="e").grid(row=1, column=1, padx=(8, 0))
        self.current_var = tk.StringVar()
        ttk.Label(prog, textvariable=self.current_var, foreground="#888").grid(
            row=2, column=0, columnspan=2, sticky="w")
        self.stats_var = tk.StringVar()
        ttk.Label(prog, textvariable=self.stats_var).grid(row=3, column=0, columnspan=2, sticky="w", pady=(4, 0))

        # tabs (a plain tab bar: ttk.Notebook is also slow to resize under sv-ttk)
        tabs = ttk.Frame(main)
        tabs.grid(row=5, column=0, sticky="nsew", pady=(6, 0))
        main.rowconfigure(5, weight=1)
        tabs.columnconfigure(0, weight=1)
        tabs.rowconfigure(2, weight=1)
        bar = ttk.Frame(tabs)
        bar.grid(row=0, column=0, sticky="w")
        tk.Frame(tabs, height=1, bg=self.text_border).grid(row=1, column=0, sticky="ew", pady=(4, 0))
        self.tab_var = tk.StringVar(value="log")
        self.tab_pages = {"log": self._build_log_tab(tabs), "schedule": self._build_schedule_tab(tabs)}
        for key, text, glyph in (("log", "Log", "document"), ("schedule", "Schedule", "calendar")):
            try:
                rb = ttk.Radiobutton(bar, text=text, image=self.icons.glyph(glyph), compound="left",
                                     value=key, variable=self.tab_var, style="Toggle.TButton",
                                     command=self._show_tab)
            except tk.TclError:
                rb = ttk.Radiobutton(bar, text=text, value=key, variable=self.tab_var,
                                     command=self._show_tab)
            rb.pack(side="left", padx=(0, 6))
        self._show_tab()
        self.opt_win = None

    def _on_root_configure(self, e):
        if e.widget is not self.root:
            return
        size = (e.width, e.height)
        if self._last_size is None or self._hidden():
            self._last_size = size
            return
        if size == self._last_size:
            return
        self._last_size = size
        if not self._frozen:
            self._frozen = True
            self.main.pack_forget()
            self.cover.pack(fill="both", expand=True)
        if self._thaw_job is not None:
            self.root.after_cancel(self._thaw_job)
        self._thaw_job = self.root.after(220, self._thaw)

    def _thaw(self):
        self._thaw_job = None
        self._frozen = False
        self.cover.pack_forget()
        self.main.pack(fill="both", expand=True)

    def sty(self, name: str) -> str:
        """'Flat.<name>' when the flat styles are installed, else the theme default."""
        return f"Flat.{name}" if self.flat else name

    def _section(self, parent, row: int, title: str, sticky: str, pady):
        """Heading + hairline + body: looks like a group box, resizes cheaply."""
        box = ttk.Frame(parent)
        box.grid(row=row, column=0, sticky=sticky, pady=pady)
        box.columnconfigure(0, weight=1)
        box.rowconfigure(2, weight=1)
        label = ttk.Label(box, text=title, font=("Segoe UI Semibold", 10))
        label.grid(row=0, column=0, sticky="w")
        tk.Frame(box, height=1, bg=self.text_border).grid(row=1, column=0, sticky="ew", pady=(3, 6))
        body = ttk.Frame(box, padding=(2, 0, 2, 0))
        body.grid(row=2, column=0, sticky="nsew")
        return body, label

    def _show_tab(self):
        for key, page in self.tab_pages.items():
            if key == self.tab_var.get():
                page.grid(row=2, column=0, sticky="nsew", pady=(6, 0))
            else:
                page.grid_remove()

    def _build_log_tab(self, parent):
        f = ttk.Frame(parent)
        f.columnconfigure(0, weight=1)
        f.rowconfigure(0, weight=1)
        self.log_text = tk.Text(f, height=7, wrap="none", font=("Consolas", 9), relief="flat",
                                bg=self.text_bg, fg=self.text_fg, insertbackground=self.text_fg,
                                highlightthickness=1, highlightbackground=self.text_border,
                                highlightcolor=self.text_border)
        self.log_text.grid(row=0, column=0, sticky="nsew")
        ys = ttk.Scrollbar(f, orient="vertical", command=self.log_text.yview,
                           style=self.sty("Vertical.TScrollbar"))
        ys.grid(row=0, column=1, sticky="ns")
        self.log_text.configure(yscrollcommand=ys.set, state="disabled")
        self.log_text.tag_configure("warn", foreground="#f5c518" if self.dark else "#9d5d00")
        self.log_text.tag_configure("err", foreground="#ff99a4" if self.dark else "#c42b1c")
        self.log_text.tag_configure("ok", foreground="#6ccb5f" if self.dark else "#107c10")
        return f

    def _build_schedule_tab(self, parent):
        f = ttk.Frame(parent, padding=(0, 4, 0, 0))
        f.columnconfigure(1, weight=1)
        c = self.cfg
        self.auto_var = tk.BooleanVar(value=bool(c["auto_sync_enabled"]))
        row = ttk.Frame(f)
        row.grid(row=0, column=0, columnspan=3, sticky="w")
        ttk.Checkbutton(row, text="While Kramers is open, back up every", variable=self.auto_var,
                        command=self._schedule_changed).pack(side="left")
        self.auto_min_var = tk.StringVar(value=str(c["auto_sync_minutes"]))
        ttk.Spinbox(row, from_=5, to=1440, width=6, textvariable=self.auto_min_var,
                    command=self._schedule_changed).pack(side="left", padx=6)
        ttk.Label(row, text="minutes").pack(side="left")
        ttk.Separator(f).grid(row=1, column=0, columnspan=3, sticky="ew", pady=8)
        ttk.Label(f, text="Windows background schedule — runs even when Kramers is closed, and is "
                          "skipped quietly if the backup drive is not connected.").grid(
            row=2, column=0, columnspan=3, sticky="w")
        line = ttk.Frame(f)
        line.grid(row=3, column=0, columnspan=3, sticky="w", pady=6)
        self.freq_var = tk.StringVar(value=FREQ_TO_LABEL.get(c["schedule_freq"], FREQ_LABELS[0][1]))
        cb = ttk.Combobox(line, textvariable=self.freq_var, state="readonly",
                          values=[v for _, v in FREQ_LABELS], width=26)
        cb.pack(side="left")
        cb.bind("<<ComboboxSelected>>", lambda _e: self._sync_freq_fields())
        self.time_lbl = ttk.Label(line, text="at")
        self.time_lbl.pack(side="left", padx=(12, 4))
        self.time_var = tk.StringVar(value=c["schedule_time"])
        self.time_entry = ttk.Entry(line, textvariable=self.time_var, width=7)
        self.time_entry.pack(side="left")
        self.everyn_lbl = ttk.Label(line, text="every")
        self.everyn_lbl.pack(side="left", padx=(12, 4))
        self.everyn_var = tk.StringVar(value=str(c["schedule_every_hours"]))
        self.everyn_entry = ttk.Spinbox(line, from_=1, to=24, width=4, textvariable=self.everyn_var)
        self.everyn_entry.pack(side="left")
        ttk.Label(line, text="hours").pack(side="left", padx=(4, 0))
        btns = ttk.Frame(f)
        btns.grid(row=4, column=0, columnspan=3, sticky="w")
        ttk.Button(btns, text="Install / update schedule", image=self.icons.glyph("clock"),
                   compound="left", command=self._install_schedule, style=self.sty("TButton")).pack(side="left")
        ttk.Button(btns, text="Remove schedule", image=self.icons.glyph("delete"), compound="left",
                   command=self._remove_schedule, style=self.sty("TButton")).pack(side="left", padx=6)
        self.sched_status = ttk.Label(btns, text="")
        self.sched_status.pack(side="left", padx=(10, 0))
        self._sync_freq_fields()
        return f

    def _open_options(self):
        if self.opt_win is not None and self.opt_win.winfo_exists():
            self.opt_win.lift()
            self.opt_win.focus_force()
            return
        win = Dialog(self, "Kramers options")
        self.opt_win = win
        self._build_options(win.body)
        bottom = ttk.Frame(win.body)
        bottom.grid(row=12, column=0, columnspan=4, sticky="e", pady=(14, 0))

        def close():
            self._options_changed()
            win.destroy()
            self.opt_win = None
        ttk.Button(bottom, text="Close", style=self.sty("Accent.TButton"), command=close).pack()
        win.protocol("WM_DELETE_WINDOW", close)
        win.update_idletasks()
        r = self.root
        win.geometry(f"+{r.winfo_rootx() + 60}+{r.winfo_rooty() + 120}")

    def _build_options(self, f):
        g = self.cfg["guard"]
        ttk.Label(f, text="Files copied at once").grid(row=0, column=0, sticky="w", pady=4)
        self.workers_var = tk.StringVar(value=str(self.cfg.get("workers", 4)))
        ttk.Spinbox(f, from_=1, to=16, width=5, textvariable=self.workers_var,
                    command=self._options_changed).grid(row=0, column=1, sticky="w", padx=8)
        shield = ttk.Frame(f)
        shield.grid(row=1, column=0, columnspan=4, sticky="w", pady=(10, 4))
        ttk.Label(shield, image=self.icons.stock(winshell.SIID_SHIELD) or "").pack(side="left", padx=(0, 6))
        ttk.Label(shield, text="Mass-deletion guard: ask before removing more than").pack(side="left")
        self.g_frac = tk.StringVar(value=str(int(round(g["fraction"] * 100))))
        ttk.Spinbox(shield, from_=1, to=100, width=4, textvariable=self.g_frac,
                    command=self._options_changed).pack(side="left", padx=4)
        ttk.Label(shield, text="% of a set").pack(side="left")
        shield2 = ttk.Frame(f)
        shield2.grid(row=2, column=0, columnspan=4, sticky="w", padx=(26, 0))
        ttk.Label(shield2, text="(only when at least").pack(side="left")
        self.g_min = tk.StringVar(value=str(g["min_files"]))
        ttk.Spinbox(shield2, from_=1, to=100000, width=6, textvariable=self.g_min,
                    command=self._options_changed).pack(side="left", padx=4)
        ttk.Label(shield2, text="files would go), and always above").pack(side="left")
        self.g_max = tk.StringVar(value=str(g["max_files"]))
        ttk.Spinbox(shield2, from_=1, to=1000000, width=7, textvariable=self.g_max,
                    command=self._options_changed).pack(side="left", padx=4)
        ttk.Label(shield2, text="files.").pack(side="left")
        ttk.Label(f, text="Theme (applies next start)").grid(row=3, column=0, sticky="w", pady=(10, 4))
        self.theme_var = tk.StringVar(value=self.cfg.get("theme", "system").capitalize())
        tc = ttk.Combobox(f, textvariable=self.theme_var, state="readonly",
                          values=["System", "Light", "Dark"], width=10)
        tc.grid(row=3, column=1, sticky="w", padx=8, pady=(10, 4))
        tc.bind("<<ComboboxSelected>>", lambda _e: self._options_changed())
        self.lowprio_var = tk.BooleanVar(value=bool(self.cfg.get("low_priority", True)))
        ttk.Checkbutton(f, text="Back up at low priority (keeps the PC responsive)",
                        variable=self.lowprio_var, command=self._options_changed).grid(
            row=5, column=0, columnspan=4, sticky="w", pady=(8, 0))
        perf = ttk.Frame(f)
        perf.grid(row=6, column=0, columnspan=4, sticky="w", pady=(8, 0))
        ttk.Label(perf, text="Speed limit").pack(side="left")
        self.speed_var = tk.StringVar(value=str(int(self.cfg.get("max_mb_per_s", 0) or 0)))
        ttk.Spinbox(perf, from_=0, to=2000, increment=10, width=6, textvariable=self.speed_var,
                    command=self._options_changed).pack(side="left", padx=4)
        ttk.Label(perf, text="MB/s (0 = no limit; applies to the next backup)").pack(side="left")
        self.tray_var = tk.BooleanVar(value=bool(self.cfg.get("close_to_tray", True)))
        ttk.Checkbutton(f, text="Closing the window keeps Kramers running in the notification area "
                                "(automatic backups continue)", variable=self.tray_var,
                        command=self._options_changed).grid(row=4, column=0, columnspan=4,
                                                            sticky="w", pady=(8, 0))
        btns = ttk.Frame(f)
        keep = ttk.Frame(f)
        keep.grid(row=7, column=0, columnspan=4, sticky="w", pady=(8, 0))
        ttk.Label(keep, text="Versioned mode: keep archived versions for").pack(side="left")
        self.keep_var = tk.StringVar(value=str(int(self.cfg.get("archive_keep_days", 30) or 0)))
        ttk.Spinbox(keep, from_=0, to=3650, width=5, textvariable=self.keep_var,
                    command=self._options_changed).pack(side="left", padx=4)
        ttk.Label(keep, text="days (0 = forever)").pack(side="left")
        notif = ttk.Frame(f)
        notif.grid(row=8, column=0, columnspan=4, sticky="w", pady=(8, 0))
        ttk.Label(notif, text="After a scheduled backup, notify me").pack(side="left")
        self.notify_labels = {"problems": "only if something needs attention", "always": "always",
                              "never": "never"}
        self.notify_var = tk.StringVar(value=self.notify_labels.get(
            self.cfg.get("notify_scheduled", "problems"), self.notify_labels["problems"]))
        nc = ttk.Combobox(notif, textvariable=self.notify_var, state="readonly", width=32,
                          values=list(self.notify_labels.values()), style=self.sty("TCombobox"))
        nc.pack(side="left", padx=6)
        nc.bind("<<ComboboxSelected>>", lambda _e: self._options_changed())
        enc = ttk.Frame(f)
        enc.grid(row=9, column=0, columnspan=4, sticky="ew", pady=(12, 0))
        ttk.Label(enc, image=self.icons.stock(winshell.SIID_SHIELD) or "").grid(row=0, column=0, padx=(0, 6))
        ttk.Label(enc, text="Backup drive encryption:").grid(row=0, column=1, sticky="w")
        serial = config.check_destination(self.cfg)[2]
        self.opt_enc_label = ttk.Label(enc, text=self.ENCRYPTION_TEXT.get(
            self._bitlocker.get(serial, "unknown"), ""), wraplength=520, justify="left")
        self.opt_enc_label.grid(row=0, column=2, sticky="w", padx=(6, 0))
        ttk.Button(enc, text="BitLocker settings…", style=self.sty("TButton"),
                   command=lambda: subprocess.Popen(
                       ["control", "/name", "Microsoft.BitLockerDriveEncryption"])).grid(
            row=0, column=3, padx=(10, 0))
        self.warn_enc_var = tk.BooleanVar(value=bool(self.cfg.get("warn_unencrypted", False)))
        ttk.Checkbutton(enc, text="Remind me in the main window when the backup drive is not encrypted",
                        variable=self.warn_enc_var, command=self._options_changed).grid(
            row=1, column=1, columnspan=3, sticky="w", pady=(6, 0))
        btns.grid(row=10, column=0, columnspan=4, sticky="w", pady=(14, 0))
        ttk.Button(btns, text="Create Desktop & Start Menu shortcuts", image=self.icons.glyph("link"),
                   compound="left", command=self._make_shortcuts, style=self.sty("TButton")).pack(side="left")
        ttk.Button(btns, text="Open settings folder", image=self.icons.glyph("settings"),
                   style=self.sty("TButton"),
                   compound="left", command=lambda: open_path(str(config.config_dir()))
                   ).pack(side="left", padx=6)
        ttk.Label(f, text=f"Settings: {config.config_path()}", foreground="#888").grid(
            row=11, column=0, columnspan=4, sticky="w", pady=(10, 0))

    # ------------------------------------------------------------------ jobs list
    def _job(self, jid):
        return next((j for j in self.cfg["jobs"] if j["id"] == jid), None)

    def _source_text(self, job):
        if job["preset"] in ("folder", "endnote"):
            return presets.expand_path(job["source"] or presets.ENDNOTE_DEFAULT)
        tmp_cfg = dict(self.cfg, dest_root=self.cfg.get("dest_root") or "X:\\")
        roots = [(s, r, on) for s, r, on in config.job_roots(tmp_cfg, job)]
        here = [r for s, r, on in roots if on and os.path.exists(r.source)]
        return f"{len(here)} location{'s' if len(here) != 1 else ''} — " + \
            ", ".join(Path(r.source).name for r in here[:3]) + ("…" if len(here) > 3 else "")

    def _load_jobs(self):
        sel = self.tree.selection()
        self.tree.delete(*self.tree.get_children())
        total_b = total_f = 0
        for job in self.cfg["jobs"]:
            st = self.state.get("jobs", {}).get(job["id"], {})
            last = human_when(st.get("last_run", ""))
            if st.get("status"):
                last += "  " + STATUS_TEXT.get(st["status"], "")
            mode = job.get("mode") or self.cfg["default_mode"]
            mode_text = {"mirror": "Exact mirror", "versioned": "Versioned", "add_update": "Add only"}[mode]
            if job.get("mode"):
                mode_text += " *"        # overrides the default mode
            if job.get("every_days"):
                days = int(job["every_days"])
                mode_text += " · " + {1: "daily", 7: "weekly", 30: "monthly"}.get(days, f"every {days} d")
            dest = os.path.join(self.cfg.get("dest_root", ""), job["subdir"])
            size = "—"
            if "size_bytes" in st:
                size = f"{human_bytes(st['size_bytes'])} · {human_count(st.get('files', 0))} files"
                if job.get("enabled"):
                    total_b += st["size_bytes"]
                    total_f += st.get("files", 0)
            self.tree.insert("", "end", iid=job["id"], text="  " + job["name"], image=self.icons.job(job),
                             values=("✓" if job.get("enabled") else "", self._source_text(job),
                                     dest, size, mode_text, last))
        self.sets_title.configure(
            text="Backup sets" + (f" — {human_bytes(total_b)} in {human_count(total_f)} files"
                                  if total_b else " — size appears after a preview or backup"))
        if sel and self.tree.exists(sel[0]):
            self.tree.selection_set(sel)

    def _tree_click(self, evt):
        if self.tree.identify_column(evt.x) == "#1" and self.tree.identify_region(evt.x, evt.y) == "cell":
            row = self.tree.identify_row(evt.y)
            if row:
                self.tree.selection_set(row)
                self._toggle_selected()
                return "break"

    def _tree_double(self, evt):
        if self.tree.identify_row(evt.y) and self.tree.identify_column(evt.x) != "#1":
            self._edit_selected()

    def _toggle_selected(self):
        for jid in self.tree.selection():
            job = self._job(jid)
            job["enabled"] = not job.get("enabled")
        self._save()
        self._load_jobs()

    def _tree_menu(self, evt):
        row = self.tree.identify_row(evt.y)
        if not row:
            return
        self.tree.selection_set(row)
        job = self._job(row)
        m = tk.Menu(self.root, tearoff=0)
        m.add_command(label="Back up this set now", command=lambda: self._start(False, [row]))
        m.add_command(label="Preview this set", command=lambda: self._start(True, [row]))
        m.add_separator()
        m.add_command(label="Open backup folder",
                      command=lambda: open_path(os.path.join(self.cfg["dest_root"], job["subdir"])))
        if job["preset"] in ("folder", "endnote"):
            m.add_command(label="Open source folder",
                          command=lambda: open_path(presets.expand_path(job["source"])))
        m.add_command(label="Restore from the backup…", command=self._restore_selected)
        m.add_command(label="Edit…", command=self._edit_selected)
        m.add_command(label="Include" if not job.get("enabled") else "Exclude from backups",
                      command=self._toggle_selected)
        m.tk_popup(evt.x_root, evt.y_root)

    def _add_folder(self):
        d = filedialog.askdirectory(parent=self.root, title="Choose a folder to back up")
        if not d:
            return
        d = os.path.normpath(d)
        name = Path(d).name or d
        taken = {j["subdir"].lower() for j in self.cfg["jobs"]}
        sub, n = name, 2
        while sub.lower() in taken:
            sub, n = f"{name} ({n})", n + 1
        ids = {j["id"] for j in self.cfg["jobs"]}
        jid, n = "folder-1", 1
        while jid in ids:
            n += 1
            jid = f"folder-{n}"
        job = config.new_job(jid, name, "folder", source=d, subdir=sub)
        res = JobDialog(self, job).show()
        if res:
            self.cfg["jobs"].append(res)
            self._save()
            self._load_jobs()

    def _edit_selected(self):
        sel = self.tree.selection()
        if not sel:
            return
        job = self._job(sel[0])
        res = JobDialog(self, job).show()
        if res:
            job.update(res)
            self._save()
            self._load_jobs()

    def _remove_selected(self):
        sel = self.tree.selection()
        if not sel:
            return
        job = self._job(sel[0])
        if messagebox.askyesno(APP_NAME, f"Remove the backup set '{job['name']}'?\n\nFiles already in "
                               f"the backup ({job['subdir']}) are not deleted.", parent=self.root):
            self.cfg["jobs"] = [j for j in self.cfg["jobs"] if j["id"] != job["id"]]
            self._save()
            self._load_jobs()

    # ------------------------------------------------------------------ destination
    def _pick_dest(self):
        d = filedialog.askdirectory(parent=self.root, title="Choose the backup destination folder",
                                    initialdir=self.dest_var.get() or "/")
        if d:
            self.dest_var.set(os.path.normpath(d))
            self._dest_changed()

    def _dest_changed(self):
        new = os.path.normpath(self.dest_var.get().strip()) if self.dest_var.get().strip() else ""
        if new == self.cfg.get("dest_root"):
            return
        old_drive = os.path.splitdrive(self.cfg.get("dest_root", ""))[0].lower()
        self.cfg["dest_root"] = new
        if os.path.splitdrive(new)[0].lower() != old_drive:
            self.cfg["dest_volume_serial"] = ""      # recorded on the next run
        self._save()
        self._load_jobs()
        self._refresh_dest()

    def _refresh_dest(self):
        status, msg, serial, label = config.check_destination(self.cfg)
        root = self.cfg.get("dest_root", "")
        drive = os.path.splitdrive(root)[0] + "\\" if root else ""
        img = self.icons.shell(drive) if status != "offline" and drive else \
            self.icons.stock(winshell.SIID_DRIVEREMOVE)
        self.drive_icon.configure(image=img or "")
        self.use_drive_btn.grid_forget()
        if status == "ok":
            try:
                free = human_bytes(shutil.disk_usage(drive).free)
            except OSError:
                free = "?"
            self.dest_info.configure(text=f"✓ '{label}' connected · {free} free · drive {serial}",
                                     foreground="#6ccb5f" if self.dark else "#107c10")
            self._check_encryption(root, serial)
        elif not root:
            self.dest_info.configure(text="Choose where to back up — an external drive is best.",
                                     foreground="#f5c518" if self.dark else "#9d5d00")
        elif status == "offline":
            self.dest_info.configure(text="Backup drive not connected — plug it in to back up.",
                                     foreground="#f5c518" if self.dark else "#9d5d00")
        else:
            self.dest_info.configure(text=msg, foreground="#ff99a4" if self.dark else "#c42b1c",
                                     wraplength=700)
            self.use_drive_btn.grid(row=1, column=4, sticky="e", pady=(6, 0))

    def _check_encryption(self, root: str, serial: str):
        """BitLocker state of the backup drive, looked up off the UI thread and cached."""
        if serial in self._bitlocker:
            self._show_encryption(self._bitlocker[serial])
            return
        self._bitlocker[serial] = "checking"

        def look():
            self.q.put(("bitlocker", (serial, winshell.bitlocker_state(root))))
        threading.Thread(target=look, daemon=True, name="kramers-bitlocker").start()

    ENCRYPTION_TEXT = {"on": "Encrypted with BitLocker.",
                       "off": "Not encrypted. Your backups include private files (documents, AI "
                              "conversations); anyone holding the drive can read them.",
                       "checking": "Checking…", "unknown": "Unknown (BitLocker status not available)."}

    def _show_encryption(self, state: str):
        """Update the Options window, and the main-window reminder if it is switched on."""
        if self.opt_enc_label is not None and self.opt_enc_label.winfo_exists():
            self.opt_enc_label.configure(text=self.ENCRYPTION_TEXT.get(state, ""))
        if state == "off" and self.cfg.get("warn_unencrypted"):
            self.enc_info.configure(text="⚠ This backup drive is not encrypted (see Options).",
                                    foreground="#f5c518" if self.dark else "#9d5d00")
            self.enc_frame.grid(row=3, column=1, columnspan=4, sticky="w", pady=(6, 0))
        else:
            self.enc_frame.grid_forget()

    def _adopt_drive(self):
        status, msg, serial, label = config.check_destination(self.cfg)
        if serial and messagebox.askyesno(
                APP_NAME, f"Use '{label}' (serial {serial}) as the backup drive from now on?",
                parent=self.root):
            self.cfg["dest_volume_serial"] = serial
            self._save()
            self._refresh_dest()

    def _mode_changed(self):
        self.cfg["default_mode"] = LABEL_TO_MODE.get(self.mode_var.get(), "mirror")
        self._save()
        self._load_jobs()

    # ------------------------------------------------------------------ settings
    def _save(self):
        try:
            config.save_config(self.cfg)
        except OSError as exc:
            messagebox.showerror(APP_NAME, f"Could not save settings:\n{exc}", parent=self.root)

    @staticmethod
    def _int(s, default, lo=1):
        try:
            return max(lo, int(float(s)))
        except (TypeError, ValueError):
            return default

    def _options_changed(self):
        if self.opt_win is None or not self.opt_win.winfo_exists():
            return
        self.cfg["workers"] = min(16, self._int(self.workers_var.get(), 4))
        self.cfg["guard"] = {"fraction": min(100, self._int(self.g_frac.get(), 20)) / 100.0,
                             "min_files": self._int(self.g_min.get(), 50),
                             "max_files": self._int(self.g_max.get(), 5000)}
        self.cfg["theme"] = self.theme_var.get().lower()
        self.cfg["close_to_tray"] = bool(self.tray_var.get())
        self.cfg["low_priority"] = bool(self.lowprio_var.get())
        self.cfg["max_mb_per_s"] = self._int(self.speed_var.get(), 0, 0)
        self.cfg["archive_keep_days"] = self._int(self.keep_var.get(), 30, 0)
        self.cfg["warn_unencrypted"] = bool(self.warn_enc_var.get())
        serial = config.check_destination(self.cfg)[2]
        if serial in self._bitlocker:
            self._show_encryption(self._bitlocker[serial])
        self.cfg["notify_scheduled"] = next((k for k, v in self.notify_labels.items()
                                             if v == self.notify_var.get()), "problems")
        self._save()

    def _schedule_changed(self):
        self.cfg["auto_sync_enabled"] = bool(self.auto_var.get())
        self.cfg["auto_sync_minutes"] = self._int(self.auto_min_var.get(), 30, 5)
        self._save()
        self._arm_autosync()

    # ------------------------------------------------------------------ run
    def _busy(self, busy: bool):
        self.run_btn.configure(state="disabled" if busy else "normal")
        self.preview_btn.configure(state="disabled" if busy else "normal")
        self.cancel_btn.configure(state="normal" if busy else "disabled")
        self.pause_btn.configure(state="normal" if busy else "disabled")
        if not busy:
            self.pause.clear()
            self.pause_btn.configure(text="Pause", image=self.icons.glyph("pause"))
        self._update_tray(busy)

    def _restore_selected(self):
        sel = self.tree.selection()
        if not sel:
            messagebox.showinfo(APP_NAME, "Select the backup set to restore.", parent=self.root)
            return
        if self.worker and self.worker.is_alive():
            messagebox.showinfo(APP_NAME, "Wait for the current backup to finish.", parent=self.root)
            return
        choice = RestoreDialog(self, self._job(sel[0])).show()
        if choice:
            self._start(choice["preview"], restore=choice["params"])

    def _start(self, dry_run: bool, job_ids=None, restore=None):
        if self.worker and self.worker.is_alive():
            return
        self._dest_changed()
        self._options_changed()
        problems = config.validate(self.cfg)
        if problems:
            messagebox.showerror(APP_NAME, "\n".join(problems), parent=self.root)
            return
        status, msg, *_ = config.check_destination(self.cfg)
        if status != "ok":
            self._refresh_dest()
            messagebox.showwarning(APP_NAME, msg, parent=self.root)
            return
        self.pbar.configure(value=0)
        self.pct_var.set("")
        self._rate_samples = []
        self.worker = BackupProcess(copy.deepcopy(self.cfg), job_ids, dry_run, restore)
        self.cancel, self.pause = self.worker.cancel, self.worker.pause
        self.worker.start()
        self._busy(True)

    def _on_cancel(self):
        self.cancel.set()
        self.pause.clear()
        self._log("Cancel requested…")

    def _toggle_pause(self):
        if not (self.worker and self.worker.is_alive()):
            return
        if self.pause.is_set():
            self.pause.clear()
            self.pause_btn.configure(text="Pause", image=self.icons.glyph("pause"))
            self.phase_var.set("Resuming…")
            self._log("Resumed.")
        else:
            self.pause.set()
            self.pause_btn.configure(text="Resume", image=self.icons.glyph("play"))
            self.phase_var.set("Paused — click Resume to continue")
            self._log("Paused.")
        self._update_tray()

    def _pump(self):
        try:
            while True:
                kind, payload = self.q.get_nowait()
                if kind == "tray":
                    self._tray_action(payload)
                elif kind == "bitlocker":
                    serial, state = payload
                    self._bitlocker[serial] = state
                    if serial == config.check_destination(self.cfg)[2]:
                        self._show_encryption(state)
        except queue.Empty:
            pass
        w = self.worker
        if w is not None and not w.finished:
            lines, last_progress, done = [], None, None
            try:
                for _ in range(2000):                 # bounded: the window always stays responsive
                    msg = w.events.get_nowait()
                    if msg[0] == "log":
                        lines.append(msg[1])
                    elif msg[0] == "progress":
                        last_progress = msg[1]
                    elif msg[0] == "confirm":
                        self._log_many(lines)
                        lines = []
                        self._confirm(w, msg[1])
                    elif msg[0] == "done":
                        done = msg
                        break
            except queue.Empty:
                pass
            self._log_many(lines)
            if last_progress:
                self._apply_progress(last_progress)
            if done is None and not w.proc.is_alive():
                try:                                  # drain anything sent just before exit
                    done = next(m for m in iter(lambda: w.events.get(timeout=0.5), None)
                                if m[0] == "done")
                except (queue.Empty, StopIteration):
                    done = ("done", None, "")
                    self._log("ERROR: the backup process ended unexpectedly.")
            if done is not None:
                w.finished = True
                self._finished(w.dry_run, done[1], done[2], "restore" if w.restore else "backup")
        self.root.after(100, self._pump)

    def _confirm(self, w: BackupProcess, req: dict):
        if self._hidden():
            self._show()
            self._notify("Kramers needs a decision",
                         f"{req['label']}: {req['n']:,} files would be removed from the backup.",
                         "warning")
        w.replies.put(bool(ConfirmDeletesDialog(self, req).show()))

    def _finished(self, dry_run, result, serial, what="backup"):
        self._busy(False)
        if serial and not self.cfg.get("dest_volume_serial"):
            self.cfg["dest_volume_serial"] = serial
        self.state = config.load_state()
        self._load_jobs()
        self._refresh_dest()
        if result is None:
            self.phase_var.set("Stopped by an error — see the log")
            self._update_tray()
            return
        self._apply_progress(result["progress"])
        if result["message"]:
            self.phase_var.set(result["message"])
        self._update_tray()
        p = result["progress"]
        if self._hidden() and not dry_run:
            if result["message"]:
                self._notify(f"Kramers {what} skipped", result["message"], "warning")
            elif p["phase"] == "done":
                level = "error" if p["errors"] else ("warning" if p["warnings"] or p["skipped"] else "info")
                self._notify(f"Kramers {what} finished",
                             f"Copied {p['copied']:,}, updated {p['updated']:,}, removed {p['deleted']:,}"
                             + (f", {p['skipped']:,} in use" if p["skipped"] else "")
                             + (f", {p['errors']:,} errors" if p["errors"] else "") + ".", level)
        if dry_run and result["plans"]:
            self.last_preview = result
            if self._hidden():
                self._show()
            PreviewDialog(self, result, what)

    def _apply_progress(self, s: dict):
        phase = s.get("phase", "")
        if self.tray and time.time() - self._tray_t > 1.0:
            self._tray_t = time.time()
            bt, bd = s.get("bytes_total", 0), s.get("bytes_done", 0)
            pct = f" {100 * bd / bt:.0f}%" if bt and phase == "copying" else ""
            self.tray.set_tooltip(f"{APP_NAME} — {phase}{pct}")
        nice = {"scanning": "Scanning", "copying": "Copying", "deleting": "Removing",
                "done": "Finished", "cancelled": "Cancelled", "error": "Stopped"}.get(phase, phase)
        label = s.get("label", "")
        if self.pause.is_set() and phase not in ("done", "cancelled", "error"):
            nice = "Paused — click Resume to continue"
            label = ""
        self.phase_var.set(f"{nice} — {label}" if label and phase in ("scanning",) else nice)
        bt, bd = s.get("bytes_total", 0), s.get("bytes_done", 0)
        ft, fd = s.get("files_total", 0), s.get("files_done", 0)
        if phase == "scanning":
            self.pbar.configure(mode="indeterminate")
            self.pbar.start(15)
        else:
            self.pbar.stop()
            self.pbar.configure(mode="determinate")
            pct = 100 * bd / bt if bt else (100 * fd / ft if ft else (100 if phase == "done" else 0))
            self.pbar.configure(value=min(100, pct))
            self.pct_var.set(f"{min(100, pct):.0f}%")
        cur = s.get("current", "")
        self.current_var.set(cur if cur else "")
        elapsed = s.get("elapsed", 0.0)
        eta = ""
        if phase == "copying" and bt:
            now = time.monotonic()
            self._rate_samples.append((now, bd))
            while len(self._rate_samples) > 2 and now - self._rate_samples[0][0] > 20:
                self._rate_samples.pop(0)          # rate over the last ~20 s
            t0, b0 = self._rate_samples[0]
            if now - t0 >= 5 and bd > b0:
                rate = (bd - b0) / (now - t0)
                left = (bt - bd) / rate
                when = f"about {int(left // 60)} min left" if left >= 90 else "under 2 min left"
                eta = f" · {human_bytes(rate)}/s · {when}"
            else:
                eta = " · estimating time…"
        self.stats_var.set(
            f"copied {s.get('copied', 0):,} · updated {s.get('updated', 0):,} · "
            f"removed {s.get('deleted', 0):,} · archived {s.get('archived', 0):,} · "
            f"in use {s.get('skipped', 0):,} · gone {s.get('vanished', 0):,} · "
            f"warnings {s.get('warnings', 0):,} · "
            f"errors {s.get('errors', 0):,} · {human_bytes(bd)} of {human_bytes(bt)} · "
            f"{int(elapsed)} s{eta}")

    def _log_many(self, lines: list):
        if not lines:
            return
        self.log_text.configure(state="normal")
        stamp = time.strftime("[%H:%M:%S] ")
        for msg in lines[-1500:]:
            self.log_text.insert("end", stamp + msg + "\n", self._log_tag(msg))
        n = int(self.log_text.index("end-1c").split(".")[0])
        if n > 5000:
            self.log_text.delete("1.0", f"{n - 5000}.0")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    @staticmethod
    def _log_tag(msg: str):
        if msg.startswith(("ERROR", "COPY ERROR", "DELETE ERROR", "STOPPED")):
            return ("err",)
        if msg.startswith(("WARNING", "GUARD", "SKIPPED", "SCAN WARNING", "GONE")):
            return ("warn",)
        if msg.startswith("Done."):
            return ("ok",)
        return ()

    def _log(self, msg: str):
        tag = ()
        if msg.startswith(("ERROR", "COPY ERROR", "DELETE ERROR", "STOPPED")):
            tag = ("err",)
        elif msg.startswith(("WARNING", "GUARD", "SKIPPED", "SCAN WARNING")):
            tag = ("warn",)
        elif msg.startswith("Done."):
            tag = ("ok",)
        self.log_text.configure(state="normal")
        self.log_text.insert("end", time.strftime("[%H:%M:%S] ") + msg + "\n", tag)
        lines = int(self.log_text.index("end-1c").split(".")[0])
        if lines > 5000:
            self.log_text.delete("1.0", f"{lines - 5000}.0")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    # ------------------------------------------------------------------ auto-sync
    def _arm_autosync(self):
        if self.auto_after is not None:
            self.root.after_cancel(self.auto_after)
            self.auto_after = None
        if self.cfg.get("auto_sync_enabled"):
            minutes = self._int(self.cfg.get("auto_sync_minutes"), 30, 5)
            self.auto_after = self.root.after(minutes * 60_000, self._auto_fire)
            self._log(f"Auto-backup armed: every {minutes} min while Kramers is open.")

    def _auto_fire(self):
        if not (self.worker and self.worker.is_alive()):
            self._log("Auto-backup started.")
            self._start(False)
        self._arm_autosync()

    # ------------------------------------------------------------------ schedule
    def _sync_freq_fields(self):
        freq = LABEL_TO_FREQ.get(self.freq_var.get(), "DAILY")
        for w in (self.time_lbl, self.time_entry):
            w.configure(state="normal" if freq == "DAILY" else "disabled")
        for w in (self.everyn_lbl, self.everyn_entry):
            w.configure(state="normal" if freq == "EVERY_N_HOURS" else "disabled")

    def _runner_command(self) -> str:
        exe, args = shortcuts.launch_command("--run")
        return " ".join(f'"{a}"' if " " in a or a.endswith((".pyw", ".exe")) else a
                        for a in [exe, *args])

    def _install_schedule(self):
        c = self.cfg
        c["schedule_freq"] = LABEL_TO_FREQ.get(self.freq_var.get(), "DAILY")
        c["schedule_time"] = self.time_var.get().strip() or "18:00"
        c["schedule_every_hours"] = self._int(self.everyn_var.get(), 4)
        self._save()
        cmd = ["schtasks", "/Create", "/TN", config.TASK_NAME, "/TR", self._runner_command(),
               "/F", "/RL", "LIMITED"]
        cmd += {"DAILY": ["/SC", "DAILY", "/ST", c["schedule_time"]],
                "HOURLY": ["/SC", "HOURLY"],
                "EVERY_N_HOURS": ["/SC", "HOURLY", "/MO", str(c["schedule_every_hours"])],
                "ONLOGON": ["/SC", "ONLOGON"]}[c["schedule_freq"]]
        out = subprocess.run(cmd, capture_output=True, text=True, creationflags=shortcuts.NO_WINDOW)
        if out.returncode == 0:
            self._log("Windows schedule installed/updated.")
        else:
            self._log("ERROR: schtasks: " + (out.stderr or out.stdout).strip())
            messagebox.showerror(APP_NAME, "Could not create the schedule:\n" + (out.stderr or out.stdout),
                                 parent=self.root)
        self._refresh_schedule_status()

    def _remove_schedule(self):
        out = subprocess.run(["schtasks", "/Delete", "/TN", config.TASK_NAME, "/F"],
                             capture_output=True, text=True, creationflags=shortcuts.NO_WINDOW)
        self._log("Schedule removed." if out.returncode == 0 else
                  "Remove: " + (out.stderr or out.stdout).strip())
        self._refresh_schedule_status()

    def _refresh_schedule_status(self):
        out = subprocess.run(["schtasks", "/Query", "/TN", config.TASK_NAME, "/FO", "LIST"],
                             capture_output=True, text=True, creationflags=shortcuts.NO_WINDOW)
        if out.returncode == 0:
            nxt = next((ln.split(":", 1)[1].strip() for ln in out.stdout.splitlines()
                        if ln.lower().startswith("next run time")), "")
            self.sched_status.configure(text=f"✓ Background schedule '{config.TASK_NAME}' is installed."
                                             + (f" Next run: {nxt}." if nxt else ""))
        else:
            self.sched_status.configure(text="No background schedule installed.")

    # ------------------------------------------------------------------ misc
    def _make_shortcuts(self):
        try:
            links = shortcuts.install()
            self._log("Shortcuts created: " + "; ".join(links))
            messagebox.showinfo(APP_NAME, "Shortcuts created:\n" + "\n".join(links), parent=self.root)
        except Exception as exc:
            messagebox.showerror(APP_NAME, f"Could not create shortcuts:\n{exc}", parent=self.root)

    # ------------------------------------------------------------------ tray
    def _hidden(self) -> bool:
        return self.root.state() == "withdrawn"

    def _show(self):
        self.root.deiconify()
        self.root.state("normal")
        self.root.lift()
        self.root.attributes("-topmost", True)
        self.root.after(200, lambda: self.root.attributes("-topmost", False))
        self.root.focus_force()

    def _notify(self, title: str, text: str, level: str = "info"):
        if self.tray:
            self.tray.notify(title, text, level)

    def _update_tray(self, busy=None):
        if not getattr(self, "tray", None):
            return
        busy = bool(self.worker and self.worker.is_alive()) if busy is None else busy
        last = human_when(self.state.get("last_run", ""))
        statuses = {v.get("status") for v in self.state.get("jobs", {}).values()}
        worst = next((s for s in ("error", "warning", "skipped", "ok") if s in statuses), None)
        last_text = f"Last backup: {last}" + (f"  {STATUS_TEXT[worst]}" if worst else "")
        items = [("open", "Open Kramers", True, True)]
        if busy:
            items.append(("pause", "Resume backup" if self.pause.is_set() else "Pause backup", True, False))
            items.append(("cancel", "Cancel backup", True, False))
        else:
            items.append(("backup", "Back up now", True, False))
        items += [(None, "", False, False), ("info", last_text, False, False),
                  (None, "", False, False), ("quit", "Quit Kramers", True, False)]
        self.tray.menu_items = items
        self.tray.set_tooltip(f"{APP_NAME} — {'backing up' if busy else last_text.lower()}")

    def _tray_action(self, key: str):
        if key in ("open", "balloon"):
            self._show()
        elif key == "backup":
            self._start(False)
        elif key == "cancel":
            self._on_cancel()
        elif key == "pause":
            self._toggle_pause()
        elif key == "quit":
            self._quit()

    def _on_close(self):
        if self.tray and self.cfg.get("close_to_tray", True):
            self.root.withdraw()
            if not self.state.get("tray_hint_shown"):
                self._notify(APP_NAME, "Kramers is still running here; automatic backups continue. "
                                       "Right-click the icon to quit.")
                self.state["tray_hint_shown"] = True
                try:
                    config.save_state(self.state)
                except OSError:
                    pass
            return
        self._quit()

    def _quit(self):
        if self.worker and self.worker.is_alive():
            self._show()
            if not messagebox.askyesno(APP_NAME, "A backup is running. Cancel it and quit Kramers?",
                                       parent=self.root):
                return
            self.cancel.set()
            self.pause.clear()
            self.worker.join(timeout=15)
        if self.tray:
            self.tray.stop()
        self.root.destroy()


def main():
    if tray_mod is not None and not tray_mod.claim_single_instance():
        return                     # the running Kramers was asked to show its window
    winshell.enable_dpi_awareness()
    winshell.set_app_user_model_id(APP_ID)
    root = tk.Tk()
    root.withdraw()
    splash = Splash(root)
    splash.step("Reading settings")
    cfg = config.load_config()
    app = App(root, cfg, splash)

    def show():
        root.deiconify()
        winshell.set_dark_title_bar(root, app.dark)
        root.lift()
        root.focus_force()
    splash.close(then=show)
    root.mainloop()


if __name__ == "__main__":
    main()
