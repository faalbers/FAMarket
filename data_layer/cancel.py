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

This module also tracks the in-flight fetch *worker thread* and exposes
`stop_for_shutdown()` — used when the browser tab is closed (see
`core.autoshutdown`) to stop a running fetch gracefully (finish the current batch,
skip analysis) before the process exits, so the databases are left consistent.
"""

from __future__ import annotations

import threading

_cancel = threading.Event()
# The thread currently running a fetch (registered by the Fetch Control worker), so
# app shutdown can wait for it to unwind. A dead/None thread means "no fetch running".
_worker: threading.Thread | None = None
# Whether a cancelled run should still rebuild analysis.db from the data fetched
# so far. The stop request carries this intent (decided at the moment Stop is
# clicked, via the checkbox next to it), so the orchestrator reads it here rather
# than taking it as a run-start parameter. Defaults to True.
_analyze_after_stop = True


def request_cancel(analyze_after: bool = True) -> None:
    """Ask the running fetch to stop at the next safe boundary.

    `analyze_after` records whether the orchestrator should still run the analysis
    rebuild on the partial data once it unwinds (set from the Stop-time checkbox).
    """
    global _analyze_after_stop
    _analyze_after_stop = analyze_after
    _cancel.set()


def clear() -> None:
    """Reset the flag (and the analyze-after intent) — called at the start of every run."""
    global _analyze_after_stop
    _analyze_after_stop = True
    _cancel.clear()


def is_cancelled() -> bool:
    """True once a stop has been requested and not yet cleared."""
    return _cancel.is_set()


def analyze_after_stop() -> bool:
    """Whether a cancelled run should still rebuild analysis (set by `request_cancel`)."""
    return _analyze_after_stop


# --------------------------------------------------------------------------- #
# In-flight worker tracking + graceful shutdown (tab close)
# --------------------------------------------------------------------------- #
def set_worker(thread: threading.Thread) -> None:
    """Register the thread running the current fetch (called by the Fetch worker)."""
    global _worker
    _worker = thread


def active_worker() -> threading.Thread | None:
    """The in-flight fetch worker thread if one is still running, else None."""
    return _worker if (_worker is not None and _worker.is_alive()) else None


def _cli(msg: str) -> None:
    """Write a shutdown notice to the terminal (stdout), NOT the run log."""
    print(f"[FAMarket] {msg}", flush=True)


def stop_for_shutdown(announce=None, max_wait: float = 180.0) -> None:
    """Stop an in-flight fetch before the app process exits (browser tab closed).

    `announce(msg)` is the output sink — defaults to a CLI printer so the notices go
    to the terminal, not the run log (the fetch itself keeps writing the log). If a
    fetch is running, requests a stop WITHOUT post-stop analysis and waits for the
    worker to unwind at the next batch boundary, leaving the databases consistent;
    if none is running, just reports the shutdown. Safe to call from any thread.
    """
    announce = announce or _cli
    worker = active_worker()
    if worker is None:
        announce("Shutting down (browser tab closed) — no fetch running.")
        return
    announce("Browser tab closed while a fetch is running.")
    announce(
        "Requesting graceful stop — finishing the current batch, skipping analysis — "
        "so the databases are left consistent…"
    )
    request_cancel(analyze_after=False)
    worker.join(timeout=max_wait)
    if worker.is_alive():
        announce(
            f"Fetch still finishing after {max_wait:.0f}s — exiting anyway. Per-batch "
            "writes are transactional, so the run stays resumable (re-run to continue)."
        )
    else:
        announce("Fetch unwound cleanly. Shutting down.")
