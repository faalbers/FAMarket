"""
Sector and sub-industry index series.

`indices.db` holds base-100 daily levels written on FULL analysis runs only.
This module rebases them into the chosen window and, for the Industries view,
plots each industry relative to its parent sector. Extracted from
`ui/pages/sector_index.py` unchanged.
"""

from __future__ import annotations

from datetime import date
from typing import Any, cast

import pandas as pd

from config import settings
from core.database import Database

PERIOD_YEARS = {"1Y": 1, "3Y": 3, "5Y": 5}

Line = dict[str, Any]


def load_indices(kind: str) -> dict[str, pd.Series]:
    """Every base-100 level series of `kind` — label -> date-indexed Series.

    Industry labels are `"<Sector> | <Industry>"`, matching what the charts page
    uses to join a symbol to its group.
    """
    if not settings.INDICES_DB.exists():
        return {}
    with Database(settings.INDICES_DB) as db:
        if not db.table_exists("sector_industry_index"):
            return {}
        frame = db.read("sector_industry_index", where="kind = ?", params=[kind])
    if frame.empty:
        return {}

    frame = frame.copy()
    frame["date"] = pd.DatetimeIndex(pd.to_datetime(frame["date"], errors="coerce")).normalize()
    frame["level"] = pd.to_numeric(frame["level"], errors="coerce")
    frame = frame[frame["date"].notna() & frame["level"].notna()]

    out: dict[str, pd.Series] = {}
    for label, group in frame.groupby("label", sort=True):
        series = pd.Series(group["level"].to_numpy(), index=pd.DatetimeIndex(group["date"]))
        out[str(label)] = cast(pd.Series, series[~series.index.duplicated(keep="last")].sort_index())
    return out


def window(period: str) -> tuple[pd.Timestamp, pd.Timestamp]:
    """(start, end) for a preset; `Max` reaches back far enough to cover everything."""
    end = cast(pd.Timestamp, pd.Timestamp(date.today()))
    if period == "Max":
        return cast(pd.Timestamp, pd.Timestamp("1900-01-01")), end
    years = PERIOD_YEARS.get(period, 3)
    return cast(pd.Timestamp, end - pd.DateOffset(years=years)), end


def _points(series: pd.Series) -> list[dict[str, Any]]:
    return [
        {"time": pd.Timestamp(when).strftime("%Y-%m-%d"), "value": float(value)}
        for when, value in series.items()
        if pd.notna(value)
    ]


def rebased(name: str, series: pd.Series, start, end) -> Line | None:
    """Rebased to 100 at its first bar INSIDE the window — a series whose history
    starts mid-window bases there rather than being dropped."""
    win = cast(pd.Series, series[(series.index >= start) & (series.index <= end)])
    if len(win) < 2:
        return None
    base = float(win.iloc[0])
    if base == 0:
        return None
    return {"name": name, "points": _points(win / base * 100.0)}


def relative(name: str, industry: pd.Series, sector: pd.Series, start, end) -> Line | None:
    """An industry against its parent sector: `industry_norm − sector_norm + 100`.

    Both rebased at their first SHARED date, so this is a literal difference — a
    big underperformer can dip below zero.
    """
    industry = cast(pd.Series, industry[(industry.index >= start) & (industry.index <= end)])
    sector = cast(pd.Series, sector[(sector.index >= start) & (sector.index <= end)])
    common = pd.DatetimeIndex(industry.index).intersection(sector.index).sort_values()
    if len(common) < 2:
        return None
    industry_base = float(industry.loc[common[0]])
    sector_base = float(sector.loc[common[0]])
    if industry_base == 0 or sector_base == 0:
        return None
    values = (
        industry.loc[common] / industry_base * 100.0
        - sector.loc[common] / sector_base * 100.0
        + 100.0
    )
    return {"name": name, "points": _points(values)}


def _end_value(line: Line) -> float:
    """A line's value at the right edge — used to order the strongest first."""
    for point in reversed(line["points"]):
        if point["value"] is not None:
            return float(point["value"])
    return float("-inf")


def series_view(view: str, period: str, sector: str = "", mode: str = "absolute") -> dict[str, Any]:
    """Sector lines, or one sector's industries (absolute or relative to it)."""
    start, end = window(period)

    sectors = load_indices("sector")
    if not sectors:
        return {
            "lines": [],
            "sectors": [],
            "message": "No index data — indices are built on FULL analysis runs only.",
        }

    def sector_lines() -> list[Line]:
        built = [rebased(name, series, start, end) for name, series in sectors.items()]
        found = [line for line in built if line is not None]
        found.sort(key=_end_value, reverse=True)
        return found

    if view == "sectors":
        lines = sector_lines()
        return {
            "lines": lines,
            # Sectors ranked strongest-first, so the picker leads with the leaders.
            "sectors": [ln["name"] for ln in lines],
            "y_label": "Indexed (100)",
            "baseline": 100.0,
            "message": None if lines else "No index history in that window.",
        }

    ranked = [line["name"] for line in sector_lines()]
    chosen = sector if sector in sectors else (ranked[0] if ranked else "")
    if not chosen:
        return {"lines": [], "sectors": ranked, "message": "No sector index available."}

    industries = load_indices("industry")
    members = {
        label: series
        for label, series in industries.items()
        if label.split(" | ", 1)[0] == chosen
    }
    if not members:
        return {
            "lines": [],
            "sectors": ranked,
            "sector": chosen,
            "message": f"No industry indices under {chosen}.",
        }

    parent = sectors[chosen]
    lines: list[Line] = []
    for label, series in members.items():
        short = label.split(" | ", 1)[1] if " | " in label else label
        line = (
            relative(short, series, parent, start, end)
            if mode == "relative"
            else rebased(short, series, start, end)
        )
        if line:
            lines.append(line)
    lines.sort(key=_end_value, reverse=True)

    return {
        "lines": lines,
        "sectors": ranked,
        "sector": chosen,
        "y_label": "Relative to sector (100)" if mode == "relative" else "Indexed (100)",
        "baseline": 100.0,
        "message": None if lines else "No overlapping index history in that window.",
    }
