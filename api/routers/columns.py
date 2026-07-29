"""
Column metadata: which parameter columns a result can show, and how to label
any concrete analysis.db column.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query

from config import settings
from services import columns as C

router = APIRouter(prefix="/api/columns")


def _csv(raw: str) -> list[str]:
    return [p.strip() for p in raw.split(",") if p.strip()]


@router.get("/options")
def options(
    types: str = Query(default="", description="comma-separated screen_type keys"),
    extra: str = Query(default="", description="already-chosen columns to keep listed"),
) -> dict[str, Any]:
    return {"options": C.options(_csv(types), _csv(extra))}


@router.get("/describe")
def describe(cols: str = Query(default="")) -> dict[str, Any]:
    """Label / unit / hint key for each of `cols` — used for headers and the PDF."""
    return {"columns": [C.describe(col) for col in _csv(cols)]}


@router.get("/external-sites")
def external_sites() -> dict[str, str]:
    """URL templates for the Action menu's external links."""
    return dict(settings.EXTERNAL_SITES)
