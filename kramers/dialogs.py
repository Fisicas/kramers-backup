"""dialogs.py -- the Kramers dialogs (backup set, confirm removals, preview, restore)."""
from __future__ import annotations

import copy
import os
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from typing import TYPE_CHECKING

from . import APP_NAME, config, presets, winshell
from .ui_common import (DEFAULT_MODE_LABEL, FREQUENCIES, FREQUENCY_TO_LABEL, LABEL_TO_FREQUENCY,
                        LABEL_TO_MODE, MODE_TO_LABEL, human_bytes)

if TYPE_CHECKING:
    from .gui import App

# --------------------------------------------------------------------------- #
# Dialogs
# --------------------------------------------------------------------------- #

class Dialog(tk.Toplevel):
    def __init__(self, app: "App", title: str):
        super().__init__(app.root)
        self.app = app
        self.title(title)
        self.transient(app.root)
        self.resizable(True, True)
        self.body = ttk.Frame(self, padding=16)
        self.body.pack(fill="both", expand=True)
        self.result = None
        winshell.set_dark_title_bar(self, app.dark)
        try:
            self.iconbitmap(str(config.RESOURCES / "kramers.ico"))
        except tk.TclError:
            pass

    def sty(self, name: str) -> str:
        return self.app.sty(name)

    def show(self):
        self.update_idletasks()
        r = self.app.root
        x = r.winfo_rootx() + (r.winfo_width() - self.winfo_reqwidth()) // 2
        y = r.winfo_rooty() + max(0, (r.winfo_height() - self.winfo_reqheight()) // 3)
        self.geometry(f"+{max(0, x)}+{max(0, y)}")
        self.grab_set()
        self.focus_set()
        self.wait_window()
        return self.result


class JobDialog(Dialog):
    """Edit one backup set."""

    def __init__(self, app: "App", job: dict):
        super().__init__(app, f"Backup set — {job['name']}")
        self.job = copy.deepcopy(job)
        b = self.body
        b.columnconfigure(1, weight=1)
        preset = config.job_preset(self.job)
        has_source = self.job["preset"] in ("folder", "endnote")

        head = ttk.Frame(b)
        head.grid(row=0, column=0, columnspan=3, sticky="ew", pady=(0, 10))
        icon = app.icons.job(self.job)
        ttk.Label(head, image=icon).pack(side="left", padx=(0, 8))
        ttk.Label(head, text=preset.title if preset else self.job["preset"],
                  font=("Segoe UI Semibold", 12)).pack(side="left")
        if preset and self.job["preset"] != "folder":
            ttk.Label(b, text=preset.description, wraplength=620, justify="left").grid(
                row=1, column=0, columnspan=3, sticky="w", pady=(0, 10))

        r = 2
        ttk.Label(b, text="Name").grid(row=r, column=0, sticky="w", pady=4)
        self.name_var = tk.StringVar(value=self.job["name"])
        ttk.Entry(b, textvariable=self.name_var).grid(row=r, column=1, columnspan=2, sticky="ew", pady=4)
        r += 1
        if has_source:
            ttk.Label(b, text="Source folder").grid(row=r, column=0, sticky="w", pady=4)
            self.src_var = tk.StringVar(value=self.job["source"])
            ttk.Entry(b, textvariable=self.src_var).grid(row=r, column=1, sticky="ew", pady=4)
            ttk.Button(b, text="Browse…", image=app.icons.glyph("open"), compound="left",
                       command=self._browse, style=self.sty("TButton")).grid(row=r, column=2, padx=(6, 0), pady=4)
            r += 1
        ttk.Label(b, text="Backup subfolder").grid(row=r, column=0, sticky="w", pady=4)
        self.sub_var = tk.StringVar(value=self.job["subdir"])
        ttk.Entry(b, textvariable=self.sub_var).grid(row=r, column=1, columnspan=2, sticky="ew", pady=4)
        r += 1
        ttk.Label(b, text="When files are removed").grid(row=r, column=0, sticky="w", pady=4)
        default_label = f"{DEFAULT_MODE_LABEL} ({MODE_TO_LABEL[app.cfg['default_mode']].split(' (')[0]})"
        self.mode_labels = [default_label] + [v for _, v in config.MODE_LABELS]
        cur = MODE_TO_LABEL.get(self.job.get("mode") or "", default_label)
        self.mode_var = tk.StringVar(value=cur)
        ttk.Combobox(b, textvariable=self.mode_var, state="readonly", values=self.mode_labels,
                     style=self.sty("TCombobox")).grid(row=r, column=1, columnspan=2, sticky="ew", pady=4)
        r += 1
        ttk.Label(b, text="Back up").grid(row=r, column=0, sticky="w", pady=4)
        self.freq_var = tk.StringVar(value=FREQUENCY_TO_LABEL.get(int(self.job.get("every_days") or 0),
                                                                  FREQUENCIES[0][1]))
        ttk.Combobox(b, textvariable=self.freq_var, state="readonly",
                     values=[v for _, v in FREQUENCIES], style=self.sty("TCombobox")).grid(
            row=r, column=1, columnspan=2, sticky="ew", pady=4)
        r += 1

        if preset and len(preset.roots) > 1 or self.job["preset"] in ("claude", "codex", "environments"):
            ttk.Label(b, text="Included locations (double-click to switch on/off)").grid(
                row=r, column=0, columnspan=3, sticky="w", pady=(12, 4))
            r += 1
            frame = ttk.Frame(b)
            frame.grid(row=r, column=0, columnspan=3, sticky="nsew")
            b.rowconfigure(r, weight=1)
            self.roots_tree = ttk.Treeview(frame, columns=("on", "source", "found"), show="headings",
                                           height=min(9, max(3, len(preset.roots) + 2)),
                                           style=app.sty("Treeview"))
            for col, text, w in (("on", "Include", 70), ("source", "Location", 520), ("found", "On this PC", 90)):
                self.roots_tree.heading(col, text=text)
                self.roots_tree.column(col, width=w, stretch=(col == "source"),
                                       anchor="center" if col != "source" else "w")
            self.roots_tree.pack(side="left", fill="both", expand=True)
            sb = ttk.Scrollbar(frame, orient="vertical", command=self.roots_tree.yview,
                               style=app.sty("Vertical.TScrollbar"))
            sb.pack(side="right", fill="y")
            self.roots_tree.configure(yscrollcommand=sb.set)
            tmp_cfg = dict(app.cfg, dest_root=app.cfg.get("dest_root") or "X:\\")
            for spec, root, on in config.job_roots(tmp_cfg, self.job):
                self.roots_tree.insert("", "end", iid=spec.key, values=(
                    "✓" if on else "", root.source, "Yes" if os.path.exists(root.source) else "—"))
            self.roots_tree.bind("<Double-1>", self._toggle_root)
            self.roots_tree.bind("<space>", self._toggle_root)
            r += 1
        else:
            self.roots_tree = None

        ttk.Label(b, text="Extra exclusions — one pattern per line. 'name' matches anywhere; "
                          "'/folder' only at the top.").grid(row=r, column=0, columnspan=3,
                                                              sticky="w", pady=(12, 4))
        r += 1
        self.ex_text = tk.Text(b, height=4, width=60, font=("Consolas", 10), relief="flat",
                               bg=app.text_bg, fg=app.text_fg, insertbackground=app.text_fg,
                               highlightthickness=1, highlightbackground=app.text_border,
                               highlightcolor=app.text_border)
        self.ex_text.grid(row=r, column=0, columnspan=3, sticky="ew")
        self.ex_text.insert("1.0", "\n".join(self.job.get("excludes") or []))
        r += 1

        btns = ttk.Frame(b)
        btns.grid(row=r, column=0, columnspan=3, sticky="e", pady=(16, 0))
        ttk.Button(btns, text="Cancel", command=self.destroy, style=self.sty("TButton")).pack(side="right")
        ttk.Button(btns, text="Save", style=self.sty("Accent.TButton"), command=self._ok).pack(side="right", padx=8)

    def _browse(self):
        d = filedialog.askdirectory(parent=self, initialdir=presets.expand_path(self.src_var.get()))
        if d:
            self.src_var.set(os.path.normpath(d))

    def _toggle_root(self, _evt=None):
        for iid in self.roots_tree.selection():
            vals = list(self.roots_tree.item(iid, "values"))
            vals[0] = "" if vals[0] == "✓" else "✓"
            self.roots_tree.item(iid, values=vals)

    def _ok(self):
        name = self.name_var.get().strip()
        sub = self.sub_var.get().strip().strip("\\/")
        if not name or not sub or any(c in sub for c in ':*?"<>|') or ".." in sub:
            messagebox.showerror(APP_NAME, "Give the set a name and a simple backup subfolder name.",
                                 parent=self)
            return
        clash = [j for j in self.app.cfg["jobs"]
                 if j["id"] != self.job["id"] and j["subdir"].lower() == sub.lower()]
        if clash:
            messagebox.showerror(APP_NAME, f"'{clash[0]['name']}' already uses the subfolder '{sub}'.",
                                 parent=self)
            return
        self.job["name"], self.job["subdir"] = name, sub
        if hasattr(self, "src_var"):
            self.job["source"] = self.src_var.get().strip()
        m = self.mode_var.get()
        self.job["mode"] = None if m.startswith(DEFAULT_MODE_LABEL) else LABEL_TO_MODE.get(m)
        self.job["every_days"] = LABEL_TO_FREQUENCY.get(self.freq_var.get(), 0)
        self.job["excludes"] = [ln.strip() for ln in self.ex_text.get("1.0", "end").splitlines() if ln.strip()]
        if self.roots_tree is not None:
            self.job["disabled_roots"] = [iid for iid in self.roots_tree.get_children()
                                          if self.roots_tree.item(iid, "values")[0] != "✓"]
        self.result = self.job
        self.destroy()


class ConfirmDeletesDialog(Dialog):
    def __init__(self, app: "App", req: dict):
        super().__init__(app, "Confirm removals")
        b = self.body
        n, total = req["n"], req["total"]
        head = ttk.Frame(b)
        head.pack(fill="x")
        ttk.Label(head, image=app.icons.stock(winshell.SIID_WARNING, 32) or "").pack(side="left", padx=(0, 10))
        ttk.Label(head, text=f"{req['label']}: {n:,} of {total:,} backed-up files no longer exist "
                             "in the source.", font=("Segoe UI Semibold", 11), wraplength=560,
                  justify="left").pack(side="left")
        ttk.Label(b, text=("An exact mirror would now remove them from the backup. If the source was "
                           "moved, renamed or emptied by mistake, keep them."),
                  wraplength=620, justify="left").pack(fill="x", pady=(8, 8))
        lb = tk.Listbox(b, height=12, font=("Consolas", 9), bg=app.text_bg, fg=app.text_fg,
                        relief="flat", highlightthickness=0)
        lb.pack(fill="both", expand=True)
        for rel in req["sample"]:
            lb.insert("end", rel)
        if n > 2000:
            lb.insert("end", f"… and {n - 2000:,} more")
        btns = ttk.Frame(b)
        btns.pack(fill="x", pady=(14, 0))
        ttk.Button(btns, text="Keep them (skip removals)", style=self.sty("Accent.TButton"),
                   command=lambda: self._done(False)).pack(side="right")
        ttk.Button(btns, text=f"Remove {n:,} files", command=lambda: self._done(True), style=self.sty("TButton")).pack(
            side="right", padx=8)
        self.protocol("WM_DELETE_WINDOW", lambda: self._done(False))
        self.result = False

    def _done(self, ok: bool):
        self.result = ok
        self.destroy()


class PreviewDialog(Dialog):
    def __init__(self, app: "App", result: dict, what: str = "backup"):
        super().__init__(app, f"Preview — what a {what} would do")
        b = self.body
        ttk.Label(b, text="Nothing has been changed. Expand a row to see files that would be removed.",
                  wraplength=760).pack(anchor="w", pady=(0, 8))
        frame = ttk.Frame(b)
        frame.pack(fill="both", expand=True)
        cols = ("new", "changed", "remove", "size", "note")
        tree = ttk.Treeview(frame, columns=cols, height=16, style=app.sty("Treeview"))
        tree.heading("#0", text="Location")
        tree.column("#0", width=330)
        for c, t, w in (("new", "New", 70), ("changed", "Changed", 80), ("remove", "Remove", 80),
                        ("size", "To write", 100), ("note", "Note", 260)):
            tree.heading(c, text=t)
            tree.column(c, width=w, anchor="e" if c != "note" else "w", stretch=(c == "note"))
        tree.pack(side="left", fill="both", expand=True)
        sb = ttk.Scrollbar(frame, orient="vertical", command=tree.yview,
                           style=app.sty("Vertical.TScrollbar"))
        sb.pack(side="right", fill="y")
        tree.configure(yscrollcommand=sb.set)
        for i, p in enumerate(result["plans"]):
            note = p["skip_reason"] if p["skip_reason"] and not p["optional"] else ""
            if p["optional"] and p["skip_reason"]:
                note = "not on this PC"
            if p["deletes_blocked"]:
                note = p["deletes_blocked"]
            elif p["guard_hit"]:
                note = "mass-deletion guard: a real run will ask first"
            if p["warnings"]:
                note = (note + "; " if note else "") + f"{p['warnings']} scan warnings"
            iid = f"r{i}"
            tree.insert("", "end", iid=iid, text=p["label"], values=(
                f"{p['new']:,}", f"{p['changed']:,}", f"{p['remove']:,}",
                human_bytes(p["bytes"]), note))
            for rel in p["delete_sample"]:
                tree.insert(iid, "end", text=rel, values=("", "", "remove", "", ""))
            if p["remove"] > len(p["delete_sample"]):
                tree.insert(iid, "end", text=f"… {p['remove'] - len(p['delete_sample']):,} more",
                            values=("",) * 5)
        prog = result["progress"]
        ttk.Label(b, text=f"Total: {prog['files_total']:,} actions, {human_bytes(prog['bytes_total'])} to write.",
                  font=("Segoe UI Semibold", 10)).pack(anchor="w", pady=(10, 0))
        ttk.Button(b, text="Close", command=self.destroy, style=self.sty("TButton")).pack(anchor="e", pady=(10, 0))
        self.geometry("1000x560")


class RestoreDialog(Dialog):
    """Choose what to restore from the backup, where to, and how. Never deletes."""

    def __init__(self, app: "App", job: dict):
        super().__init__(app, f"Restore — {job['name']}")
        self.job = job
        b = self.body
        b.columnconfigure(1, weight=1)
        head = ttk.Frame(b)
        head.grid(row=0, column=0, columnspan=3, sticky="ew")
        ttk.Label(head, image=app.icons.job(job)).pack(side="left", padx=(0, 8))
        ttk.Label(head, text=f"Restore {job['name']} from the backup",
                  font=("Segoe UI Semibold", 12)).pack(side="left")
        ttk.Label(b, text="Copies files from the backup drive back to this PC. A restore never deletes "
                          "anything.", wraplength=640, justify="left").grid(
            row=1, column=0, columnspan=3, sticky="w", pady=(6, 10))

        self.rows = [(spec, root) for spec, root, _on in config.job_roots(app.cfg, job)
                     if os.path.exists(root.dest)]
        if not self.rows:
            ttk.Label(b, text="Nothing of this set is in the backup yet.").grid(row=2, column=0, sticky="w")
            ttk.Button(b, text="Close", command=self.destroy, style=self.sty("TButton")).grid(
                row=3, column=2, sticky="e", pady=(14, 0))
            return

        ttk.Label(b, text="Locations (double-click to switch on/off)").grid(row=2, column=0, columnspan=3,
                                                                           sticky="w")
        frame = ttk.Frame(b)
        frame.grid(row=3, column=0, columnspan=3, sticky="nsew", pady=(4, 10))
        b.rowconfigure(3, weight=1)
        self.tree = ttk.Treeview(frame, columns=("on", "from", "to"), show="headings",
                                 height=min(8, len(self.rows) + 1), style=self.sty("Treeview"))
        for col, text, w in (("on", "Include", 70), ("from", "In the backup", 300),
                             ("to", "Original location", 330)):
            self.tree.heading(col, text=text)
            self.tree.column(col, width=w, stretch=(col != "on"), anchor="center" if col == "on" else "w")
        self.tree.pack(fill="both", expand=True)
        dest_root = app.cfg["dest_root"]
        for spec, root in self.rows:
            self.tree.insert("", "end", iid=spec.key, values=(
                "✓", os.path.relpath(root.dest, dest_root), root.source))
        self.tree.bind("<Double-1>", self._toggle)
        self.tree.bind("<space>", self._toggle)

        ttk.Label(b, text="Restore to", font=("Segoe UI Semibold", 10)).grid(row=4, column=0, sticky="w")
        self.where = tk.StringVar(value="original")
        ttk.Radiobutton(b, text="The original locations", value="original",
                        variable=self.where).grid(row=5, column=0, columnspan=3, sticky="w", pady=2)
        other = ttk.Frame(b)
        other.grid(row=6, column=0, columnspan=3, sticky="ew", pady=2)
        other.columnconfigure(1, weight=1)
        ttk.Radiobutton(other, text="Another folder:", value="other", variable=self.where).grid(
            row=0, column=0, sticky="w")
        self.target_var = tk.StringVar()
        ttk.Entry(other, textvariable=self.target_var, style=self.sty("TEntry")).grid(
            row=0, column=1, sticky="ew", padx=6)
        ttk.Button(other, text="Browse…", command=self._browse, style=self.sty("TButton")).grid(
            row=0, column=2)

        ttk.Label(b, text="Files that already exist", font=("Segoe UI Semibold", 10)).grid(
            row=7, column=0, sticky="w", pady=(10, 0))
        self.overwrite = tk.BooleanVar(value=False)
        ttk.Radiobutton(b, text="Keep them — only restore files that are missing (safest)",
                        value=False, variable=self.overwrite).grid(row=8, column=0, columnspan=3,
                                                                   sticky="w", pady=2)
        ttk.Radiobutton(b, text="Replace them when they differ from the backup",
                        value=True, variable=self.overwrite).grid(row=9, column=0, columnspan=3,
                                                                  sticky="w", pady=2)
        notes = ["Settings files stored without login tokens are never copied over the originals."]
        if job["preset"] in ("claude", "codex"):
            notes.insert(0, f"Close the {job['name']} app before restoring to the original locations.")
        if job["preset"] == "environments":
            notes.insert(0, "Environments only work at their original paths; restore the base Python first.")
        ttk.Label(b, text="  ".join(notes), foreground="#888", wraplength=640, justify="left").grid(
            row=10, column=0, columnspan=3, sticky="w", pady=(10, 0))

        btns = ttk.Frame(b)
        btns.grid(row=11, column=0, columnspan=3, sticky="e", pady=(14, 0))
        ttk.Button(btns, text="Cancel", command=self.destroy, style=self.sty("TButton")).pack(side="right")
        ttk.Button(btns, text="Restore", style=self.sty("Accent.TButton"),
                   command=lambda: self._go(False)).pack(side="right", padx=8)
        ttk.Button(btns, text="Preview", command=lambda: self._go(True),
                   style=self.sty("TButton")).pack(side="right")

    def _toggle(self, _evt=None):
        for iid in self.tree.selection():
            vals = list(self.tree.item(iid, "values"))
            vals[0] = "" if vals[0] == "✓" else "✓"
            self.tree.item(iid, values=vals)

    def _browse(self):
        d = filedialog.askdirectory(parent=self, title="Restore into this folder")
        if d:
            self.target_var.set(os.path.normpath(d))
            self.where.set("other")

    def _go(self, preview: bool):
        keys = [iid for iid in self.tree.get_children() if self.tree.item(iid, "values")[0] == "✓"]
        if not keys:
            messagebox.showinfo(APP_NAME, "Switch on at least one location.", parent=self)
            return
        target = None
        if self.where.get() == "other":
            target = self.target_var.get().strip()
            if not target:
                messagebox.showinfo(APP_NAME, "Choose the folder to restore into.", parent=self)
                return
        overwrite = bool(self.overwrite.get())
        if not preview and target is None and overwrite and not messagebox.askyesno(
                APP_NAME, "Replace files on this PC that differ from the backup with the backup's "
                          "versions?\n\nRun a preview first if you are not sure.", parent=self):
            return
        self.result = {"preview": preview,
                       "params": {"job_id": self.job["id"], "root_keys": keys, "target": target,
                                  "overwrite": overwrite}}
        self.destroy()
