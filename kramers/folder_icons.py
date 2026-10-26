"""
folder_icons.py -- give the folders on the backup drive the icons the app shows.

Each backup set's folder gets a hidden `.kramers.ico` (the same Windows icon the app
shows for that set: Desktop, Downloads, EndNote, Claude, Codex, Python...) and a hidden
`desktop.ini` pointing Explorer at it; the backup root gets the Kramers icon. Explorer
only reads desktop.ini in folders marked read-only or system, so the folder is marked
read-only (which, for folders, does not stop anything from being written inside).

The engine never mirrors, overwrites or deletes these two files at the top of a set.
"""
from __future__ import annotations

import os
import struct
from pathlib import Path

from . import presets, winshell

ICON_NAME = ".kramers.ico"
INI_NAME = "desktop.ini"
SIZES = (16, 24, 32, 48, 256)
DESKTOP_INI = ("[.ShellClassInfo]\r\nIconResource={0},0\r\nIconFile={0}\r\nIconIndex=0\r\n"
               .format(ICON_NAME))

_ATTR_READONLY, _ATTR_HIDDEN, _ATTR_SYSTEM, _ATTR_NORMAL = 0x1, 0x2, 0x4, 0x80


def ico_from_images(images: list) -> bytes:
    """A .ico from [(size, bpp, data)]; data may be a PNG or an icon DIB (both are valid
    .ico payloads; PNG ones need Windows Vista or later)."""
    images = sorted({s: (b, d) for s, b, d in images}.items())
    out = bytearray(struct.pack("<HHH", 0, 1, len(images)))
    offset = 6 + 16 * len(images)
    for size, (bpp, data) in images:
        dim = 0 if size >= 256 else size
        out += struct.pack("<BBBBHHII", dim, dim, 0, 0, 1, bpp, len(data), offset)
        offset += len(data)
    for _, (_bpp, data) in images:
        out += data
    return bytes(out)


def ico_from_pngs(pngs: list) -> bytes:
    """A .ico holding 32-bit PNG images [(size, png bytes)]."""
    return ico_from_images([(s, 32, d) for s, d in pngs])


def native_icon(spec: str) -> bytes:
    """The program's or folder's own embedded icon images, copied without rescaling
    (sharp at every size Explorer uses, up to 256 px). b'' if not available."""
    kind, _, val = spec.partition(":")
    if kind not in ("exe", "path") or not val:
        return b""
    loc = winshell.icon_location(presets.expand_path(val))
    images = winshell.icon_resource_images(*loc) if loc else []
    return ico_from_images(images) if images else b""


def icon_pngs(spec: str, dark: bool) -> list:
    """[(size, png)] for an icon spec as used by presets ('package:…', 'exe:…', 'path:…')."""
    kind, _, val = spec.partition(":")
    out = []
    for px in SIZES:
        if kind == "package":
            png = winshell.package_logo(val, px, dark)
            if png and f"targetsize-{px}" in png:
                try:
                    out.append((px, Path(png).read_bytes()))
                except OSError:
                    pass
        elif kind in ("exe", "path") and val:
            rgba = winshell.shell_icon_rgba(presets.expand_path(val), px)
            if rgba:
                out.append((px, winshell.rgba_to_png(px, px, rgba)))
    return out


def _set_attrs(path: str, attrs: int) -> None:
    if os.name == "nt":
        import ctypes
        ctypes.windll.kernel32.SetFileAttributesW(str(path), attrs)


def _get_attrs(path: str) -> int:
    if os.name != "nt":
        return 0
    import ctypes
    a = ctypes.windll.kernel32.GetFileAttributesW(str(path))
    return 0 if a == 0xFFFFFFFF else a


def _write_if_changed(path: Path, data: bytes, attrs: int) -> bool:
    try:
        if path.exists() and path.read_bytes() == data:
            _set_attrs(path, attrs)
            return False
        if path.exists():
            _set_attrs(path, _ATTR_NORMAL)      # hidden/system files refuse to be overwritten
        path.write_bytes(data)
        _set_attrs(path, attrs)
        return True
    except OSError:
        return False


def set_folder_icon(folder: str, ico: bytes) -> bool:
    """Write .kramers.ico + desktop.ini into folder. True if anything changed."""
    f = Path(folder)
    if not f.is_dir() or not ico:
        return False
    changed = _write_if_changed(f / ICON_NAME, ico, _ATTR_HIDDEN)
    changed |= _write_if_changed(f / INI_NAME, DESKTOP_INI.encode("ascii"),
                                 _ATTR_HIDDEN | _ATTR_SYSTEM)
    a = _get_attrs(str(f))
    if a and not a & _ATTR_READONLY:
        _set_attrs(str(f), a | _ATTR_READONLY)
        changed = True
    if changed and os.name == "nt":
        import ctypes
        ctypes.windll.shell32.SHChangeNotify(0x00001000, 0x0005, str(f), None)  # UPDATEDIR, PATHW
    return changed


def apply(cfg: dict, job_preset, resources: Path) -> int:
    """Icons for every enabled set's backup folder and for the backup root. Returns the
    number of folders updated. Never raises: icons are cosmetic."""
    if os.name != "nt" or not cfg.get("dest_root"):
        return 0
    dark = winshell.system_uses_dark_mode()
    done = 0
    try:
        root_ico = (resources / "kramers.ico").read_bytes()
        done += set_folder_icon(cfg["dest_root"], root_ico)
    except OSError:
        pass
    for job in cfg.get("jobs", []):
        if not job.get("enabled"):
            continue
        folder = os.path.join(cfg["dest_root"], job["subdir"])
        if not os.path.isdir(folder):
            continue
        preset = job_preset(job)
        spec = "path:" + job["source"] if job["preset"] == "folder" else (preset.icon if preset else "")
        try:
            ico = native_icon(spec)                      # exact images from the exe / dll
            if not ico:
                pngs = icon_pngs(spec, dark)             # packaged-app logos, or a rendering
                if not pngs and job["preset"] == "claude":
                    pngs = icon_pngs("path:%USERPROFILE%\\.claude", dark)
                ico = ico_from_pngs(pngs) if pngs else b""
            done += set_folder_icon(folder, ico) if ico else 0
        except Exception:                               # noqa: BLE001 -- cosmetic only
            continue
    return done
