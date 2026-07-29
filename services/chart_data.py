"""
Series builders for the chart views.

Extracted from `ui/pages/charts.py` so the API can serve finished series and the
frontend stays a renderer. The maths is unchanged — same windows, same anchors,
same base-100 convention — because both front ends must agree on what a line
means.

Dates come out as `YYYY-MM-DD` strings, which is what Lightweight Charts wants
for daily series. Note that its time scale is index-based: consecutive points
are drawn side by side regardless of the real gap between them, so no
gap-breaking placeholder points are inserted here (the ECharts version needed
them because its axis is a true time axis).
"""

from __future__ import annotations

from datetime import date
from typing import Any, cast

import pandas as pd

from config import settings
from core.database import Database

Point = dict[str, Any]
Line = dict[str, Any]

PERIOD_YEARS = {"1Y": 1, "3Y": 3, "5Y": 5}


def window(period: str) -> tuple[pd.Timestamp, pd.Timestamp]:
    """(start, end) for a period preset; unknown presets fall back to 3Y."""
    # cast: pandas-stubs types the Timestamp constructor as Timestamp | NaT,
    # which it can't be for a real date.
    end = cast(pd.Timestamp, pd.Timestamp(date.today()))
    start = cast(pd.Timestamp, end - pd.DateOffset(years=PERIOD_YEARS.get(period, 3)))
    return start, end


# --------------------------------------------------------------------------- #
# loads
# --------------------------------------------------------------------------- #
def load_prices(symbols: list[str]) -> pd.DataFrame:
    """adj_close history for `symbols` -> (symbol, date, adj_close), sorted.

    A handful of symbols hits the (symbol, date) unique index, so this stays a
    small, fast read even against the full table.
    """
    if not symbols or not settings.OHLCV_DB.exists():
        return pd.DataFrame()
    with Database(settings.OHLCV_DB) as db:
        if not db.table_exists("ohlcv"):
            return pd.DataFrame()
        placeholders = ",".join("?" * len(symbols))
        frame = db.read("ohlcv", where=f"symbol IN ({placeholders})", params=symbols)
    if frame.empty:
        return frame

    frame = frame[["symbol", "date", "adj_close"]].copy()
    frame["date"] = pd.to_datetime(frame["date"], errors="coerce")
    frame["adj_close"] = pd.to_numeric(frame["adj_close"], errors="coerce")
    frame = frame[frame["date"].notna() & frame["adj_close"].notna()]
    return frame.sort_values(["symbol", "date"], kind="stable")


def symbol_groups(symbols: list[str]) -> tuple[dict[str, list[str]], dict[str, str], dict[str, str]]:
    """Group the charted symbols by sector -> industries.

    Returns (tree, sym2sector, sym2industry) where tree maps a sector to its
    `'Sector | Industry'` labels — the same label form `indices.db` uses.
    """
    empty: tuple[dict[str, list[str]], dict[str, str], dict[str, str]] = ({}, {}, {})
    if not symbols or not settings.ANALYSIS_DB.exists():
        return empty
    with Database(settings.ANALYSIS_DB) as db:
        if not db.table_exists("analysis"):
            return empty
        placeholders = ",".join("?" * len(symbols))
        frame = db.read("analysis", where=f"symbol IN ({placeholders})", params=symbols)
    if frame.empty or "sector" not in frame.columns:
        return empty

    tree: dict[str, set[str]] = {}
    sym2sector: dict[str, str] = {}
    sym2industry: dict[str, str] = {}
    wanted = [c for c in ("symbol", "sector", "industry") if c in frame.columns]
    for row in frame[wanted].to_dict(orient="records"):
        sector = row.get("sector")
        industry = row.get("industry")
        symbol = str(row.get("symbol", ""))
        if not symbol or not isinstance(sector, str) or not sector:
            continue
        sym2sector[symbol] = sector
        tree.setdefault(sector, set())
        if isinstance(industry, str) and industry:
            label = f"{sector} | {industry}"
            tree[sector].add(label)
            sym2industry[symbol] = label
    return {s: sorted(v) for s, v in sorted(tree.items())}, sym2sector, sym2industry


def index_series(kind: str, label: str) -> pd.Series:
    """One group's base-100 level series from indices.db, date-indexed."""
    if not settings.INDICES_DB.exists():
        return pd.Series(dtype=float)
    with Database(settings.INDICES_DB) as db:
        if not db.table_exists("sector_industry_index"):
            return pd.Series(dtype=float)
        frame = db.read(
            "sector_industry_index", where="kind = ? AND label = ?", params=[kind, label]
        )
    if frame.empty:
        return pd.Series(dtype=float)

    levels = pd.Series(pd.to_numeric(frame["level"], errors="coerce"))
    dates = pd.DatetimeIndex(pd.to_datetime(frame["date"], errors="coerce")).normalize()
    series = pd.Series(levels.to_numpy(), index=dates)
    series = series[series.notna()]
    return cast(pd.Series, series[~series.index.duplicated(keep="last")].sort_index())


# --------------------------------------------------------------------------- #
# series builders
# --------------------------------------------------------------------------- #
def _points(dates, values) -> list[Point]:
    return [
        {"time": pd.Timestamp(d).strftime("%Y-%m-%d"), "value": float(v)}
        for d, v in zip(dates, values)
        if pd.notna(v)
    ]


def _line(name: str, dates, values) -> Line:
    return {"name": name, "points": _points(dates, values)}


def normalized_lines(prices: pd.DataFrame, symbols: list[str], start, end) -> list[Line]:
    """Each symbol's adj_close indexed to 100 at the window start."""
    win = prices[(prices["date"] >= start) & (prices["date"] <= end)]
    out: list[Line] = []
    for symbol in symbols:
        rows = win[win["symbol"] == symbol]
        if rows.empty:
            continue
        base = float(rows["adj_close"].iloc[0])
        if base == 0:
            continue
        out.append(_line(symbol, rows["date"], rows["adj_close"] / base * 100.0))
    return out


def group_symbol_lines(
    prices: pd.DataFrame, group: list[str], index: pd.Series, start, end, relative: bool
) -> list[Line]:
    """In-group symbols over the period ∩ index window, rebased at the first shared date.

    relative=True  -> symbol_norm − index_norm + 100 (above 100 beats the group).
    relative=False -> symbol_norm alone.
    Both share the window and anchor, so toggling keeps the start points put.
    """
    if not group or index.empty:
        return []
    index = cast(pd.Series, index[(index.index >= start) & (index.index <= end)])
    if index.empty:
        return []

    win = prices[(prices["date"] >= start) & (prices["date"] <= end)]
    out: list[Line] = []
    for symbol in group:
        rows = win[win["symbol"] == symbol]
        if rows.empty:
            continue
        dates = pd.DatetimeIndex(pd.to_datetime(rows["date"])).normalize()
        series = pd.Series(rows["adj_close"].to_numpy(), index=dates)
        series = cast(pd.Series, series[~series.index.duplicated(keep="last")])
        common = pd.DatetimeIndex(series.index).intersection(index.index).sort_values()
        if len(common) < 2:
            continue
        symbol_base = float(series.loc[common[0]])
        index_base = float(index.loc[common[0]])
        if symbol_base == 0 or index_base == 0:
            continue
        symbol_norm = series.loc[common] / symbol_base * 100.0
        values = (
            symbol_norm - (index.loc[common] / index_base * 100.0) + 100.0
            if relative
            else symbol_norm
        )
        out.append(_line(symbol, common, values))
    return out


def index_line(index: pd.Series, start, end, label: str) -> list[Line]:
    """The group index itself, normalized to 100 at the first date in the window."""
    index = cast(pd.Series, index[(index.index >= start) & (index.index <= end)])
    if len(index) < 2:
        return []
    base = float(index.iloc[0])
    if base == 0:
        return []
    return [_line(label, index.index, index / base * 100.0)]


# --------------------------------------------------------------------------- #
# the price view, assembled
# --------------------------------------------------------------------------- #
def price_view(
    symbols: list[str], period: str, group: str | None = None, mode: str = "relative"
) -> dict[str, Any]:
    """Everything the price chart needs for one set of options.

    `group` is `"S::<sector>"` or `"I::<Sector | Industry>"`; without it the view
    is the plain normalized chart. `mode` is relative | symbols | index.
    """
    start, end = window(period)
    prices = load_prices(symbols)
    if prices.empty:
        return {"lines": [], "missing": symbols, "message": "No price history for these symbols."}

    present = set(prices["symbol"])
    have = [s for s in symbols if s in present]
    missing = [s for s in symbols if s not in present]

    tree, sym2sector, sym2industry = symbol_groups(have)
    base = {
        "missing": missing,
        "tree": tree,
        "baseline": 100.0,
        "period": period,
    }

    if not group:
        lines = normalized_lines(prices, have, start, end)
        return {
            **base,
            "lines": lines,
            "mode": None,
            "y_label": "Indexed (100)",
            "message": None if lines else "No data in the selected window.",
        }

    kind = "sector" if group.startswith("S::") else "industry"
    label = group[3:]
    mapping = sym2sector if kind == "sector" else sym2industry
    members = [s for s in have if mapping.get(s) == label]
    index = index_series(kind, label)

    if mode == "index":
        lines = index_line(index, start, end, label)
        y_label = "Indexed (100)"
    else:
        lines = group_symbol_lines(prices, members, index, start, end, mode == "relative")
        y_label = "Relative to index (100)" if mode == "relative" else "Indexed (100)"

    return {
        **base,
        "lines": lines,
        "mode": mode,
        "group": group,
        "group_label": label,
        "y_label": y_label,
        "message": None
        if lines
        else (
            f"Nothing to plot for {label}: no charted symbols in this group, or no "
            "overlapping dates between them and the group's index history."
        ),
    }
