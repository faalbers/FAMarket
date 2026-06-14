"""Lightweight process/system memory reporting (Windows, stdlib only).

A single observability helper: report this process's PEAK resident memory ("working
set") and total physical RAM, so each analysis run can self-report its high-water
mark in the log (see `analysis_layer.pipeline`). No new dependency (`psutil` avoided
on purpose) — the numbers come straight from the Win32 API via `ctypes`.

Everything is best-effort: on a non-Windows host, or if a call fails, the getters
return None and `peak_ram_summary()` returns "" so callers can log unconditionally.
"""

from __future__ import annotations

import ctypes
import sys
from ctypes import wintypes

_GB = 1024 ** 3


class _MEMORYSTATUSEX(ctypes.Structure):
    _fields_ = [
        ("dwLength", ctypes.c_ulong),
        ("dwMemoryLoad", ctypes.c_ulong),
        ("ullTotalPhys", ctypes.c_ulonglong),
        ("ullAvailPhys", ctypes.c_ulonglong),
        ("ullTotalPageFile", ctypes.c_ulonglong),
        ("ullAvailPageFile", ctypes.c_ulonglong),
        ("ullTotalVirtual", ctypes.c_ulonglong),
        ("ullAvailVirtual", ctypes.c_ulonglong),
        ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
    ]


class _PROCESS_MEMORY_COUNTERS(ctypes.Structure):
    _fields_ = [
        ("cb", ctypes.c_ulong),
        ("PageFaultCount", ctypes.c_ulong),
        ("PeakWorkingSetSize", ctypes.c_size_t),
        ("WorkingSetSize", ctypes.c_size_t),
        ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
        ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
        ("PagefileUsage", ctypes.c_size_t),
        ("PeakPagefileUsage", ctypes.c_size_t),
    ]


def _on_windows() -> bool:
    return sys.platform == "win32"


def peak_working_set() -> int | None:
    """Peak resident memory (bytes) of this process so far — None if unavailable."""
    if not _on_windows():
        return None
    try:
        k32, psapi = ctypes.windll.kernel32, ctypes.windll.psapi
        k32.GetCurrentProcess.restype = wintypes.HANDLE
        psapi.GetProcessMemoryInfo.argtypes = [
            wintypes.HANDLE, ctypes.POINTER(_PROCESS_MEMORY_COUNTERS), ctypes.c_ulong]
        psapi.GetProcessMemoryInfo.restype = wintypes.BOOL
        counters = _PROCESS_MEMORY_COUNTERS()
        counters.cb = ctypes.sizeof(counters)
        if not psapi.GetProcessMemoryInfo(k32.GetCurrentProcess(),
                                          ctypes.byref(counters), counters.cb):
            return None
        return int(counters.PeakWorkingSetSize)
    except Exception:
        return None


def system_total() -> int | None:
    """Total physical RAM (bytes) — None if unavailable."""
    if not _on_windows():
        return None
    try:
        stat = _MEMORYSTATUSEX()
        stat.dwLength = ctypes.sizeof(stat)
        if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat)):
            return None
        return int(stat.ullTotalPhys)
    except Exception:
        return None


def peak_ram_summary() -> str:
    """e.g. 'peak RAM 11.2 GB / 32.0 GB system' — empty string when unavailable."""
    peak = peak_working_set()
    if peak is None:
        return ""
    total = system_total()
    if total:
        return f"peak RAM {peak / _GB:.1f} GB / {total / _GB:.1f} GB system"
    return f"peak RAM {peak / _GB:.1f} GB"
