"""
Charts page (ROADMAP Topic 6.2 chart actions → 6.3 normalized price chart).

Opened in its own browser tab from an Output run's Action menu, e.g.
    /charts?view=price&symbols=AAPL,MSFT,KO
The selected symbols + chart kind ride in the query string, so each Action is a
plain link that opens a fresh tab — the same mechanism as /output?run=<id>.

Implemented so far: view=price — adjusted close, every symbol indexed to 100 at the
window start, color-blind-safe palette (settings.CHART_COLORWAY), period buttons +
custom range, vertical gridlines, and line breaks for data gaps (no interpolation).
Line identification is the x-unified hover (no on-chart labels or legend). The symbol
selector is an Output-style multi-row-selectable list (selecting none = plot all).
Fundamentals and dividend chart views land here next.
"""

from __future__ import annotations

from datetime import date

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from config import settings
from core.database import Database

_GAP_DAYS = 7      # consecutive bars more than this many days apart → draw a line break
_CHART_HEIGHT = 600  # px; the symbol list's scroll box is capped to this so they align


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


def _with_gap_breaks(dates: pd.Series, values: pd.Series) -> tuple[list, list]:
    """Insert a NaN point wherever consecutive bars are >_GAP_DAYS apart.

    With connectgaps=False plotly renders the NaN as a break, so genuine data gaps
    (missing fetch, trading halt) show as a discontinuity instead of a straight-line
    interpolation. Normal weekend/holiday gaps stay under the threshold.
    """
    xs: list = []
    ys: list = []
    prev = None
    for d, v in zip(dates, values):
        if prev is not None and (d - prev).days > _GAP_DAYS:
            xs.append(prev + (d - prev) / 2)
            ys.append(float("nan"))
        xs.append(d)
        ys.append(v)
        prev = d
    return xs, ys


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

# -- layout: narrow symbol selector on the left, chart on the right; the period --
# -- controls render in their own row BELOW the chart (the container is created --
# -- here so it lands there, but its widgets are read before the figure is built). #
_left, _right = st.columns([1, 9], gap="medium")
_period_host = st.container()

# Symbol selector (left): an Output-style multi-row-selectable list (click,
# Shift-click range, Ctrl/Cmd-click add). Selecting nothing plots every symbol, so
# "all" is the zero-effort default. Height is capped to the chart so a long universe
# scrolls and a short one stays compact.
_left.caption("Symbols")
_sym_df = pd.DataFrame({"Symbol": _have})
_list_height = min(_CHART_HEIGHT, 38 + len(_have) * 35 + 3)
_sel = (_left.dataframe(
    _sym_df, hide_index=True, width="stretch", height=_list_height,
    on_select="rerun", selection_mode="multi-row", key="chartsym_sel",
) or {}).get("selection", {})
_checked = [_have[i] for i in _sel.get("rows", []) if i < len(_have)] or _have

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

# -- build the figure -------------------------------------------------------- #
_win = prices[(prices["date"] >= _start) & (prices["date"] <= _end)]
_colors = settings.CHART_COLORWAY
fig = go.Figure()
for _i, _sym in enumerate(_checked):
    _s = _win[_win["symbol"] == _sym]
    if _s.empty:
        continue
    _base = float(_s["adj_close"].iloc[0])
    if _base == 0:
        continue
    _color = _colors[_i % len(_colors)]
    _norm = _s["adj_close"] / _base * 100.0
    _xs, _ys = _with_gap_breaks(_s["date"], _norm)
    fig.add_trace(go.Scatter(
        x=_xs, y=_ys, mode="lines", name=_sym, connectgaps=False,
        line=dict(color=_color, width=1.6),
        hovertemplate=f"<b>{_sym}</b>  %{{y:.1f}}<extra></extra>",
    ))

# No on-chart symbol labels — the x-unified hover already names every line and shows
# its value at the cursor (works at any zoom level), so it serves as the legend.
fig.update_layout(
    colorway=list(settings.CHART_COLORWAY),
    hovermode="x unified",
    showlegend=False,
    height=_CHART_HEIGHT,
    margin=dict(l=50, r=30, t=20, b=40),
    # Vertical gridlines outline the time axis. Plotly's date auto-ticks choose the
    # granularity (years / months / weeks) for the visible span and re-pick it on
    # zoom/pan, so the lines stay readable instead of crowding at any zoom level.
    xaxis=dict(showgrid=True, gridcolor="rgba(128,128,128,0.18)"),
    yaxis=dict(title="Indexed (window start = 100)",
               showgrid=True, gridcolor="rgba(128,128,128,0.10)"),
)
_right.plotly_chart(fig, width="stretch")
_right.caption("Drag to zoom, double-click to reset. Hover to read each line's value "
               "(the symbol names appear there). Line breaks mark data gaps (no interpolation).")
