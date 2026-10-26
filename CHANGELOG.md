# Changelog

## Unreleased

**New**
- **The backup drive is found by identity, not by letter.** If Windows gives the drive another letter
  (for example K: becomes L: after plugging drives in a different order), Kramers finds it by its
  volume serial, or by its label together with the Kramers marker, and follows it. The log says so.
- **Picking a drive or parent folder uses the existing backup.** Choosing `L:\` when `L:\Backups`
  holds your Kramers backup uses `L:\Backups`, and a saved destination that points at such a root is
  corrected. A bare drive root becomes `<drive>\Backups`, so a drive root never gets a second full copy.
- **Free-space check:** a backup that would not fit on the drive stops before copying anything, and
  a preview warns.
- **Folder icons on the backup drive:** each set's backup folder shows the same icon as in the app,
  copied at full resolution (up to 256 px) from the program or Windows icon library,
  and the backup folder shows the Kramers icon (hidden `.kramers.ico` + `desktop.ini`; never mirrored
  or removed).
- The Claude set includes the Claude desktop app's new data location,
  `%LOCALAPPDATA%\Claude-Data\claude_*`. The earlier locations are kept as optional fallbacks.

**Changed**
- **Unattended backups never stop to ask.** Previously, an automatic backup while the window was open
  waited for an answer when it hit the mass-deletion guard. A scheduled backup withheld those
  removals instead. Both now carry out the removals by default and log them. Options →
  "When a scheduled or automatic backup would remove many files" can keep the files and notify
  you instead. **Back up now** still asks. Missing and empty sources still never delete.
- The open window keeps "Last backup", sizes and the schedule's "Next run" current, and shows
  scheduled runs that were skipped because the backup drive was not connected (#1, #2).


## 2.1.0 — 2026-10-13 — first public release

**New**
- **Restore** a set from the backup, to its original locations or to another folder. You can
  restore only missing files (the default) or missing and changed files, with a preview first.
  A restore never deletes anything, and never puts token-free settings files back over the
  originals.
- **Encryption check:** Options shows whether the backup drive is BitLocker-encrypted and has a
  shortcut to BitLocker settings. An optional reminder in the main window is off by default.
- **Per-set frequency:** every run, daily, weekly or monthly. "Back up now" and scheduled runs
  skip sets that are not due; right-clicking a set backs it up now regardless.
- **Archive retention** for versioned mode (30 days by default; 0 keeps everything).
- **Notifications after scheduled backups**: only when something needs attention (default),
  always, or never. Clicking a notification opens Kramers.
- **pip install:** `pip install kramers-backup` provides the `kramers` (window) and `kramers-run`
  (headless) commands.
- **GitHub Actions** run the tests on Windows (Python 3.10–3.14) and Linux.
- README screenshots, `SECURITY.md`, `CONTRIBUTING.md` and `docs/DESIGN.md`.

**Changed**
- The window code is split into `gui.py`, `dialogs.py` and `ui_common.py`.
- Without `sv-ttk`, the native fallback theme is used in light mode, so icons stay visible.
- With no destination chosen yet, the app asks for one instead of reporting a missing drive.

## 2.0.1 — 2026-10-13

**New**
- **Size** column: each set's size and file count, plus a total, updated after every preview or backup.
- **Low-priority copying** (on by default). Scanning and copy threads run in Windows background mode,
  with low CPU and disk priority, so the PC stays responsive during a backup.
- **Pause / Resume** in the window and in the tray menu.
- Optional **speed limit** in MB/s.
- Options moved to their own window (the **Options** button).
- **Backups run in their own process**, in Windows background mode. The window no longer competes with
  the scan for Python's interpreter lock, and the scheduled runner uses background mode too.
- **Window resizing is fast.** Sun Valley draws widgets by tiling translucent images, which Tk on
  Windows blends in software: a resize step took about 1.4 s. Large widgets (table, entry, drop-down,
  progress bar, scrollbars, buttons) now use flat, image-free styles in Sun Valley's colours
  (about 0.23 s per full repaint). While the window edge is being dragged, a light placeholder is
  shown (about 10 ms per step) and the window redraws once when you let go.
- Group boxes and tabs are drawn as plain headings and a tab bar (same reason).
- The log is updated in batches, and the time estimate uses the last ~20 s of progress and appears after
  a few seconds (no more "about 470 min left" at the start).
- Installed-app logos use the light- or dark-background variant to match the theme.

**Changed**
- 4 files are copied at once by default, down from 6. Configs that kept 2.0.0's default of 6 are lowered to 4.
- Files deleted from the source mid-run are counted as **gone**, not as *in use*. `*.partial`
  work-in-progress folders of other tools (for example a GammaKeeper release being deployed) are skipped.
- The disabled **Back up now** button shows a greyed-out icon.

## 2.0.0 — 2026-10-13 — Kramers

Desktop Mirror Backup has been rebuilt and renamed Kramers.

**New**
- Multiple backup sets: Desktop, Downloads, EndNote, Claude, Codex, Python environments, and any folder you add. Each set can be switched on or off, and can override the default mode and exclusions.
- Presets for Claude (Code, the desktop app's MSIX data, `.claude-science`) and Codex, with login tokens excluded or redacted.
- Python environments: full copies of venvs and conda envs and of the base Python, plus rebuild recipes.
- EndNote preset: SQLite snapshots of `.enl`/`.eni` library databases; `login.txt` is excluded.
- Exact mirror is now the default mode.
- Safety guards for missing sources, empty sources, mass deletions (confirmation dialog) and the wrong drive (volume serial check).
- Preview window listing every file that would be removed.
- Consistent SQLite snapshots of databases held open by running apps.
- Parallel copying (6 files at once by default).
- `RESTORE.md` and a drive marker written to the backup root after every backup.
- New look: Sun Valley (Windows 11) theme following the system light/dark setting, Windows shell icons for each set, Segoe Fluent Icons buttons, dark title bar.
- App icon, splash screen with live startup steps, and AppUserModelID so the taskbar shows Kramers' icon.
- Desktop and Start Menu shortcuts (`tools\install_kramers.cmd`); `tools\uninstall_kramers.cmd` removes them
  together with the scheduled task, and optionally the settings.
- Notification-area (tray) icon: closing the window keeps Kramers running, with automatic backups,
  a tray menu (Open, Back up now / Cancel, last-backup status, Quit) and Windows notifications.
- Single instance: launching Kramers again brings the running copy to the front.
- A run lock on the backup drive, so the app and the scheduled task never back up at the same time.
- Folder sets snapshot SQLite databases (for example GammaKeeper runs) and remove stale `-wal`/`-shm`
  files from the backup. A file deleted mid-run is reported as skipped, not as an error.

**Fixed**
- `[WinError 87] The parameter is incorrect` on exFAT drives for files with pre-1980 timestamps (for example `py.typed` with atime 0). Timestamps are now clamped.
- Files locked by running programs (for example GammaKeeper's `.writer.lock`) are reported as *in use* and are no longer counted as errors.
- Folders named `_archive` or `_logs` inside your data are now backed up. Only the backup root's own tool folders are excluded.
- Read-only files in the backup can now be updated and removed.

**Moved**
- Settings now live in `%APPDATA%\Kramers\config.json`. A v1 `config.json` is migrated automatically.
