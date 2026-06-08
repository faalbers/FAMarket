"""Cooperative cancellation for a running fetch (Fetch Control "Stop Fetch" button).

A fetch runs on a background worker thread (Streamlit Fetch Control) or in the
foreground (`scripts.run_fetch`). There is no preemptive kill — instead a single
process-wide flag is polled at *safe boundaries* only: between fetchers in the
orchestrator and between batches in `BaseFetcher.run`. A stop therefore never
interrupts a half-written batch, and because `fetch_status` is written per batch,
a cancelled run is fully resumable (re-run to pick up where it left off).

The flag is process-global (not per-session) — fine for this single-user local
app. The orchestrator calls `clear()` at the start of every run, so a stale stop
from a previous run never leaks into the next one.
"""

from __future__ import annotations

import threading

_cancel = threading.Event()


def request_cancel() -> None:
    """Ask the running fetch to stop at the next safe boundary."""
    _cancel.set()


def clear() -> None:
    """Reset the flag — called at the start of every run."""
    _cancel.clear()


def is_cancelled() -> bool:
    """True once a stop has been requested and not yet cleared."""
    return _cancel.is_set()
