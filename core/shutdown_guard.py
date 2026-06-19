"""Warn (and let the user cancel) on logoff/shutdown while a run is active — Windows, stdlib only.

A fetch/analysis run is launched as a windowless DETACHED process
(`CREATE_NO_WINDOW`, see `data_layer/launcher.py`), so an absent-minded Start-menu
shutdown or sign-out would normally just KILL it with no warning. Windows only
warns about — and lets the user cancel — a shutdown for processes that own a
top-level window AND register a *block reason* via the Win32
`ShutdownBlockReasonCreate` API.

`ShutdownGuard` fills that gap. On `start()` it spins up a daemon thread that owns a
hidden (never-shown) top-level window, registers a block reason, and pumps messages
so the window keeps answering the shutdown query even while the main thread is busy
computing. While active, a normal logoff/shutdown shows
"This app is preventing you from shutting down — <reason>" and the user picks
*Shut down anyway* (the run is killed) or *Cancel* (the run keeps going). A FORCED
shutdown (`shutdown /f`, or "shut down anyway") still bypasses it — by design.

Best-effort and Windows-only: on another OS, or if any Win32 call fails, start()/
stop() are silent no-ops, so callers never have to guard the platform. Runs are
resumable anyway (batch commits + `fetch_status`), so this is a convenience guard,
not a data-safety one. No new dependency — pure Win32 via `ctypes`, like
`core/meminfo.py`.
"""

from __future__ import annotations

import ctypes
import os
import sys
import threading
from ctypes import wintypes

# Pointer-sized message params (correct on both 32- and 64-bit Python).
if ctypes.sizeof(ctypes.c_void_p) == 8:
    WPARAM = ctypes.c_uint64
    LPARAM = ctypes.c_int64
else:
    WPARAM = ctypes.c_uint32
    LPARAM = ctypes.c_int32
LRESULT = LPARAM

# stdcall callback signature for the window procedure.
WNDPROC = ctypes.WINFUNCTYPE(LRESULT, wintypes.HWND, wintypes.UINT, WPARAM, LPARAM)

# Window messages we care about.
WM_DESTROY = 0x0002
WM_CLOSE = 0x0010
WM_QUERYENDSESSION = 0x0011


class _POINT(ctypes.Structure):
    _fields_ = [("x", wintypes.LONG), ("y", wintypes.LONG)]


class _MSG(ctypes.Structure):
    _fields_ = [
        ("hwnd", wintypes.HWND),
        ("message", wintypes.UINT),
        ("wParam", WPARAM),
        ("lParam", LPARAM),
        ("time", wintypes.DWORD),
        ("pt", _POINT),
    ]


class _WNDCLASSW(ctypes.Structure):
    _fields_ = [
        ("style", wintypes.UINT),
        ("lpfnWndProc", WNDPROC),
        ("cbClsExtra", ctypes.c_int),
        ("cbWndExtra", ctypes.c_int),
        ("hInstance", wintypes.HINSTANCE),
        ("hIcon", wintypes.HICON),
        ("hCursor", wintypes.HANDLE),
        ("hbrBackground", wintypes.HBRUSH),
        ("lpszMenuName", wintypes.LPCWSTR),
        ("lpszClassName", wintypes.LPCWSTR),
    ]


def _on_windows() -> bool:
    return sys.platform == "win32"


class ShutdownGuard:
    """Block automatic logoff/shutdown (with a cancel-able prompt) while active.

    Use as a context manager or via explicit start()/stop(). Both are idempotent
    and never raise — any Win32 failure degrades to a no-op.
    """

    def __init__(self, reason: str) -> None:
        self._reason = reason
        self._thread: threading.Thread | None = None
        self._hwnd: int | None = None
        self._ready = threading.Event()
        # The class name must be unique per process so re-runs never collide.
        self._class_name = f"FAMarketShutdownGuard_{os.getpid()}"
        self._wndproc_cb: WNDPROC | None = None  # keep a ref so ctypes won't GC it

    # -- lifecycle ---------------------------------------------------------- #

    def start(self) -> None:
        if not _on_windows() or self._thread is not None:
            return
        self._thread = threading.Thread(
            target=self._run, name="shutdown-guard", daemon=True)
        self._thread.start()
        # Give the window a moment to come up; best-effort, never blocks the run.
        self._ready.wait(timeout=2.0)

    def stop(self) -> None:
        hwnd = self._hwnd
        if hwnd:
            # Ask the window thread to tear itself down (DestroyWindow must run on
            # the thread that created the window).
            try:
                ctypes.windll.user32.PostMessageW(hwnd, WM_CLOSE, 0, 0)
            except Exception:
                pass
        if self._thread:
            self._thread.join(timeout=2.0)
            self._thread = None

    def __enter__(self) -> "ShutdownGuard":
        self.start()
        return self

    def __exit__(self, *exc) -> None:
        self.stop()

    # -- worker thread ------------------------------------------------------ #

    def _wndproc(self, hwnd, msg, wparam, lparam):
        user32 = ctypes.windll.user32
        if msg == WM_QUERYENDSESSION:
            return 0  # FALSE -> block; Windows shows our registered reason + a cancel
        if msg == WM_CLOSE:
            # Clear the block reason while the HWND is still valid, then destroy it.
            user32.ShutdownBlockReasonDestroy(hwnd)
            user32.DestroyWindow(hwnd)
            return 0
        if msg == WM_DESTROY:
            user32.PostQuitMessage(0)
            return 0
        return user32.DefWindowProcW(hwnd, msg, wparam, lparam)

    def _run(self) -> None:
        try:
            self._build_and_pump()
        except Exception:
            # Best-effort: a guard failure must never take down the actual run.
            self._ready.set()

    def _build_and_pump(self) -> None:
        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32

        user32.DefWindowProcW.restype = LRESULT
        user32.DefWindowProcW.argtypes = [
            wintypes.HWND, wintypes.UINT, WPARAM, LPARAM]
        user32.RegisterClassW.restype = wintypes.ATOM
        user32.RegisterClassW.argtypes = [ctypes.POINTER(_WNDCLASSW)]
        user32.CreateWindowExW.restype = wintypes.HWND
        user32.CreateWindowExW.argtypes = [
            wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
            ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
            wintypes.HWND, ctypes.c_void_p, wintypes.HINSTANCE, ctypes.c_void_p]
        user32.ShutdownBlockReasonCreate.restype = wintypes.BOOL
        user32.ShutdownBlockReasonCreate.argtypes = [wintypes.HWND, wintypes.LPCWSTR]
        user32.ShutdownBlockReasonDestroy.restype = wintypes.BOOL
        user32.ShutdownBlockReasonDestroy.argtypes = [wintypes.HWND]
        user32.GetMessageW.argtypes = [
            ctypes.POINTER(_MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT]
        user32.GetMessageW.restype = ctypes.c_int  # BOOL, but -1 signals error
        user32.DestroyWindow.argtypes = [wintypes.HWND]
        kernel32.GetModuleHandleW.restype = wintypes.HMODULE
        kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]

        # Hold the callback on the instance so it survives the message loop.
        self._wndproc_cb = WNDPROC(self._wndproc)

        hinst = kernel32.GetModuleHandleW(None)
        wc = _WNDCLASSW()
        wc.lpfnWndProc = self._wndproc_cb
        wc.hInstance = hinst
        wc.lpszClassName = self._class_name
        if not user32.RegisterClassW(ctypes.byref(wc)):
            self._ready.set()
            return

        # A real (overlapped) top-level window — message-only windows never receive
        # WM_QUERYENDSESSION. It's never shown (no WS_VISIBLE, no ShowWindow).
        hwnd = user32.CreateWindowExW(
            0, self._class_name, "FAMarket", 0, 0, 0, 0, 0,
            None, None, hinst, None)
        if not hwnd:
            self._ready.set()
            return
        self._hwnd = hwnd
        user32.ShutdownBlockReasonCreate(hwnd, self._reason)
        self._ready.set()

        # Pump messages until the window is destroyed (GetMessageW returns 0 on
        # WM_QUIT, -1 on error).
        msg = _MSG()
        while True:
            ret = user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
            if ret in (0, -1):
                break
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))
        self._hwnd = None
