"""Stop the server shortly after the last browser tab is closed.

Streamlit deliberately keeps the server running when a tab disconnects — it
supports reconnects (refresh) and multiple clients. For this local, single-user
app we want the opposite: closing the UI should stop the process, so it isn't left
running headless in the terminal.

A daemon watcher polls the runtime's active-session count. Once at least one
session has connected and the count then stays at zero for `grace` seconds, it
terminates the process. The `grace` window is what distinguishes a real close from
a refresh (which briefly drops to zero before the new session connects), and the
"has connected" latch prevents an early exit during startup before the browser
attaches.

Best-effort and defensive: it reaches into Streamlit's session manager (a
semi-private API), so any version incompatibility simply disables auto-shutdown —
the manual Quit button in the sidebar still works.
"""

from __future__ import annotations

import os
import signal
import threading
import time

_started = False


def _num_active() -> int | None:
    """Active (connected) browser sessions, or None if the runtime isn't reachable."""
    try:
        from streamlit.runtime import get_instance

        return get_instance()._session_mgr.num_active_sessions()
    except Exception:
        return None


def _watch(grace: float, poll: float) -> None:
    seen = False                 # latch: at least one session has connected
    gone_since: float | None = None
    while True:
        time.sleep(poll)
        n = _num_active()
        if n is None:
            continue             # runtime not up yet (or API changed) — keep waiting
        if n > 0:
            seen, gone_since = True, None
            continue
        if not seen:
            continue             # startup: browser hasn't attached yet, don't exit
        # Zero active sessions after having seen one — start/continue the countdown.
        if gone_since is None:
            gone_since = time.monotonic()
        elif time.monotonic() - gone_since >= grace:
            os.kill(os.getpid(), signal.SIGTERM)
            return


def enable_autoshutdown(grace: float = 8.0, poll: float = 1.5) -> None:
    """Start the tab-close watcher once per process (idempotent across reruns)."""
    global _started
    if _started:
        return
    _started = True
    threading.Thread(target=_watch, args=(grace, poll), daemon=True).start()
