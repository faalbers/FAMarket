"""
Cross-page reference data: health, the parameter-hint registry and the
analysis snapshot header.

`config/param_hints.py` stays the ONE canonical hint registry (CLAUDE.md) — the
frontend renders whatever it serves and never hardcodes a description.
"""

from __future__ import annotations

from typing import Any

import pandas as pd
from fastapi import APIRouter

from config import settings
from config.param_hints import PARAM_HINTS
from core.database import Database

router = APIRouter(prefix="/api")

ANALYSIS_META = "analysis_meta"


@router.get("/health")
def health() -> dict[str, Any]:
    dbs = sorted(p.name for p in settings.DB_DIR.glob("*.db"))
    return {"ok": True, "databases": dbs, "params": len(PARAM_HINTS)}


@router.get("/hints")
def hints() -> dict[str, dict[str, Any]]:
    """The whole registry — small (~100 entries), fetched once and cached."""
    return PARAM_HINTS


@router.get("/meta/analysis")
def analysis_meta() -> dict[str, Any]:
    """Single `analysis_meta` row: when analysis last ran and over what."""
    db = Database(settings.ANALYSIS_DB)
    try:
        frame = db.read(ANALYSIS_META)
    except Exception:
        return {"available": False}
    if frame is None or frame.empty:
        return {"available": False}

    row: dict[str, Any] = frame.iloc[0].to_dict()
    return {"available": True, **{k: (None if pd.isna(v) else v) for k, v in row.items()}}
