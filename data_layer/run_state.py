"""Cross-process state for a detached fetch run.

A fetch runs as its own OS process (`data_layer/launcher.py` spawns
`scripts/run_fetch.py` detached) so it survives closing the Streamlit app. That
means the UI can no longer track the run through an in-process dict — it needs a
small file both processes can read. This module owns that file
(`settings.FETCH_RUN_STATE_FILE`, JSON): the launcher and the detached run write
it through its lifecycle, and any UI instance reads it to know whether a fetch is
running and how the last one ended.

Lifecycle (`status`):
    launching -> running -> done | error | cancelled

`launching` is written by the launching process (no PID yet); `running` is written
by the detached process itself, recording its OWN `os.getpid()` — that sidesteps
the py3.14 venv-launcher shim (its child PID differs from the spawned one), so the
PID we liveness-check is always the real worker. A run that dies hard without
writing a terminal status is caught by the PID liveness check in `is_active()`.
"""

from __future__ import annotations

import ctypes
import json
import os
import sys
from datetime import datetime, timezone

from config import settings

# A `launching` state with no PID yet is only trusted this long — past it, the
# detached process clearly never started (failed import / spawn), so it's not active.
_LAUNCH_GRACE_SECONDS = 60.0

_ACTIVE = ("launching", "running")
_TERMINAL = ("done", "error", "cancelled")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _write(state: dict) -> None:
    """Atomically write the state file (temp + os.replace), creating the dir."""
    path = settings.FETCH_RUN_STATE_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(state, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def read() -> dict | None:
    """The current run-state dict, or None if there is no state file yet."""
    path = settings.FETCH_RUN_STATE_FILE
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def clear() -> None:
    """Remove the state file (e.g. after a data reset, so no stale result lingers)."""
    try:
        settings.FETCH_RUN_STATE_FILE.unlink(missing_ok=True)
    except OSError:
        pass


# --------------------------------------------------------------------------- #
# lifecycle writers
# --------------------------------------------------------------------------- #
def mark_launching(label: str, mode: str) -> None:
    """Record that a detached run is being spawned (written by the launcher, no PID)."""
    _write({
        "status": "launching", "pid": None, "label": label, "mode": mode,
        "started_at": _now(), "finished_at": None, "summary": None, "error": None,
    })


def mark_running(label: str, mode: str) -> None:
    """Record that the run is underway (written by the detached process, own PID)."""
    _write({
        "status": "running", "pid": os.getpid(), "label": label, "mode": mode,
        "started_at": _now(), "finished_at": None, "summary": None, "error": None,
    })


def _finish(status: str, *, summary: dict | None = None, error: str | None = None) -> None:
    state = read() or {}
    state.update({
        "status": status, "finished_at": _now(),
        "summary": summary, "error": error,
    })
    _write(state)


def mark_done(summary: dict) -> None:
    """Record a clean finish with its run summary."""
    _finish("done", summary=summary)


def mark_cancelled(summary: dict) -> None:
    """Record a Stop-induced finish (run unwound at a batch boundary)."""
    _finish("cancelled", summary=summary)


def mark_error(message: str) -> None:
    """Record a failed finish with the error text."""
    _finish("error", error=message)


# --------------------------------------------------------------------------- #
# liveness
# --------------------------------------------------------------------------- #
def is_pid_alive(pid: int | None) -> bool:
    """True if a process with `pid` is currently running. Best-effort, no psutil."""
    if not pid:
        return False
    if sys.platform != "win32":  # POSIX fallback (dev only; this app is Windows)
        try:
            os.kill(int(pid), 0)
            return True
        except PermissionError:
            return True
        except OSError:
            return False
    try:
        SYNCHRONIZE = 0x00100000
        WAIT_TIMEOUT = 0x00000102  # signaled => exited; timeout => still running
        k32 = ctypes.windll.kernel32
        handle = k32.OpenProcess(SYNCHRONIZE, False, int(pid))
        if not handle:
            return False
        try:
            return k32.WaitForSingleObject(handle, 0) == WAIT_TIMEOUT
        finally:
            k32.CloseHandle(handle)
    except Exception:
        return False


def _launch_stale(state: dict) -> bool:
    """A `launching` state whose detached process never reported in within the grace."""
    started = state.get("started_at")
    if not started:
        return True
    try:
        age = (datetime.now(timezone.utc) - datetime.fromisoformat(started)).total_seconds()
    except (TypeError, ValueError):
        return False
    return age > _LAUNCH_GRACE_SECONDS


def is_active() -> bool:
    """True if a fetch is genuinely running right now (status + live PID).

    `running` is trusted only while its PID is alive (so a hard crash that skipped
    a terminal status doesn't wedge the UI on "running"). `launching` is trusted
    only briefly, until the detached process reports its PID via `mark_running`.
    """
    state = read()
    if not state or state.get("status") not in _ACTIVE:
        return False
    if state.get("status") == "running":
        return is_pid_alive(state.get("pid"))
    return not _launch_stale(state)  # launching


def ended_unexpectedly() -> bool:
    """True if the file still says 'running'/'launching' but the process is gone.

    Distinguishes a crash (no terminal status written) from a clean finish, so the
    UI can point the user at the console log instead of showing a stale "running".
    """
    state = read()
    if not state or state.get("status") not in _ACTIVE:
        return False
    return not is_active()


# --------------------------------------------------------------------------- #
# shutdown notice (the launcher's last-tab-closed hook)
# --------------------------------------------------------------------------- #
def announce_on_shutdown() -> None:
    """Print, to the terminal, whether a detached fetch keeps running after exit.

    Wired into `scripts/serve_ui.py`'s shutdown callback. It deliberately does
    NOT stop the fetch — the whole point is that a run survives the app exit.
    """
    state = read()
    if state and is_active():
        print(
            f"[FAMarket] App closing — a fetch is still running in the background "
            f"(pid {state.get('pid')}); it will continue. Watch {settings.LOG_FILE}.",
            flush=True,
        )
    else:
        print("[FAMarket] App closing — no fetch running.", flush=True)
