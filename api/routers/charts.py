"""
Chart data. The server computes every series; the client only renders.

The maths leans on `metrics`, `scoring_rules` and full-universe reads, so it
belongs next to Python — see `services/chart_data.py`.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from api.dialogs import DialogRequest, ask_path
from config import settings
from services import chart_data, filter_fail, fundamentals_data, scores_data, valuation_data

router = APIRouter(prefix="/api/charts")


def _symbols(raw: str) -> list[str]:
    return [s.strip().upper() for s in raw.split(",") if s.strip()]


def _csv(raw: str) -> list[str]:
    return [s.strip() for s in raw.split(",") if s.strip()]


@router.get("/price")
def price(
    symbols: str = Query(default=""),
    period: str = Query(default="3Y"),
    group: str = Query(default=""),
    mode: str = Query(default="relative"),
) -> dict[str, Any]:
    """Normalized price lines, or the relative-strength view when `group` is set."""
    return chart_data.price_view(_symbols(symbols), period, group or None, mode)


# --------------------------------------------------------------------------- #
# reported-period views
# --------------------------------------------------------------------------- #
@router.get("/fundamentals/options")
def fundamentals_options() -> dict[str, Any]:
    """Parameters the bar and growth-line views can plot."""
    return {"options": fundamentals_data.options()}


@router.get("/fundamentals/bar")
def fundamentals_bar(
    symbol: str = Query(...),
    param: str = Query(...),
    freq: str = Query(default="annual"),
) -> dict[str, Any]:
    return fundamentals_data.bar_view(symbol.strip().upper(), param, freq)


@router.get("/fundamentals/line")
def fundamentals_line(
    symbols: str = Query(default=""),
    param: str = Query(...),
    freq: str = Query(default="annual"),
    normalized: bool = Query(default=False),
) -> dict[str, Any]:
    return fundamentals_data.line_view(_symbols(symbols), param, freq, normalized)


@router.get("/dividends")
def dividends(
    symbols: str = Query(default=""),
    freq: str = Query(default="annual"),
    normalized: bool = Query(default=False),
) -> dict[str, Any]:
    return fundamentals_data.dividend_view(_symbols(symbols), freq, normalized)


# --------------------------------------------------------------------------- #
# score views
# --------------------------------------------------------------------------- #
@router.get("/radar")
def radar(symbols: str = Query(default="")) -> dict[str, Any]:
    return scores_data.radar_view(_symbols(symbols))


@router.get("/heatmap/options")
def heatmap_options(kind: str = Query(default="metrics")) -> dict[str, Any]:
    return {"options": scores_data.heatmap_options(kind)}


@router.get("/heatmap")
def heatmap(
    symbols: str = Query(default=""),
    cols: str = Query(default=""),
    kind: str = Query(default="metrics"),
) -> dict[str, Any]:
    return scores_data.heatmap_view(_symbols(symbols), _csv(cols), kind)


# --------------------------------------------------------------------------- #
# valuation range (football field)
# --------------------------------------------------------------------------- #
@router.get("/valuation-range")
def valuation_range(symbols: str = Query(default="")) -> dict[str, Any]:
    return valuation_data.football_field_view(_symbols(symbols))


# --------------------------------------------------------------------------- #
# filter-fail diagnostic
# --------------------------------------------------------------------------- #
@router.get("/filters")
def saved_filters() -> dict[str, Any]:
    return {"filters": filter_fail.list_filters()}


class FilterFailRequest(BaseModel):
    symbols: list[str]
    """Either an existing path (from /charts/filters) or blank to pop a dialog."""
    path: str = ""
    fake_path: str = ""


@router.post("/filter-fail")
async def filter_fail_report(req: FilterFailRequest) -> dict[str, Any]:
    path = req.path
    if not path:
        chosen = await ask_path(
            DialogRequest(
                mode="open",
                initial_dir=str(settings.FILTERS_DIR),
                ext=".filt",
                title="Select a filter",
                fake_path=req.fake_path,
            )
        )
        if chosen is None:
            return {"cancelled": True}
        path = chosen

    if not req.symbols:
        raise HTTPException(status_code=400, detail="no symbols given")
    return {
        "cancelled": False,
        "path": path,
        "report": filter_fail.build_report([s.upper() for s in req.symbols], path),
    }
