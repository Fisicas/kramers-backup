"""
tray.py -- Kramers' notification-area (system tray) icon, ctypes only.

The icon lives on its own thread with a hidden window and a Win32 message
loop. Clicks and menu choices are handed to the Tk side through `on_event`
(called on the tray thread: the callback must only queue work for Tk).

The hidden window's class name doubles as the single-instance beacon: a
second Kramers posts WM_SHOW_EXISTING to it and exits.
"""
from __future__ import annotations

import ctypes
import threading
from ctypes import wintypes
from typing import Callable

WINDOW_CLASS = "KramersTrayWindow"
WM_TRAY = 0x8000 + 1            # WM_APP + 1
WM_SHOW_EXISTING = 0x8000 + 2   # posted by a second instance
WM_DESTROY, WM_CLOSE, WM_COMMAND, WM_NULL = 0x0002, 0x0010, 0x0111, 0x0000
WM_LBUTTONUP, WM_LBUTTONDBLCLK, WM_RBUTTONUP, WM_CONTEXTMENU = 0x0202, 0x0203, 0x0205, 0x007B
NIM_ADD, NIM_MODIFY, NIM_DELETE = 0, 1, 2
NIN_BALLOONUSERCLICK = 0x0405
NIF_MESSAGE, NIF_ICON, NIF_TIP, NIF_INFO, NIF_SHOWTIP = 0x1, 0x2, 0x4, 0x10, 0x80
NIIF_INFO, NIIF_WARNING, NIIF_ERROR = 1, 2, 3
MF_STRING, MF_GRAYED, MF_SEPARATOR, MF_DEFAULT = 0x0, 0x1, 0x800, 0x1000
TPM_RIGHTBUTTON, TPM_RETURNCMD, TPM_NONOTIFY = 0x2, 0x100, 0x80

user32 = ctypes.windll.user32
shell32 = ctypes.windll.shell32
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

LRESULT = ctypes.c_ssize_t
WNDPROC = ctypes.WINFUNCTYPE(LRESULT, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)


class WNDCLASSEXW(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.UINT), ("style", wintypes.UINT), ("lpfnWndProc", WNDPROC),
                ("cbClsExtra", ctypes.c_int), ("cbWndExtra", ctypes.c_int),
                ("hInstance", wintypes.HINSTANCE), ("hIcon", wintypes.HICON),
                ("hCursor", wintypes.HANDLE), ("hbrBackground", wintypes.HBRUSH),
                ("lpszMenuName", wintypes.LPCWSTR), ("lpszClassName", wintypes.LPCWSTR),
                ("hIconSm", wintypes.HICON)]


class GUID(ctypes.Structure):
    _fields_ = [("a", wintypes.DWORD), ("b", wintypes.WORD), ("c", wintypes.WORD),
                ("d", ctypes.c_ubyte * 8)]


class NOTIFYICONDATAW(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("hWnd", wintypes.HWND), ("uID", wintypes.UINT),
                ("uFlags", wintypes.UINT), ("uCallbackMessage", wintypes.UINT),
                ("hIcon", wintypes.HICON), ("szTip", wintypes.WCHAR * 128),
                ("dwState", wintypes.DWORD), ("dwStateMask", wintypes.DWORD),
                ("szInfo", wintypes.WCHAR * 256), ("uVersion", wintypes.UINT),
                ("szInfoTitle", wintypes.WCHAR * 64), ("dwInfoFlags", wintypes.DWORD),
                ("guidItem", GUID), ("hBalloonIcon", wintypes.HICON)]


user32.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
user32.DefWindowProcW.restype = LRESULT
user32.CreateWindowExW.argtypes = [wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
                                   ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                   wintypes.HWND, wintypes.HMENU, wintypes.HINSTANCE, wintypes.LPVOID]
user32.CreateWindowExW.restype = wintypes.HWND
user32.RegisterClassExW.argtypes = [ctypes.POINTER(WNDCLASSEXW)]
user32.LoadImageW.argtypes = [wintypes.HINSTANCE, wintypes.LPCWSTR, wintypes.UINT,
                              ctypes.c_int, ctypes.c_int, wintypes.UINT]
user32.LoadImageW.restype = wintypes.HANDLE
user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
user32.FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
user32.FindWindowW.restype = wintypes.HWND
user32.AppendMenuW.argtypes = [wintypes.HMENU, wintypes.UINT, ctypes.c_size_t, wintypes.LPCWSTR]
user32.CreatePopupMenu.restype = wintypes.HMENU
user32.TrackPopupMenu.argtypes = [wintypes.HMENU, wintypes.UINT, ctypes.c_int, ctypes.c_int,
                                  ctypes.c_int, wintypes.HWND, wintypes.LPVOID]
user32.DestroyMenu.argtypes = [wintypes.HMENU]
user32.SetForegroundWindow.argtypes = [wintypes.HWND]
shell32.Shell_NotifyIconW.argtypes = [wintypes.DWORD, ctypes.POINTER(NOTIFYICONDATAW)]
kernel32.GetModuleHandleW.restype = wintypes.HMODULE
kernel32.CreateMutexW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR]
kernel32.CreateMutexW.restype = wintypes.HANDLE


# --------------------------------------------------------------------------- #
# Single instance
# --------------------------------------------------------------------------- #

_mutex = None


def claim_single_instance(name: str = "Local\\Kramers.Backup.App") -> bool:
    """True if this is the only Kramers window process. Otherwise the running
    one is asked to show itself and False is returned."""
    global _mutex
    _mutex = kernel32.CreateMutexW(None, False, name)
    if ctypes.get_last_error() != 183:          # ERROR_ALREADY_EXISTS
        return True
    hwnd = user32.FindWindowW(WINDOW_CLASS, None)
    if hwnd:
        user32.PostMessageW(hwnd, WM_SHOW_EXISTING, 0, 0)
    return False


def running_instance() -> bool:
    return bool(user32.FindWindowW(WINDOW_CLASS, None))


# --------------------------------------------------------------------------- #
# Tray icon
# --------------------------------------------------------------------------- #

class TrayIcon:
    """menu_items: list of (key, label, enabled, is_default); key None = separator."""

    def __init__(self, icon_path: str, tooltip: str, on_event: Callable[[str], None]):
        self.icon_path = icon_path
        self.tooltip = tooltip
        self.on_event = on_event
        self.menu_items: list = [("open", "Open", True, True), ("quit", "Quit", True, False)]
        self.hwnd = None
        self.hicon = None
        self.added = False
        self._ready = threading.Event()
        self._wndproc = WNDPROC(self._proc)     # keep a reference for the thread's lifetime
        self._taskbar_created = user32.RegisterWindowMessageW("TaskbarCreated")
        self.thread = threading.Thread(target=self._run, name="kramers-tray", daemon=True)

    # ---- public (any thread) ---------------------------------------------- #
    def start(self) -> bool:
        self.thread.start()
        self._ready.wait(5)
        return bool(self.hwnd)

    def set_tooltip(self, text: str) -> None:
        self.tooltip = text[:127]
        if self.hwnd:
            nid = self._nid(NIF_TIP | NIF_SHOWTIP)
            shell32.Shell_NotifyIconW(NIM_MODIFY, ctypes.byref(nid))

    def notify(self, title: str, text: str, level: str = "info") -> None:
        if not self.hwnd:
            return
        nid = self._nid(NIF_INFO)
        nid.szInfoTitle = title[:63]
        nid.szInfo = text[:255]
        nid.dwInfoFlags = {"info": NIIF_INFO, "warning": NIIF_WARNING, "error": NIIF_ERROR}[level]
        shell32.Shell_NotifyIconW(NIM_MODIFY, ctypes.byref(nid))

    def stop(self) -> None:
        if self.hwnd:
            user32.PostMessageW(self.hwnd, WM_CLOSE, 0, 0)
            self.thread.join(timeout=3)

    # ---- tray thread ------------------------------------------------------- #
    def _nid(self, flags: int) -> NOTIFYICONDATAW:
        nid = NOTIFYICONDATAW()
        nid.cbSize = ctypes.sizeof(NOTIFYICONDATAW)
        nid.hWnd = self.hwnd
        nid.uID = 1
        nid.uFlags = flags
        nid.uCallbackMessage = WM_TRAY
        nid.hIcon = self.hicon
        nid.szTip = self.tooltip[:127]
        return nid

    def _add_icon(self) -> None:
        nid = self._nid(NIF_MESSAGE | NIF_ICON | NIF_TIP | NIF_SHOWTIP)
        self.added = bool(shell32.Shell_NotifyIconW(NIM_ADD, ctypes.byref(nid)))

    def _run(self) -> None:
        hinst = kernel32.GetModuleHandleW(None)
        wc = WNDCLASSEXW()
        wc.cbSize = ctypes.sizeof(WNDCLASSEXW)
        wc.lpfnWndProc = self._wndproc
        wc.hInstance = hinst
        wc.lpszClassName = WINDOW_CLASS
        user32.RegisterClassExW(ctypes.byref(wc))
        self.hwnd = user32.CreateWindowExW(0, WINDOW_CLASS, "Kramers", 0, 0, 0, 0, 0,
                                           None, None, hinst, None)
        cx, cy = user32.GetSystemMetrics(49), user32.GetSystemMetrics(50)   # SM_CXSMICON
        self.hicon = user32.LoadImageW(None, self.icon_path, 1, cx, cy, 0x10)  # IMAGE_ICON, LR_LOADFROMFILE
        if self.hwnd:
            self._add_icon()
        self._ready.set()
        msg = wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))

    def _menu(self) -> None:
        menu = user32.CreatePopupMenu()
        ids = {}
        for i, (key, label, enabled, default) in enumerate(self.menu_items, start=1):
            if key is None:
                user32.AppendMenuW(menu, MF_SEPARATOR, 0, None)
                continue
            flags = MF_STRING | (0 if enabled else MF_GRAYED) | (MF_DEFAULT if default else 0)
            user32.AppendMenuW(menu, flags, i, label)
            ids[i] = key
        pt = wintypes.POINT()
        user32.GetCursorPos(ctypes.byref(pt))
        user32.SetForegroundWindow(self.hwnd)     # so the menu closes when clicking elsewhere
        cmd = user32.TrackPopupMenu(menu, TPM_RIGHTBUTTON | TPM_RETURNCMD | TPM_NONOTIFY,
                                    pt.x, pt.y, 0, self.hwnd, None)
        user32.PostMessageW(self.hwnd, WM_NULL, 0, 0)
        user32.DestroyMenu(menu)
        if cmd in ids:
            self.on_event(ids[cmd])

    def _proc(self, hwnd, msg, wparam, lparam):
        if msg == WM_TRAY:
            ev = lparam & 0xFFFF
            if ev in (WM_LBUTTONUP, WM_LBUTTONDBLCLK):
                self.on_event("open")
            elif ev in (WM_RBUTTONUP, WM_CONTEXTMENU):
                self._menu()
            elif ev == NIN_BALLOONUSERCLICK:
                self.on_event("open")
            return 0
        if msg == WM_SHOW_EXISTING:
            self.on_event("open")
            return 0
        if msg == self._taskbar_created:        # Explorer restarted: re-add the icon
            self._add_icon()
            return 0
        if msg == WM_CLOSE:
            user32.DestroyWindow(hwnd)
            return 0
        if msg == WM_DESTROY:
            nid = self._nid(0)
            shell32.Shell_NotifyIconW(NIM_DELETE, ctypes.byref(nid))
            user32.PostQuitMessage(0)
            return 0
        return user32.DefWindowProcW(hwnd, msg, wparam, lparam)
