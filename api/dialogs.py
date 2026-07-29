"""
Native file-dialog endpoint.

The child process is awaited with asyncio's subprocess API, so the event loop
keeps serving other requests (SSE streams, polling) while the dialog sits open.
Running it with blocking `subprocess.run()` would freeze every other endpoint
for as long as the user leaves the dialog open.

Only picks the path — each file type owns its own (de)serialisation, exactly as
`ui/file_io.py` does for the Streamlit UI.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

router = APIRouter()

_CHILD = Path(__file__).parent / "file_dialog_child.py"
_lock = asyncio.Lock()


class DialogRequest(BaseModel):
    mode: Literal["open", "save"] = "open"
    initial_dir: str = ""
    initial_file: str = ""
    ext: str = ""  # e.g. ".filt"
    title: str = "Select file"
    # Test hooks — headless verification without popping a real dialog.
    fake_path: str = ""
    delay_ms: int = 0


async def ask_path(req: DialogRequest) -> str | None:
    """Pop the dialog, return the chosen path (None if cancelled)."""
    if _lock.locked():
        raise HTTPException(status_code=409, detail="a dialog is already open")
    async with _lock:
        cmd = [sys.executable, str(_CHILD), req.mode]
        for flag, value in (
            ("--initial-dir", req.initial_dir),
            ("--initial-file", req.initial_file),
            ("--ext", req.ext),
            ("--title", req.title),
        ):
            if value:
                cmd += [flag, value]
        if req.fake_path:
            cmd += ["--fake-path", req.fake_path, "--delay-ms", str(req.delay_ms)]

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        out, err = await proc.communicate()
        if proc.returncode != 0:
            raise HTTPException(status_code=500, detail=err.decode(errors="replace")[-500:])
        return out.decode(errors="replace").strip() or None


@router.post("/api/dialog")
async def dialog(req: DialogRequest) -> dict[str, Any]:
    path = await ask_path(req)
    return {"path": path, "cancelled": path is None}
