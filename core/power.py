"""Opt a long run out of Windows power throttling ("efficiency mode" / EcoQoS).

Windows 11 throttles processes it judges to be background work. A detached fetch or
analysis run has no window, and neither does the UI server that starts it, so
Windows can move it to the efficiency cores at low clock. Measured 2026-11-08
(ROADMAP 8.5): the same full analysis loop ran at ~123 ms/symbol when launched from
the app versus ~18 ms/symbol from a terminal, and data loading took 10 min against
3.5 min. Asking for HighQoS removes that penalty.

This follows Microsoft's documented `SetProcessInformation(ProcessPowerThrottling)`
"HighQoS" pattern: set the EXECUTION_SPEED bit in ControlMask and clear it in
StateMask. Stdlib `ctypes` only, best-effort, and a no-op returning False off Windows.
"""

from __future__ import annotations

import ctypes
import sys

_PROCESS_POWER_THROTTLING = 4            # PROCESS_INFORMATION_CLASS.ProcessPowerThrottling
_THROTTLING_CURRENT_VERSION = 1
_EXECUTION_SPEED = 0x1                   # PROCESS_POWER_THROTTLING_EXECUTION_SPEED


class _PowerThrottlingState(ctypes.Structure):
    _fields_ = [
        ("Version", ctypes.c_ulong),
        ("ControlMask", ctypes.c_ulong),
        ("StateMask", ctypes.c_ulong),
    ]


def disable_power_throttling() -> bool:
    """Ask Windows to never throttle this process. True if the call succeeded."""
    if sys.platform != "win32":
        return False
    try:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.GetCurrentProcess.restype = ctypes.c_void_p
        kernel32.SetProcessInformation.argtypes = (
            ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p, ctypes.c_ulong)
        kernel32.SetProcessInformation.restype = ctypes.c_int
        state = _PowerThrottlingState(_THROTTLING_CURRENT_VERSION, _EXECUTION_SPEED, 0)
        ok = kernel32.SetProcessInformation(
            kernel32.GetCurrentProcess(), _PROCESS_POWER_THROTTLING,
            ctypes.byref(state), ctypes.sizeof(state))
        return bool(ok)
    except (OSError, AttributeError):
        return False
