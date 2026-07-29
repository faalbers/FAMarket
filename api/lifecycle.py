"""
"Last tab closed" detection via a presence WebSocket.

Every open tab holds one WebSocket to /api/presence. When the count drops to
zero a grace timer starts (a reload briefly drops to zero); if no tab
reconnects before it fires, the shutdown callback runs. The callback is set by
the launcher (`scripts/serve_ui.py`) only — under a plain `uvicorn` dev run
nothing ever shuts down.
"""

from __future__ import annotations

import asyncio
from typing import Any, Callable

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

router = APIRouter()

GRACE_SECONDS = 3.0

_count = 0
_pending: asyncio.Task[None] | None = None
_shutdown: Callable[[], None] | None = None


def set_shutdown(callback: Callable[[], None]) -> None:
    """Arm tab-close shutdown; called by the launcher only."""
    global _shutdown
    _shutdown = callback


async def _grace_then_exit() -> None:
    await asyncio.sleep(GRACE_SECONDS)
    if _count == 0 and _shutdown is not None:
        _shutdown()


@router.websocket("/api/presence")
async def presence(sock: WebSocket) -> None:
    global _count, _pending
    await sock.accept()
    _count += 1
    if _pending is not None:
        _pending.cancel()
        _pending = None
    try:
        while True:
            await sock.receive_text()  # tabs never send; this just awaits the close
    except WebSocketDisconnect:
        pass
    finally:
        _count -= 1
        if _count == 0 and _shutdown is not None:
            _pending = asyncio.get_running_loop().create_task(_grace_then_exit())


@router.get("/api/presence/count")
async def count() -> dict[str, Any]:
    return {"tabs": _count, "armed": _shutdown is not None}
