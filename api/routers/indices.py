"""Sector / sub-industry index series."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query

from services import indices_data

router = APIRouter(prefix="/api/indices")


@router.get("/series")
def series(
    view: str = Query(default="sectors"),
    period: str = Query(default="3Y"),
    sector: str = Query(default=""),
    mode: str = Query(default="absolute"),
) -> dict[str, Any]:
    return indices_data.series_view(view, period, sector, mode)
