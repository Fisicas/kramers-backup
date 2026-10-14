"""splash.py -- borderless startup splash showing live startup steps."""
from __future__ import annotations

import time
import tkinter as tk

from .config import RESOURCES

MIN_SHOW_S = 0.9


class Splash:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.t0 = time.time()
        self.win = None
        self.steps: list = []
        try:
            self.img = tk.PhotoImage(master=root, file=str(RESOURCES / "splash.png"))
        except tk.TclError:
            return
        w, h = self.img.width(), self.img.height()
        win = tk.Toplevel(root)
        win.overrideredirect(True)
        win.attributes("-topmost", True)
        x = (win.winfo_screenwidth() - w) // 2
        y = (win.winfo_screenheight() - h) // 2
        win.geometry(f"{w}x{h}+{x}+{y}")
        self.canvas = tk.Canvas(win, width=w, height=h, highlightthickness=0, bd=0, bg="#111827")
        self.canvas.pack()
        self.canvas.create_image(0, 0, image=self.img, anchor="nw")
        self.win = win
        win.update()

    def step(self, text: str) -> None:
        """Mark the previous step done and show the next one."""
        if not self.win:
            return
        c = self.canvas
        if self.steps:
            c.itemconfigure(self.steps[-1][0], text="✓", fill="#34d399")
        y = 262 + 20 * len(self.steps)
        if len(self.steps) >= 4:                   # keep the list inside the window
            for mark, label in self.steps:
                c.move(mark, 0, -20)
                c.move(label, 0, -20)
            y -= 20
        mark = c.create_text(242, y, text="•", fill="#7dd3fc", anchor="w", font=("Segoe UI", 10, "bold"))
        label = c.create_text(260, y, text=text, fill="#cbd5e1", anchor="w", font=("Segoe UI", 10))
        self.steps.append((mark, label))
        self.win.update()

    def close(self, then=None) -> None:
        if not self.win:
            if then:
                then()
            return
        if self.steps:
            self.canvas.itemconfigure(self.steps[-1][0], text="✓", fill="#34d399")
        wait_ms = max(0, int((MIN_SHOW_S - (time.time() - self.t0)) * 1000))

        def done():
            self.win.destroy()
            self.win = None
            if then:
                then()
        self.root.after(wait_ms, done)
