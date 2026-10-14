"""
uninstall.py -- remove Kramers from this Windows user account.

Removes the Desktop and Start Menu shortcuts and the 'Kramers' scheduled
task, and (only if you answer yes) the settings folder %APPDATA%\\Kramers.
Your backups, the program folder and the sv-ttk package are never touched.

Run tools\\uninstall_kramers.cmd from Explorer: a process started inside a
packaged (MSIX) app, such as the Claude desktop app, sees a redirected AppData.
"""
from __future__ import annotations

import shutil

from . import config, shortcuts


def main(assume_yes: bool = False) -> int:
    try:
        from . import tray
        if tray.running_instance():
            print("Kramers is running in the notification area. Right-click its icon and choose "
                  "'Quit Kramers' first, then run this again.")
            return 1
    except (ImportError, OSError, AttributeError):
        pass

    for link in shortcuts.uninstall():
        print("Removed shortcut:", link)
    try:
        if shortcuts.remove_schedule(config.TASK_NAME):
            print(f"Removed scheduled task '{config.TASK_NAME}'.")
        else:
            print("No scheduled task to remove.")
    except RuntimeError as exc:
        print("Could not remove the scheduled task:", exc)

    settings = config.config_dir()
    if settings.exists():
        answer = "y" if assume_yes else input(
            f"Also delete Kramers' settings and history in {settings}? [y/N] ").strip().lower()
        if answer in ("y", "yes"):
            shutil.rmtree(settings, ignore_errors=True)
            print("Settings removed.")
        else:
            print("Settings kept; reinstalling will pick them up again.")

    print()
    print("Kramers is uninstalled. Your backups and this program folder were not changed;")
    print("delete the folder yourself if you no longer need it.")
    return 0
