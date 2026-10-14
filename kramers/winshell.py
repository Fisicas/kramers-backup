"""
winshell.py -- small Windows shell helpers (ctypes only; safe no-ops elsewhere).

* known_folder("Downloads")        -> the real (possibly relocated) folder
* volume_serial("K:\\")            -> "1A2B-3C4D"
* shell_icon_rgba(path, px)        -> the icon Explorer shows for a file/folder/drive
* stock_icon_rgba(SIID_*, px)      -> a Windows stock icon (shield, warning, ...)
* glyph_rgba("\\uE895", px, rgb)   -> a Segoe Fluent Icons glyph
* package_logo(family_prefix, px)  -> PNG path of an installed MSIX app's logo
* rgba_to_png(w, h, rgba)          -> PNG bytes (Tk 8.6 loads these directly)

Images are produced at run time from the user's own Windows install, so no
third-party artwork ships with Kramers.
"""
from __future__ import annotations

import glob
import os
import re
import struct
import zlib
from pathlib import Path
from typing import Optional

IS_WINDOWS = os.name == "nt"

if IS_WINDOWS:
    import ctypes
    from ctypes import wintypes

    _shell32 = ctypes.windll.shell32
    _user32 = ctypes.windll.user32
    _gdi32 = ctypes.windll.gdi32
    _kernel32 = ctypes.windll.kernel32
    _ole32 = ctypes.windll.ole32

    class GUID(ctypes.Structure):
        _fields_ = [("Data1", wintypes.DWORD), ("Data2", wintypes.WORD),
                    ("Data3", wintypes.WORD), ("Data4", ctypes.c_ubyte * 8)]

        def __init__(self, s: str):
            super().__init__()
            h = s.strip("{}").replace("-", "")
            self.Data1, self.Data2, self.Data3 = int(h[:8], 16), int(h[8:12], 16), int(h[12:16], 16)
            for i in range(8):
                self.Data4[i] = int(h[16 + 2 * i:18 + 2 * i], 16)

    class SHFILEINFOW(ctypes.Structure):
        _fields_ = [("hIcon", wintypes.HICON), ("iIcon", ctypes.c_int),
                    ("dwAttributes", wintypes.DWORD),
                    ("szDisplayName", wintypes.WCHAR * 260), ("szTypeName", wintypes.WCHAR * 80)]

    class SHSTOCKICONINFO(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.DWORD), ("hIcon", wintypes.HICON),
                    ("iSysImageIndex", ctypes.c_int), ("iIcon", ctypes.c_int),
                    ("szPath", wintypes.WCHAR * 260)]

    class BITMAPINFOHEADER(ctypes.Structure):
        _fields_ = [("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG),
                    ("biHeight", wintypes.LONG), ("biPlanes", wintypes.WORD),
                    ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                    ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", wintypes.LONG),
                    ("biYPelsPerMeter", wintypes.LONG), ("biClrUsed", wintypes.DWORD),
                    ("biClrImportant", wintypes.DWORD)]

    _gdi32.CreateDIBSection.restype = wintypes.HBITMAP
    _gdi32.CreateDIBSection.argtypes = [wintypes.HDC, ctypes.c_void_p, wintypes.UINT,
                                        ctypes.POINTER(ctypes.c_void_p), wintypes.HANDLE, wintypes.DWORD]
    _gdi32.CreateCompatibleDC.restype = wintypes.HDC
    _gdi32.CreateCompatibleDC.argtypes = [wintypes.HDC]
    _gdi32.SelectObject.restype = wintypes.HGDIOBJ
    _gdi32.SelectObject.argtypes = [wintypes.HDC, wintypes.HGDIOBJ]
    _gdi32.DeleteObject.argtypes = [wintypes.HGDIOBJ]
    _gdi32.DeleteDC.argtypes = [wintypes.HDC]
    _gdi32.CreateFontW.restype = wintypes.HFONT
    _gdi32.SetTextColor.argtypes = [wintypes.HDC, wintypes.COLORREF]
    _gdi32.SetBkMode.argtypes = [wintypes.HDC, ctypes.c_int]
    _user32.DrawTextW.argtypes = [wintypes.HDC, wintypes.LPCWSTR, ctypes.c_int,
                                  ctypes.POINTER(wintypes.RECT), wintypes.UINT]
    _user32.DrawIconEx.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int, wintypes.HICON,
                                   ctypes.c_int, ctypes.c_int, wintypes.UINT, wintypes.HBRUSH,
                                   wintypes.UINT]
    _user32.DestroyIcon.argtypes = [wintypes.HICON]
    _shell32.SHGetFileInfoW.restype = ctypes.c_size_t
    _shell32.SHGetFileInfoW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD,
                                        ctypes.POINTER(SHFILEINFOW), wintypes.UINT, wintypes.UINT]
    _shell32.SHDefExtractIconW.argtypes = [wintypes.LPCWSTR, ctypes.c_int, wintypes.UINT,
                                           ctypes.POINTER(wintypes.HICON),
                                           ctypes.POINTER(wintypes.HICON), wintypes.UINT]

KNOWN_FOLDERS = {
    "Desktop": "{B4BFCC3A-DB2C-424C-B029-7FE99A87C641}",
    "Downloads": "{374DE290-123F-4565-9164-39C4925E467B}",
    "Documents": "{FDD39AD0-238F-46AF-ADB4-6C85480369C7}",
    "Pictures": "{33E28130-4E1E-4676-835A-98395C3BC3BB}",
    "Music": "{4BD8D571-6D19-48D3-BE97-422220080E43}",
    "Videos": "{18989B1D-99B5-455B-841C-AB7C74E4DDFC}",
}

# Stock icon ids (SHSTOCKICONID).
SIID_FOLDER, SIID_DRIVEREMOVE, SIID_DRIVEFIXED = 3, 7, 8
SIID_SHIELD, SIID_WARNING, SIID_INFO, SIID_ERROR = 77, 78, 79, 80

# Segoe Fluent Icons / Segoe MDL2 Assets code points.
GLYPHS = {
    "play": "\uE768", "pause": "\uE769", "sync": "\uE895", "cancel": "\uE711", "folder": "\uE8B7",
    "open": "\uE838", "save": "\uE74E", "settings": "\uE713", "shield": "\uEA18",
    "preview": "\uE7B3", "add": "\uE710", "remove": "\uE738", "edit": "\uE70F",
    "check": "\uE73E", "warning": "\uE7BA", "error": "\uEA39", "info": "\uE946",
    "clock": "\uE823", "drive": "\uEDA2", "calendar": "\uE787", "history": "\uE81C",
    "document": "\uE8A5", "link": "\uE71B", "delete": "\uE74D", "up": "\uE70E",
    "down": "\uE70D",
}


# --------------------------------------------------------------------------- #
# Folders, volumes, theme
# --------------------------------------------------------------------------- #

def known_folder(name: str) -> Optional[str]:
    if not IS_WINDOWS or name not in KNOWN_FOLDERS:
        fallback = Path.home() / name
        return str(fallback) if fallback.exists() else None
    out = ctypes.c_wchar_p()
    hr = _shell32.SHGetKnownFolderPath(ctypes.byref(GUID(KNOWN_FOLDERS[name])), 0, None,
                                       ctypes.byref(out))
    if hr != 0:
        return None
    try:
        return out.value
    finally:
        _ole32.CoTaskMemFree(out)


def volume_info(path: str) -> tuple[str, str]:
    """(serial "XXXX-XXXX", label) of the volume holding path; ("", "") if absent."""
    if not IS_WINDOWS:
        return "", ""
    drive = os.path.splitdrive(os.path.abspath(path))[0] + "\\"
    label = ctypes.create_unicode_buffer(261)
    fs = ctypes.create_unicode_buffer(261)
    serial = wintypes.DWORD()
    ok = _kernel32.GetVolumeInformationW(drive, label, 261, ctypes.byref(serial), None, None, fs, 261)
    if not ok:
        return "", ""
    s = serial.value
    return f"{s >> 16:04X}-{s & 0xFFFF:04X}", label.value


def bitlocker_state(path: str) -> str:
    """'on' | 'off' | 'unknown' for the volume holding path (no admin rights needed).

    Reads the shell property System.Volume.BitLockerProtection through PowerShell
    (about 0.3 s); call it off the UI thread."""
    if not IS_WINDOWS or not path:
        return "unknown"
    import subprocess
    drive = os.path.splitdrive(os.path.abspath(path))[0] + "\\"
    script = ("(New-Object -ComObject Shell.Application).NameSpace('%s').Self."
              "ExtendedProperty('System.Volume.BitLockerProtection')" % drive.replace("'", "''"))
    try:
        out = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
                             capture_output=True, text=True, timeout=15, creationflags=0x08000000)
        value = out.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    # 2 = BitLocker off; 1/3/5 = protection on (unlocked/locked); 4/6 = encrypting/decrypting
    return {"2": "off", "1": "on", "3": "on", "5": "on", "4": "on", "6": "on"}.get(value, "unknown")


def system_uses_dark_mode() -> bool:
    if not IS_WINDOWS:
        return False
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize") as k:
            return winreg.QueryValueEx(k, "AppsUseLightTheme")[0] == 0
    except OSError:
        return False


def enable_dpi_awareness() -> None:
    if not IS_WINDOWS:
        return
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)   # system-DPI aware
    except (AttributeError, OSError):
        try:
            _user32.SetProcessDPIAware()
        except (AttributeError, OSError):
            pass


def set_app_user_model_id(app_id: str) -> None:
    if IS_WINDOWS:
        try:
            _shell32.SetCurrentProcessExplicitAppUserModelID(ctypes.c_wchar_p(app_id))
        except (AttributeError, OSError):
            pass


def set_dark_title_bar(tk_window, dark: bool) -> None:
    """Match the window frame to the app theme (Windows 10 20H1+ / 11)."""
    if not IS_WINDOWS:
        return
    try:
        tk_window.update_idletasks()
        hwnd = _user32.GetParent(tk_window.winfo_id())
        value = ctypes.c_int(1 if dark else 0)
        for attr in (20, 19):    # DWMWA_USE_IMMERSIVE_DARK_MODE (new, old)
            if ctypes.windll.dwmapi.DwmSetWindowAttribute(
                    hwnd, attr, ctypes.byref(value), ctypes.sizeof(value)) == 0:
                break
    except (AttributeError, OSError):
        pass


def icon_font() -> Optional[str]:
    fonts = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"
    if (fonts / "SegoeIcons.ttf").exists():
        return "Segoe Fluent Icons"
    if (fonts / "segmdl2.ttf").exists():
        return "Segoe MDL2 Assets"
    return None


# --------------------------------------------------------------------------- #
# Rendering helpers
# --------------------------------------------------------------------------- #

def _dib(px: int):
    hdc = _gdi32.CreateCompatibleDC(None)
    bmi = BITMAPINFOHEADER()
    bmi.biSize = ctypes.sizeof(BITMAPINFOHEADER)
    bmi.biWidth, bmi.biHeight, bmi.biPlanes, bmi.biBitCount = px, -px, 1, 32
    bits = ctypes.c_void_p()
    hbmp = _gdi32.CreateDIBSection(hdc, ctypes.byref(bmi), 0, ctypes.byref(bits), None, 0)
    old = _gdi32.SelectObject(hdc, hbmp)
    return hdc, hbmp, old, bits


def _free_dib(hdc, hbmp, old) -> None:
    _gdi32.SelectObject(hdc, old)
    _gdi32.DeleteObject(hbmp)
    _gdi32.DeleteDC(hdc)


def _hicon_to_rgba(hicon, px: int) -> bytes:
    """Draw on black and on white; recover colour and alpha from the pair."""
    layers = []
    for bg in (0x00, 0xFF):
        hdc, hbmp, old, bits = _dib(px)
        ctypes.memset(bits, bg, px * px * 4)
        _user32.DrawIconEx(hdc, 0, 0, hicon, px, px, 0, None, 3)   # DI_NORMAL
        _gdi32.GdiFlush()
        layers.append(ctypes.string_at(bits, px * px * 4))
        _free_dib(hdc, hbmp, old)
    black, white = layers
    out = bytearray(px * px * 4)
    for i in range(0, len(out), 4):
        a = 255 - max(white[i + c] - black[i + c] for c in range(3))
        a = max(0, min(255, a))
        if a:
            for c in range(3):    # BGR -> RGB, un-premultiply
                out[i + 2 - c] = min(255, black[i + c] * 255 // a)
        out[i + 3] = a
    return bytes(out)


def _extract(path: str, index: int, px: int):
    hicon = wintypes.HICON()
    hr = _shell32.SHDefExtractIconW(path, index, 0, ctypes.byref(hicon), None, px)
    return hicon if hr == 0 and hicon.value else None


def _desktop_ini_icon(folder: str) -> Optional[tuple[str, int]]:
    ini = os.path.join(folder, "desktop.ini")
    if not os.path.isfile(ini):
        return None
    try:
        raw = open(ini, "rb").read()
        text = raw.decode("utf-16") if raw[:2] in (b"\xff\xfe", b"\xfe\xff") else raw.decode("mbcs", "replace")
    except (OSError, UnicodeError):
        return None
    m = re.search(r"^\s*IconResource\s*=\s*(.+?),\s*(-?\d+)\s*$", text, re.M | re.I)
    if not m:
        return None
    return os.path.expandvars(m.group(1).strip()), int(m.group(2))


def shell_icon_rgba(path: str, px: int = 16) -> Optional[bytes]:
    """The icon Explorer shows for path (special folders, drives, executables)."""
    if not IS_WINDOWS or not path:
        return None
    info = SHFILEINFOW()
    SHGFI_ICON, SHGFI_ICONLOCATION, SHGFI_SMALLICON = 0x100, 0x1000, 0x1
    hicon = None
    custom = _desktop_ini_icon(path)          # Desktop, Downloads, customised folders
    if custom:
        hicon = _extract(custom[0], custom[1], px)
    if hicon is None and _shell32.SHGetFileInfoW(path, 0, ctypes.byref(info), ctypes.sizeof(info),
                                                 SHGFI_ICONLOCATION):
        if info.szDisplayName:
            hicon = _extract(info.szDisplayName, info.iIcon, px)
    if hicon is None:
        flags = SHGFI_ICON | (SHGFI_SMALLICON if px <= 16 else 0)
        if not _shell32.SHGetFileInfoW(path, 0, ctypes.byref(info), ctypes.sizeof(info), flags):
            return None
        hicon = wintypes.HICON(info.hIcon)
    if not hicon or not hicon.value:
        return None
    try:
        return _hicon_to_rgba(hicon, px)
    finally:
        _user32.DestroyIcon(hicon)


def stock_icon_rgba(siid: int, px: int = 16) -> Optional[bytes]:
    if not IS_WINDOWS:
        return None
    info = SHSTOCKICONINFO()
    info.cbSize = ctypes.sizeof(info)
    if _shell32.SHGetStockIconInfo(siid, 0, ctypes.byref(info)) != 0:   # SHGSI_ICONLOCATION
        return None
    hicon = _extract(info.szPath, info.iIcon, px)
    if not hicon:
        return None
    try:
        return _hicon_to_rgba(hicon, px)
    finally:
        _user32.DestroyIcon(hicon)


def glyph_rgba(char: str, px: int, rgb=(0, 0, 0), font: Optional[str] = None) -> Optional[bytes]:
    """Render one icon-font glyph as anti-aliased RGBA."""
    font = font or icon_font()
    if not IS_WINDOWS or not font:
        return None
    hdc, hbmp, old, bits = _dib(px)
    ctypes.memset(bits, 0, px * px * 4)
    hfont = _gdi32.CreateFontW(-px, 0, 0, 0, 400, 0, 0, 0, 1, 0, 0, 4, 0, font)  # ANTIALIASED_QUALITY
    oldf = _gdi32.SelectObject(hdc, hfont)
    _gdi32.SetTextColor(hdc, 0x00FFFFFF)
    _gdi32.SetBkMode(hdc, 1)
    rect = wintypes.RECT(0, 0, px, px)
    _user32.DrawTextW(hdc, char, 1, ctypes.byref(rect), 0x1 | 0x4 | 0x20 | 0x800)
    _gdi32.GdiFlush()
    raw = ctypes.string_at(bits, px * px * 4)
    _gdi32.SelectObject(hdc, oldf)
    _gdi32.DeleteObject(hfont)
    _free_dib(hdc, hbmp, old)
    out = bytearray(px * px * 4)
    r, g, b = rgb
    for i in range(0, len(out), 4):
        out[i], out[i + 1], out[i + 2], out[i + 3] = r, g, b, raw[i + 1]
    return bytes(out)


def rgba_to_png(w: int, h: int, rgba: bytes) -> bytes:
    def chunk(tag: bytes, data: bytes) -> bytes:
        return (struct.pack(">I", len(data)) + tag + data
                + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))
    rows = b"".join(b"\x00" + rgba[y * w * 4:(y + 1) * w * 4] for y in range(h))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(rows, 9)) + chunk(b"IEND", b""))


# --------------------------------------------------------------------------- #
# MSIX (packaged) apps such as the Claude and Codex desktop apps
# --------------------------------------------------------------------------- #

def package_root(family_prefix: str) -> Optional[str]:
    """Install folder of the newest installed package whose name starts with prefix."""
    if not IS_WINDOWS:
        return None
    import winreg
    base = (r"Software\Classes\Local Settings\Software\Microsoft\Windows"
            r"\CurrentVersion\AppModel\Repository\Packages")
    best = None
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, base) as k:
            i = 0
            while True:
                try:
                    name = winreg.EnumKey(k, i)
                except OSError:
                    break
                i += 1
                if name.lower().startswith(family_prefix.lower() + "_"):
                    try:
                        with winreg.OpenKey(k, name) as pk:
                            root = winreg.QueryValueEx(pk, "PackageRootFolder")[0]
                        if os.path.isdir(root) and (best is None or name > best[0]):
                            best = (name, root)
                    except OSError:
                        pass
    except OSError:
        return None
    return best[1] if best else None


def package_logo(family_prefix: str, px: int = 16, dark: bool = True) -> Optional[str]:
    """A PNG logo (Square44x44 target-size variant) for an installed MSIX app,
    preferring the variant drawn for a dark or light background."""
    root = package_root(family_prefix)
    if not root:
        return None
    try:
        manifest = Path(root, "AppxManifest.xml").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    m = re.search(r'Square44x44Logo="([^"]+)"', manifest)
    if not m:
        return None
    rel = m.group(1).replace("/", "\\")
    stem, ext = os.path.splitext(os.path.join(root, rel))
    candidates = glob.glob(glob.escape(stem) + ".targetsize-*" + ext)
    want = "_altform-unplated" if dark else "_altform-lightunplated"
    sized = []
    for c in candidates:
        mm = re.search(r"targetsize-(\d+)(_altform-\w+)?\.", c)
        if mm:
            alt = mm.group(2) or ""
            rank = 0 if alt == want else (1 if not alt else 2)
            sized.append((int(mm.group(1)), rank, c))
    exact = [s for s in sized if s[0] == px]
    if exact:
        return sorted(exact, key=lambda t: t[1])[0][2]
    bigger = sorted((s for s in sized if s[0] > px), key=lambda t: (t[0], t[1]))
    return bigger[0][2] if bigger else (candidates[0] if candidates else None)
