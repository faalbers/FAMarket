"""
Charts page (ROADMAP Topic 6.2 chart actions → 6.3 normalized price chart).

Opened in its own browser tab from an Output run's Action menu, e.g.
    /charts?view=price&symbols=AAPL,MSFT,KO
The selected symbols + chart kind ride in the query string, so each Action is a
plain link that opens a fresh tab — the same mechanism as /output?run=<id>.

EXPERIMENT (branch experiment/echarts): the price chart is rendered with Apache
ECharts (streamlit-echarts) instead of Plotly — dark theme, bright color-blind-safe
palette, axis-trigger tooltip (unified hover: every symbol named + colored + valued),
clickable legend, mouse-wheel zoom. Baseline to return to: tag plotly-charts-baseline.

view=price: adjusted close, every symbol indexed to 100 at the window start, period
buttons + custom range, gridlines, line breaks for data gaps (no interpolation). The
symbol selector is an Output-style multi-row-selectable list (selecting none = plot
all). Fundamentals and dividend chart views land here next.
"""

from __future__ import annotations

from datetime import date

import pandas as pd
import streamlit as st
from streamlit_echarts import st_echarts

from config import settings
from core.database import Database

_GAP_DAYS = 7      # consecutive bars more than this many days apart → draw a line break
_CHART_HEIGHT = 600  # px; the symbol list's scroll box is capped to this so they align

# EXPERIMENT (branch experiment/echarts): dark theme + a bright, color-blind-safe line
# palette (Paul Tol's "vibrant" scheme, brightest-first) — higher contrast on a dark
# background than the Okabe-Ito set in settings.CHART_COLORWAY. Promote to settings if kept.
_DARK_BG = "#0e1117"     # matches Streamlit's default dark theme background
_DARK_TEXT = "#e6e6e6"
_GRID_LINE = "rgba(255,255,255,0.14)"  # subtle gridlines on the dark background
_COLORWAY = ("#33BBEE", "#EE7733", "#EE3377", "#009988", "#0077BB", "#CC3311", "#BBBBBB")


@st.cache_data(show_spinner=False)
def _load_prices(symbols: tuple[str, ...], _mtime: float) -> pd.DataFrame:
    """adj_close history for `symbols` from ohlcv.db → (symbol, date, adj_close).

    `_mtime` (the db file's mtime) is part of the cache key so a fresh fetch
    invalidates the cache; it's otherwise unused. A handful of symbols hits the
    (symbol, date) unique index, so this is a small, fast read even on the full table.
    """
    if not settings.OHLCV_DB.exists():
        return pd.DataFrame()
    with Database(settings.OHLCV_DB) as db:
        if not db.table_exists("ohlcv"):
            return pd.DataFrame()
        ph = ",".join("?" * len(symbols))
        df = db.read("ohlcv", where=f"symbol IN ({ph})", params=list(symbols))
    if df.empty:
        return df
    df = df[["symbol", "date", "adj_close"]].copy()
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    df["adj_close"] = pd.to_numeric(df["adj_close"], errors="coerce")
    return (df.dropna(subset=["date", "adj_close"])
              .sort_values(["symbol", "date"], kind="stable"))


def _echarts_points(dates: pd.Series, values: pd.Series) -> list[list]:
    """Build ECharts [date, value] points, inserting a null wherever consecutive
    bars are >_GAP_DAYS apart.

    With series `connectNulls=False`, ECharts renders the null as a break, so genuine
    data gaps (missing fetch, trading halt) show as a discontinuity instead of a
    straight-line interpolation. Normal weekend/holiday gaps stay under the threshold.
    """
    pts: list[list] = []
    prev = None
    for d, v in zip(dates, values):
        if prev is not None and (d - prev).days > _GAP_DAYS:
            pts.append([(prev + (d - prev) / 2).strftime("%Y-%m-%d"), None])
        pts.append([d.strftime("%Y-%m-%d"), round(float(v), 2)])
        prev = d
    return pts


# --------------------------------------------------------------------------- #
# page
# --------------------------------------------------------------------------- #
st.title("Charts")

_raw = st.query_params.get("symbols", "")
symbols = [s.strip().upper() for s in _raw.split(",") if s.strip()]
view = st.query_params.get("view", "price")

if not symbols:
    st.info("Open this page from an **Output** run: select rows, then "
            "**Action → Normalized price chart**. It opens here in its own tab.")
    st.stop()

if view != "price":
    st.info(f"Chart view '{view}' isn't built yet — only the normalized price "
            "chart is available so far.")
    st.stop()

_mtime = settings.OHLCV_DB.stat().st_mtime if settings.OHLCV_DB.exists() else 0.0
prices = _load_prices(tuple(symbols), _mtime)
if prices.empty:
    st.warning("No price history found for the selected symbols.")
    st.stop()

_present = set(prices["symbol"])
_have = [s for s in symbols if s in _present]
_missing = [s for s in symbols if s not in _present]
if _missing:
    st.caption("No price data for: " + ", ".join(_missing))

st.caption("Normalized adjusted close — every line indexed to 100 at the window start.")

# -- layout: full-width chart, period controls in their own row below it -------- #
# The ECharts legend (click a name to show/hide a line) replaces a separate symbol
# selector, so every symbol with data is plotted and toggled from the legend. The
# containers are created top→bottom (chart above, period below); period widgets are
# read before the chart is built, but render into their own container underneath.
_chart_host = st.container()
_period_host = st.container()
_checked = _have

# Period control (below the chart): "Period:" label + options on one row, with the
# custom date inputs appearing inline only when Custom is picked.
_today = pd.Timestamp(date.today())
with _period_host:
    _pr = st.columns([0.5, 5, 1.5, 1.5], vertical_alignment="center")
    _pr[0].markdown("**Period:**")
    _period = _pr[1].radio("Period", ["1Y", "3Y", "5Y", "Custom"], index=2,
                           horizontal=True, label_visibility="collapsed")
    if _period == "Custom":
        _start = pd.Timestamp(_pr[2].date_input(
            "Start", value=(_today - pd.DateOffset(years=3)).date(),
            label_visibility="collapsed"))
        _end = pd.Timestamp(_pr[3].date_input(
            "End", value=_today.date(), label_visibility="collapsed"))
    else:
        _years = {"1Y": 1, "3Y": 3, "5Y": 5}[_period]
        _start, _end = _today - pd.DateOffset(years=_years), _today
if _end < _start:
    st.warning("End date is before the start date.")
    st.stop()

# -- build the chart (EXPERIMENT: Apache ECharts via streamlit-echarts) ------- #
# Same inputs as the Plotly version (data reader, selector, period window); only the
# render differs. tooltip trigger="axis" gives the unified hover (every symbol named,
# colored, with its value at the cursor); the legend is a clickable name+color key.
_win = prices[(prices["date"] >= _start) & (prices["date"] <= _end)]
_series_opt: list[dict] = []
for _sym in _checked:
    _s = _win[_win["symbol"] == _sym]
    if _s.empty:
        continue
    _base = float(_s["adj_close"].iloc[0])
    if _base == 0:
        continue
    _norm = _s["adj_close"] / _base * 100.0
    _series_opt.append({
        "name": _sym,
        "type": "line",
        "showSymbol": False,
        "connectNulls": False,
        "lineStyle": {"width": 2},
        "emphasis": {"focus": "series"},
        "data": _echarts_points(_s["date"], _norm),
    })

if not _series_opt:
    _chart_host.info("No data in the selected window.")
    st.stop()

_options = {
    "backgroundColor": _DARK_BG,
    "color": list(_COLORWAY),
    "textStyle": {"color": _DARK_TEXT},
    "tooltip": {
        "trigger": "axis", "order": "valueDesc",
        "backgroundColor": "rgba(15,18,25,0.92)",
        "borderColor": "rgba(255,255,255,0.20)",
        "textStyle": {"color": _DARK_TEXT},
    },
    "legend": {
        "type": "scroll", "top": 4,
        "data": [s["name"] for s in _series_opt],
        "textStyle": {"color": _DARK_TEXT},
        "inactiveColor": "rgba(255,255,255,0.35)",
        "pageTextStyle": {"color": _DARK_TEXT},
    },
    # Vertical + horizontal gridlines; ECharts time axis picks the tick granularity
    # (years / months / weeks) for the visible span and re-picks it on zoom.
    "grid": {"left": 8, "right": 18, "top": 44, "bottom": 28, "containLabel": True},
    "xAxis": {
        "type": "time",
        "axisLine": {"lineStyle": {"color": "rgba(255,255,255,0.35)"}},
        "axisLabel": {"color": _DARK_TEXT},
        "splitLine": {"show": True, "lineStyle": {"color": _GRID_LINE}},
    },
    "yAxis": {
        "type": "value", "name": "Indexed (100)", "scale": True,
        "nameTextStyle": {"color": _DARK_TEXT},
        "axisLabel": {"color": _DARK_TEXT},
        "splitLine": {"show": True, "lineStyle": {"color": _GRID_LINE}},
    },
    # Mouse-wheel zoom + drag-pan inside the plot (no extra slider bar).
    "dataZoom": [{"type": "inside"}],
    "series": _series_opt,
}
with _chart_host:
    st_echarts(options=_options, height=f"{_CHART_HEIGHT}px", key="echarts_price")
    st.caption("Apache ECharts (dark theme). Click a legend name to show/hide that line; "
               "hover shows every symbol's value at the cursor. Mouse-wheel to zoom, drag "
               "to pan. Line breaks mark data gaps.")
