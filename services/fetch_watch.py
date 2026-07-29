"""
Watches a detached fetch run so the UI can follow it live.

A run is its own OS process writing `state/fetch_run.json` and appending to
`logs/famarket.log`. Nothing pushes to us, so this polls both: the state file by
mtime, the log by byte offset (a tail, never a full re-read). Callers get a
snapshot dict plus whatever log lines are new.

Progress granularity is capped by what the run process actually writes — this
relays state transitions and log lines, it does not invent per-batch counts.
"""

from __future__ import annotations

from typing import Any

from config import settings
from data_layer import cancel, run_state

# Lines kept for a tab that attaches mid-run, so it has context immediately.
BACKLOG_LINES = 200


def snapshot() -> dict[str, Any]:
    """Everything the Fetch Control header needs, in one read."""
    state = run_state.read()
    return {
        "active": run_state.is_active(),
        "ended_unexpectedly": run_state.ended_unexpectedly(),
        "stop_requested": cancel.is_cancelled(),
        "analyze_after_stop": cancel.analyze_after_stop(),
        "state": state,
    }


def log_size() -> int:
    """Current size of the run log, or 0 when it doesn't exist yet."""
    path = settings.LOG_FILE
    try:
        return path.stat().st_size if path.exists() else 0
    except OSError:
        return 0


def read_from(offset: int) -> tuple[list[str], int]:
    """Log lines after `offset`, plus the new offset.

    A rolled log (size < offset) restarts the tail from the beginning rather
    than returning nothing.
    """
    path = settings.LOG_FILE
    if not path.exists():
        return [], 0
    try:
        size = path.stat().st_size
        if size < offset:  # the log rolled between reads
            offset = 0
        if size == offset:
            return [], offset
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            handle.seek(offset)
            text = handle.read()
            return [line for line in text.splitlines() if line.strip()], handle.tell()
    except OSError:
        return [], offset


def backlog() -> tuple[list[str], int]:
    """The last BACKLOG_LINES of the log plus the offset to continue from."""
    lines, offset = read_from(0)
    return lines[-BACKLOG_LINES:], offset


def _mtime(path) -> float:
    try:
        return path.stat().st_mtime if path.exists() else 0.0
    except OSError:
        return 0.0


def version() -> tuple[float, float, int]:
    """Cheap change key; the stream only pushes when this moves.

    The stop flag is watched as well as the run state: requesting a stop changes
    neither the state file nor the log (the run only reacts at its next batch
    boundary), so without it the UI would sit on a stale "Running" until
    something else happened to move.
    """
    return _mtime(settings.FETCH_RUN_STATE_FILE), _mtime(settings.FETCH_STOP_FILE), log_size()
