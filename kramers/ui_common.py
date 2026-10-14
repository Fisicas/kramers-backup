"""ui_common.py -- labels, formatting helpers and the Windows icon cache shared by the UI."""
from __future__ import annotations

import base64
import os
import tkinter as tk
from datetime import datetime

from . import config, presets, winshell

MODE_TO_LABEL = dict(config.MODE_LABELS)
LABEL_TO_MODE = {v: k for k, v in config.MODE_LABELS}
DEFAULT_MODE_LABEL = "Use default"

FREQ_LABELS = [
    ("DAILY", "Every day at a set time"),
    ("HOURLY", "Once an hour"),
    ("EVERY_N_HOURS", "Every N hours"),
    ("ONLOGON", "When I sign in to Windows"),
]
FREQ_TO_LABEL = dict(FREQ_LABELS)
LABEL_TO_FREQ = {v: k for k, v in FREQ_LABELS}

FREQUENCIES = [(0, "Every run"), (1, "Once a day"), (7, "Once a week"), (30, "Once a month")]
FREQUENCY_TO_LABEL = dict(FREQUENCIES)
LABEL_TO_FREQUENCY = {v: k for k, v in FREQUENCIES}

STATUS_TEXT = {"ok": "✓ OK", "warning": "⚠ Warnings", "error": "✕ Errors", "skipped": "– Skipped"}


def human_bytes(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{int(n)} B" if unit == "B" else f"{n:,.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


def human_count(n: int) -> str:
    if n >= 1_000_000:
        return f"{n / 1e6:.1f}M"
    if n >= 10_000:
        return f"{n / 1e3:.0f}k"
    return f"{n:,}"


def human_when(iso: str) -> str:
    try:
        t = datetime.fromisoformat(iso)
    except (TypeError, ValueError):
        return "Never"
    now = datetime.now()
    if t.date() == now.date():
        return f"Today {t:%H:%M}"
    if (now.date() - t.date()).days == 1:
        return f"Yesterday {t:%H:%M}"
    return f"{t:%d %b %Y %H:%M}"


def open_path(p: str) -> None:
    if p and os.path.exists(p):
        os.startfile(p)  # type: ignore[attr-defined]


# --------------------------------------------------------------------------- #
# Icons
# --------------------------------------------------------------------------- #

class Icons:
    """Caches Tk images made from Windows icons and Fluent glyphs."""

    def __init__(self, root: tk.Misc, dark: bool):
        self.root = root
        self.dark = dark
        scale = root.winfo_fpixels("1i") / 96.0
        self.px = max(16, round(16 * scale))
        self.fg = (255, 255, 255) if dark else (26, 26, 26)
        self.green = (108, 203, 95) if dark else (16, 124, 16)
        self.amber = (252, 225, 0) if dark else (157, 93, 0)
        self.red = (255, 153, 164) if dark else (196, 43, 28)
        self._cache: dict = {}

    def _photo(self, key, rgba, px):
        if rgba is None:
            return None
        img = tk.PhotoImage(master=self.root,
                            data=base64.b64encode(winshell.rgba_to_png(px, px, rgba)))
        self._cache[key] = img
        return img

    def glyph(self, name: str, color=None, px=None):
        px, color = px or self.px, color or self.fg
        key = ("g", name, color, px)
        if key not in self._cache:
            self._photo(key, winshell.glyph_rgba(winshell.GLYPHS[name], px, color), px)
        return self._cache.get(key)

    def shell(self, path: str, px=None):
        px = px or self.px
        key = ("s", path, px)
        if key not in self._cache:
            self._photo(key, winshell.shell_icon_rgba(path, px), px)
        return self._cache.get(key)

    def stock(self, siid: int, px=None):
        px = px or self.px
        key = ("k", siid, px)
        if key not in self._cache:
            self._photo(key, winshell.stock_icon_rgba(siid, px), px)
        return self._cache.get(key)

    def spec(self, spec: str, px=None):
        """'package:Claude' | 'exe:<path>' | 'path:<path>' -> image."""
        px = px or self.px
        key = ("p", spec, px)
        if key in self._cache:
            return self._cache[key]
        kind, _, val = spec.partition(":")
        img = None
        if kind == "package":
            png = winshell.package_logo(val, px, self.dark)
            if png:
                try:     # Tk cannot open files under WindowsApps; Python can
                    with open(png, "rb") as f:
                        img = tk.PhotoImage(master=self.root, data=base64.b64encode(f.read()))
                    if img.width() >= 2 * px:
                        img = img.subsample(img.width() // px)
                except (OSError, tk.TclError):
                    img = None
        elif kind in ("exe", "path") and val:
            img = self.shell(presets.expand_path(val), px)
        img = img or self.stock(winshell.SIID_FOLDER, px)
        self._cache[key] = img
        return img

    def job(self, job: dict):
        preset = config.job_preset(job)
        if preset is None:
            return self.stock(winshell.SIID_FOLDER)
        img = self.spec(preset.icon)
        if preset.name == "claude" and img is self.stock(winshell.SIID_FOLDER):
            img = self.shell(presets.expand_path(r"%USERPROFILE%\.claude"))
        return img
