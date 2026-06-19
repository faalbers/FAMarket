"""Launch a fetch as a detached OS process so it survives closing the app.

The Fetch Control page used to run the pipeline on a background *thread* inside the
Streamlit process — which meant closing the browser tab (auto-shutdown) killed the
fetch. Instead we spawn `scripts/run_fetch.py` as a fully detached child: its own
process group, no console, std streams redirected to a file. Killing the Streamlit
app then leaves it running to completion. Progress is in the run log
(`settings.LOG_FILE`); lifecycle/state is in `data_layer/run_state.py`.

Single-fetch invariant: `launch_detached_fetch` refuses to start if a fetch is
already active, so there is never a way to run two at once (the UI also gates its
buttons, but this is the authoritative check).
"""

from __future__ import annotations

import subprocess
import sys

from config import settings
from data_layer import run_state


def launch_detached_fetch(
    *,
    discover: bool = True,
    subset: list[str] | None = None,
    respect_lock: bool = True,
    run_backup: bool = True,
    analysis_only: bool = False,
    label: str,
) -> dict:
    """Spawn the pipeline as a detached process. Returns {launched, pid|reason}.

    Returns ``{"launched": False, "reason": ...}`` without spawning if a fetch is
    already running. Otherwise marks the run `launching`, spawns the child, and
    returns its PID. The child flips the state to `running` (with its own PID) and
    on to a terminal status as it runs.
    """
    if run_state.is_active():
        return {"launched": False, "reason": "a fetch is already running"}

    mode = "analysis" if analysis_only else "fetch"
    run_state.mark_launching(label, mode)

    argv = [sys.executable, "-m", "scripts.run_fetch", "--label", label]
    if analysis_only:
        argv.append("--analysis-only")
    else:
        if not discover:
            argv.append("--no-discover")
        if not respect_lock:
            argv.append("--no-lock")
        if not run_backup:
            argv.append("--no-backup")
    if subset:
        argv += ["--subset", ",".join(subset)]

    # Run with NO visible window but as an independent process that outlives this
    # (Streamlit) one. CREATE_NO_WINDOW hides the console window that python.exe (a
    # console program) would otherwise pop up — DETACHED_PROCESS would leave that
    # window because the venv launcher re-spawns a console child. Windows child
    # processes aren't killed when the parent exits, and the app is stopped via
    # SIGTERM (not a console Ctrl-C), so the fetch keeps running; CREATE_NEW_PROCESS_GROUP
    # additionally shields it from any console signals. Std streams are redirected
    # (no console to attach to); stdout/stderr go to a file for post-mortem if the
    # child dies before logging is set up.
    settings.FETCH_CONSOLE_LOG.parent.mkdir(parents=True, exist_ok=True)
    log_handle = open(settings.FETCH_CONSOLE_LOG, "a", encoding="utf-8")
    popen_kwargs: dict = {}
    if sys.platform == "win32":
        popen_kwargs["creationflags"] = (
            subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP
        )
    else:  # POSIX dev fallback (this app targets Windows)
        popen_kwargs["start_new_session"] = True
    try:
        proc = subprocess.Popen(
            argv,
            cwd=str(settings.BASE_DIR),
            stdin=subprocess.DEVNULL,
            stdout=log_handle,
            stderr=subprocess.STDOUT,
            close_fds=True,
            **popen_kwargs,
        )
    finally:
        log_handle.close()  # the child holds its own inherited copy

    return {"launched": True, "pid": proc.pid, "mode": mode, "label": label}
