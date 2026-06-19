"""Cooperative cancellation for a running fetch (Fetch Control "Stop Fetch" button).

The fetch runs as its OWN detached OS process (`data_layer/launcher.py` spawns
`scripts/run_fetch.py`), so a Stop click in the Streamlit app must reach a
DIFFERENT process. The cross-process signal is a tiny flag file
(`settings.FETCH_STOP_FILE`): the UI writes it on Stop, and the running fetch polls
for it at *safe boundaries* only — between fetchers in the orchestrator and between
batches in `BaseFetcher.run`. A stop therefore never interrupts a half-written
batch, and because `fetch_status` is written per batch, a cancelled run is fully
resumable (re-run to pick up where it left off).

An in-process `threading.Event` is also kept and OR-ed in, so a same-process caller
(e.g. a standalone `scripts.run_fetch` driven in the foreground) still works. The
orchestrator calls `clear()` at the start of every run, so a stale stop from a
previous run never leaks into the next one.

The flag is process-global / file-based (not per-session) — fine for this
single-user local app.
"""

from __future__ import annotations

import json
import threading

from config import settings

_cancel = threading.Event()
# Whether a cancelled run should still rebuild analysis.db from the data fetched
# so far. The stop request carries this intent (decided at the moment Stop is
# clicked, via the checkbox next to it) — written into the stop file so the
# detached run reads the same choice the UI made. Defaults to True.
_analyze_after_stop = True


def request_cancel(analyze_after: bool = True) -> None:
    """Ask the running fetch to stop at the next safe boundary.

    Writes the cross-process stop flag (read by the detached run) AND sets the
    in-process Event (for a same-process foreground run). `analyze_after` records
    whether the orchestrator should still rebuild analysis on the partial data once
    it unwinds (set from the Stop-time checkbox); it's persisted in the flag file so
    the other process sees the same choice.
    """
    global _analyze_after_stop
    _analyze_after_stop = analyze_after
    try:
        settings.FETCH_STOP_FILE.parent.mkdir(parents=True, exist_ok=True)
        settings.FETCH_STOP_FILE.write_text(
            json.dumps({"analyze_after": bool(analyze_after)}), encoding="utf-8"
        )
    except OSError:
        pass
    _cancel.set()


def clear() -> None:
    """Reset the flag (and the analyze-after intent) — called at the start of every run."""
    global _analyze_after_stop
    _analyze_after_stop = True
    try:
        settings.FETCH_STOP_FILE.unlink(missing_ok=True)
    except OSError:
        pass
    _cancel.clear()


def is_cancelled() -> bool:
    """True once a stop has been requested (this process's Event, or the flag file)."""
    return _cancel.is_set() or settings.FETCH_STOP_FILE.exists()


def analyze_after_stop() -> bool:
    """Whether a cancelled run should still rebuild analysis (set by `request_cancel`).

    Prefers the value persisted in the flag file (so a detached run honors the
    choice made in the UI process); falls back to the in-process global.
    """
    try:
        if settings.FETCH_STOP_FILE.exists():
            data = json.loads(settings.FETCH_STOP_FILE.read_text(encoding="utf-8"))
            return bool(data.get("analyze_after", True))
    except (OSError, ValueError):
        pass
    return _analyze_after_stop
