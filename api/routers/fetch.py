"""
Fetch Control: start, watch, stop, and the danger-zone operations.

A run is a DETACHED OS process, so this router never owns it — it launches one,
reads the cross-process state file, and asks it to stop via a flag the run polls
at safe batch boundaries. That is why the server restarting, or every tab
closing, cannot interrupt a fetch.

The SSE stream is the one thing the Streamlit page never had: it pushes state
changes and new log lines as they land, and a tab that attaches mid-run gets a
full snapshot immediately.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any, AsyncIterator

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from config import settings
from data_layer import cancel, launcher, run_state
from data_layer.orchestrator import report_fetch
from services import fetch_watch

router = APIRouter(prefix="/api/fetch")

POLL_SECONDS = 1.0


@router.get("/status")
def status() -> dict[str, Any]:
    return fetch_watch.snapshot()


@router.get("/stream")
async def stream() -> StreamingResponse:
    """Server-sent events: one `snapshot` on connect, then changes only.

    SSE rather than a WebSocket because EventSource reconnects on its own — a
    dropped socket would otherwise need hand-written retry logic. Reattaching is
    trivial because the run's state lives on disk, not in this process.
    """

    async def events() -> AsyncIterator[str]:
        lines, offset = fetch_watch.backlog()
        payload = {**fetch_watch.snapshot(), "lines": lines}
        yield f"data: {json.dumps(payload, default=str)}\n\n"

        last = fetch_watch.version()
        while True:
            await asyncio.sleep(POLL_SECONDS)
            current = fetch_watch.version()
            if current == last:
                # A comment frame keeps proxies and the browser from timing out.
                yield ": keep-alive\n\n"
                continue
            last = current
            new_lines, offset = fetch_watch.read_from(offset)
            payload = {**fetch_watch.snapshot(), "lines": new_lines}
            yield f"data: {json.dumps(payload, default=str)}\n\n"

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


class StartRequest(BaseModel):
    discover: bool = False
    subset: list[str] | None = None
    respect_lock: bool = True
    run_backup: bool = True
    analysis_only: bool = False
    label: str = "UI run"


@router.post("/start")
def start(req: StartRequest) -> dict[str, Any]:
    """Launch a detached run. The launcher re-checks that none is active, so two
    runs can never overlap even if the UI's gate is stale."""
    result = launcher.launch_detached_fetch(
        discover=req.discover,
        subset=req.subset or None,
        respect_lock=req.respect_lock,
        run_backup=req.run_backup,
        analysis_only=req.analysis_only,
        label=req.label,
    )
    if not result.get("launched"):
        raise HTTPException(status_code=409, detail=result.get("reason", "could not launch"))
    return result


class StopRequest(BaseModel):
    analyze_after: bool = True


@router.post("/stop")
def stop(req: StopRequest) -> dict[str, Any]:
    """Ask the run to stop at its next safe batch boundary.

    Cooperative on purpose: every completed batch is already committed, so a
    stopped run is resumable rather than wasted.
    """
    cancel.request_cancel(analyze_after=req.analyze_after)
    return {"stop_requested": True, "analyze_after": req.analyze_after}


@router.post("/clear")
def clear() -> dict[str, Any]:
    """Dismiss a finished run's state so the page returns to idle."""
    run_state.clear()
    cancel.clear()
    return {"cleared": True}


@router.get("/report")
def report(subset: str = "", respect_lock: bool = True) -> dict[str, Any]:
    """Dry run: what the next fetch WOULD do, per step, without fetching."""
    symbols = [s.strip().upper() for s in subset.split(",") if s.strip()] or None
    return {
        "report": report_fetch(subset=symbols, respect_lock=respect_lock),
        "lock_days": settings.FETCH_LOCK_DAYS,
        "abandonment_enabled": settings.FETCH_ABANDONMENT_ENABLED,
        "respect_lock": respect_lock,
    }


# --------------------------------------------------------------------------- #
# danger zone
# --------------------------------------------------------------------------- #
@router.get("/snapshots")
def snapshots() -> dict[str, Any]:
    from core.restore import list_snapshots

    return {"snapshots": list_snapshots()}


class RestoreRequest(BaseModel):
    stamp: str


@router.post("/restore")
def restore(req: RestoreRequest) -> dict[str, Any]:
    """Revert the live databases to a snapshot. The current ones are copied to
    `backups/pre_restore/` first, as a one-level undo."""
    if run_state.is_active():
        raise HTTPException(status_code=409, detail="a fetch is running")
    from core.restore import restore_snapshot

    return restore_snapshot(req.stamp)


class ResetRequest(BaseModel):
    """`confirm` must be the literal word, so a stray POST can't wipe the data."""

    confirm: str


@router.post("/reset")
def reset(req: ResetRequest) -> dict[str, Any]:
    if req.confirm != "RESET":
        raise HTTPException(status_code=400, detail='confirm must be "RESET"')
    if run_state.is_active():
        raise HTTPException(status_code=409, detail="a fetch is running")
    from core.reset import reset_all_data

    return reset_all_data()
