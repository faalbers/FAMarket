"""
Settings and the peak-detection calibration tuner.

`config/settings.py` is never written: Save persists only the CHANGED keys to
the gitignored `settings.local.json`, which is laid over the defaults at import.
Deleting that file resets everything.

Note the change applies to this process; a detached fetch re-reads the override
file when it starts, so a setting saved mid-run affects the NEXT run.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from config.settings_overrides import SettingsWriteError, update_settings
from services import calibration_data, settings_schema

router = APIRouter(prefix="/api")


@router.get("/settings")
def read_settings() -> dict[str, Any]:
    return {"sections": settings_schema.sections()}


class SaveSettingsRequest(BaseModel):
    """Only the paths the user actually changed."""

    changes: dict[str, Any]


@router.put("/settings")
def save_settings(req: SaveSettingsRequest) -> dict[str, Any]:
    if not req.changes:
        return {"saved": 0, "changed": []}
    collapsed = settings_schema.collapse(req.changes)
    try:
        update_settings(collapsed)
    except SettingsWriteError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"saved": len(collapsed), "changed": sorted(collapsed)}


@router.get("/calibration/samples")
def calibration_samples() -> dict[str, Any]:
    return {"samples": calibration_data.samples()}


@router.get("/calibration/signals")
def calibration_signals(symbol: str, prominence: float, distance: int) -> dict[str, Any]:
    return calibration_data.signals(symbol, prominence, distance)
