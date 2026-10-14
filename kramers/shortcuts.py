"""
shortcuts.py -- Desktop and Start Menu shortcuts for Kramers.

The shortcuts run Kramers.pyw with pythonw.exe and use kramers.ico. Create
them from Kramers itself (Options tab) or tools\\install_kramers.cmd started
from Explorer: a process started inside a packaged (MSIX) app -- such as the
Claude desktop app -- has its AppData writes (where the Start Menu lives)
redirected into that app's private storage.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from .config import APP_DIR, RESOURCES

NAME = "Kramers.lnk"
NO_WINDOW = 0x08000000


def _ps(script: str) -> str:
    out = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy",
                          "Bypass", "-Command", script], capture_output=True, text=True,
                         creationflags=NO_WINDOW)
    if out.returncode:
        raise RuntimeError(out.stderr.strip() or out.stdout.strip())
    return out.stdout.strip()


def _q(v) -> str:
    return "'" + str(v).replace("'", "''") + "'"


def pythonw() -> Path:
    exe = Path(sys.executable)
    w = exe.with_name("pythonw.exe")
    return w if w.exists() else exe


def launch_command(*extra: str) -> tuple:
    """(pythonw.exe, [args]) that starts Kramers: the Kramers.pyw launcher in a source
    checkout, or `-m kramers` when installed with pip."""
    launcher = APP_DIR / "Kramers.pyw"
    args = [str(launcher)] if launcher.exists() else ["-m", "kramers"]
    return str(pythonw()), args + list(extra)


def install() -> list:
    desktop, programs = _ps("[Environment]::GetFolderPath('Desktop'); "
                            "[Environment]::GetFolderPath('Programs')").splitlines()
    icon = RESOURCES / "kramers.ico"
    exe, args = launch_command()
    arg_text = " ".join(f'"{a}"' if " " in a else a for a in args)
    made = []
    for folder in (desktop, programs):
        link = Path(folder) / NAME
        _ps(f"$s = (New-Object -ComObject WScript.Shell).CreateShortcut({_q(link)}); "
            f"$s.TargetPath = {_q(exe)}; $s.Arguments = {_q(arg_text)}; "
            f"$s.IconLocation = {_q(str(icon) + ',0')}; $s.WorkingDirectory = {_q(APP_DIR)}; "
            "$s.Description = 'Kramers: mirror backups'; $s.Save()")
        made.append(str(link))
    return made


def remove_schedule(task_name: str) -> bool:
    """Delete the Task Scheduler task if present. True if one was removed."""
    q = subprocess.run(["schtasks", "/Query", "/TN", task_name], capture_output=True,
                       creationflags=NO_WINDOW)
    if q.returncode != 0:
        return False
    d = subprocess.run(["schtasks", "/Delete", "/TN", task_name, "/F"], capture_output=True,
                       text=True, creationflags=NO_WINDOW)
    if d.returncode != 0:
        raise RuntimeError((d.stderr or d.stdout).strip())
    return True


def uninstall() -> list:
    desktop, programs = _ps("[Environment]::GetFolderPath('Desktop'); "
                            "[Environment]::GetFolderPath('Programs')").splitlines()
    removed = []
    for folder in (desktop, programs):
        link = Path(folder) / NAME
        if link.exists():
            link.unlink()
            removed.append(str(link))
    return removed
