# Kramers design notes

How Kramers works, and why it works that way. User-facing documentation is in the [README](../README.md).

## Modules

| Module | Role |
|---|---|
| `engine.py` | One-way mirror engine: scan, plan, guards, parallel copy, delete. Standard library only; portable. |
| `presets.py` | What each kind of backup set covers (Desktop/Downloads folders, EndNote, Claude, Codex, Python environments). |
| `config.py` | Settings (schema v2), v1 migration, job → engine roots, destination identity, logs, state, restore, archive pruning, per-set frequency. |
| `recipes.py` | Rebuild recipes for Python environments (`pip freeze`, `conda list --explicit`, `environment.yml`). |
| `worker.py` | Runs a backup or restore in a child process in Windows background mode. |
| `runner.py` | Headless runs for Task Scheduler, plus their notifications. |
| `gui.py`, `dialogs.py`, `ui_common.py`, `fastui.py`, `splash.py` | The tkinter window (sv-ttk theme plus flat styles for large widgets). |
| `tray.py` | Notification-area icon, single-instance handling, notifications (ctypes). |
| `winshell.py` | Known folders, volume serials, BitLocker state, shell/stock icons, Fluent glyphs, MSIX package logos (ctypes). |
| `shortcuts.py`, `uninstall.py` | Desktop/Start Menu shortcuts, scheduled-task removal, uninstall. |

## The engine

A **root** pairs one source (a folder or a single file) with one destination. A run:

1. **Scans** every root. `os.scandir` provides cached stat data, so reparse points (junctions, symlinks) are detected without extra system calls and are not followed.
2. **Applies the guards.**
   - A missing source leaves its backup untouched.
   - An empty source never deletes.
   - Mass deletions need confirmation. Unattended runs withhold them.
3. **Copies** new and changed files in a thread pool. Each file goes to `name.kramers-partial` first and is renamed into place only when complete.
4. **Deletes** (mirror mode) or archives (versioned mode) what the source no longer has.

A file counts as changed when its size differs or its mtime differs by more than 2 s, because FAT and exFAT store mtimes at 2 s resolution. Timestamps before 1980, which exFAT cannot store, are clamped.

**Special copies:**
- **SQLite databases** (`*.sqlite`, `*.db`, EndNote `*.enl`/`*.eni`, checked against the SQLite header) are copied with SQLite's online-backup API. A database that a running app holds open in WAL mode is therefore captured consistently. Change detection uses the newer of the database and its `-wal` file. Stale `-wal`/`-shm` files beside a fresh snapshot are removed.
- **JSON settings** containing login tokens are written with the token keys removed. A file that cannot be parsed is skipped, never copied unredacted, and a restore never puts a redacted file back over the original.

## Safety model

- **One-way only.** Nothing on the source side is ever written by a backup. A restore never deletes.
- **Destination identity.** The backup volume's serial number is recorded, so a different drive that appears under the same letter is never written to.
- **One run at a time.** A lock file on the backup drive stops the app and the scheduled task from running at once.
- **No secrets.** Credential files are excluded and token keys are redacted. The backup drive should still be encrypted, because the backups contain private documents and AI conversation histories. Options shows the drive's BitLocker state, with an optional reminder in the main window.

## Packaged (MSIX) apps

The Claude, Codex and ChatGPT desktop apps are MSIX packages. Their app data really lives under `%LOCALAPPDATA%\Packages\<family>\LocalCache\...`. A process started from inside such an app (for example a terminal opened by it) sees a merged, redirected `%APPDATA%`, and its AppData writes land in the package's private folder. As a result:

- The Claude preset backs up the package location, and treats `%APPDATA%\Claude` as an optional second root for classic installs.
- Kramers' installer and first run should be started from Explorer or a shortcut.

## Window performance

Sun Valley draws widgets by tiling small translucent PNG slices, which Tk on Windows blends in software. A wide Entry or table header therefore costs hundreds of blends per repaint. Measured resize cost for the full window was about 1.4 s per step. Kramers keeps Sun Valley for small widgets and draws the large, stretched ones (table, entries, drop-downs, progress bar, scrollbars, buttons) with clam's flat vector elements in Sun Valley's colours, which brings it to about 0.23 s. During an interactive resize a placeholder is shown, at about 10 ms per step. Backups run in a separate process, so they never compete with the window for Python's interpreter lock.
