"""
Charts page (ROADMAP Topic 6.2 chart actions → 6.3 normalized price chart).

Opened in its own browser tab from an Output run's Action menu, e.g.
    /charts?view=price&symbols=AAPL,MSFT,KO
The selected symbols + chart kind ride in the query string, so each Action is a
plain link that opens a fresh tab — the same mechanism as /output?run=<id>.

The price chart is rendered with Apache ECharts (streamlit-echarts): dark theme, a
bright color-blind-safe palette, an axis-trigger tooltip (unified hover — every symbol
named + colored + valued at the cursor), and a clickable scroll legend that toggles
lines (so no separate symbol selector is needed).

view=price: adjusted close, every symbol indexed to 100 at the window start. The Period
presets (1Y/3Y/5Y) set the loaded data window; the ECharts dataZoom slider + wheel/drag
pick sub-ranges (native arbitrary-range selector); the toolbox restore icon resets to
the full view. Line breaks mark data gaps (no interpolation). Fundamentals and dividend
chart views land here next.
"""

from __future__ import annotations

from datetime import date

import pandas as pd
import streamlit as st
from streamlit_echarts import st_echarts

from analysis_layer import metrics
from config import settings
from core.database import Database
from ui.chart_theme import (
    COLORWAY as _COLORWAY,
    DARK_BG as _DARK_BG,
    DARK_TEXT as _DARK_TEXT,
    GRID_LINE as _GRID_LINE,
    echarts_points as _echarts_points,
)

_CHART_HEIGHT = 600  # px; the symbol list's scroll box is capped to this so they align

# Fundamentals-bar view (ROADMAP 6.2): one symbol × one parameter, plotted over its
# reported periods. Ratios reuse metrics.RATIO_PERIOD_METRICS — the SAME formula
# functions the analysis snapshot uses — so a ratio is defined once, never twice.
# These are display labels only (a UI concern); the formulas live in metrics.py.
_RATIO_LABELS = {
    "gross_margin": "Gross margin", "operating_margin": "Operating margin",
    "net_margin": "Net margin", "fcf_margin": "FCF margin", "roe": "ROE", "roa": "ROA",
    "debt_to_equity": "Debt / equity", "debt_to_ebitda": "Debt / EBITDA",
    "current_ratio": "Current ratio", "interest_coverage": "Interest coverage",
}
_FREQ = {"Annual": "annual", "Quarterly": "quarterly"}


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


def _true_annual(df: pd.DataFrame) -> pd.DataFrame:
    """Keep only genuine fiscal-year-end rows.

    The EDGAR backfill tags some quarter-end frames as freq='annual' (sparse,
    off-cycle — e.g. Mar/Jun/Sep for a December filer), so a raw annual read shows
    several rows per year, most empty. The real annual rows share the fiscal
    year-end month and carry the full-year figures. Detect that month from the most
    recent revenue-bearing row (fallback: the most recent row), keep only that
    month, then one row per year (the most complete). `_periods` sidesteps this for
    analysis by dropping per-field NaNs; the chart needs one bar per real period.
    """
    if df.empty:
        return df
    pe = df["period_end"]
    anchor = df
    if "total_revenue" in df.columns:
        rev = df[pd.to_numeric(df["total_revenue"], errors="coerce").notna()]
        if not rev.empty:
            anchor = rev
    fmonth = int(pe.loc[anchor.index[-1]].month)  # most recent anchor row's month
    kept = df[pe.dt.month == fmonth].copy()
    if kept.empty:  # heuristic missed — better to show everything than a blank chart
        return df
    kept["_yr"] = kept["period_end"].dt.year
    kept["_nn"] = kept.notna().sum(axis=1)  # completeness, to break source dupes
    kept = (kept.sort_values(["_yr", "_nn", "period_end"])
                .drop_duplicates("_yr", keep="last")
                .drop(columns=["_yr", "_nn"]))
    return kept.sort_values("period_end")


@st.cache_data(show_spinner=False)
def _load_financials(symbol: str, freq: str, _mtime: float) -> pd.DataFrame:
    """One symbol's reported periods for `freq` from financials.db, sorted oldest→newest.

    Annual rows are de-duplicated to one genuine fiscal-year-end per year (see
    _true_annual); quarterly rows are already one per period.
    """
    if not settings.FINANCIALS_DB.exists():
        return pd.DataFrame()
    with Database(settings.FINANCIALS_DB) as db:
        if not db.table_exists("financials"):
            return pd.DataFrame()
        df = db.read("financials", where="symbol = ? AND freq = ?", params=[symbol, freq])
    if df.empty:
        return df
    df["period_end"] = pd.to_datetime(df["period_end"], errors="coerce")
    df = df.dropna(subset=["period_end"]).sort_values("period_end")
    return _true_annual(df) if freq == "annual" else df


@st.cache_data(show_spinner=False)
def _load_splits(symbol: str, _mtime: float) -> pd.Series:
    """Symbol's split events from ohlcv.db as a datetime-indexed factor series.

    The full split history (not the analysis lookback window) so EPS far back is
    adjusted; same source the analysis pipeline feeds metrics.split_adjust.
    """
    if not settings.OHLCV_DB.exists():
        return pd.Series(dtype=float)
    with Database(settings.OHLCV_DB) as db:
        if not db.table_exists("ohlcv"):
            return pd.Series(dtype=float)
        df = db.read("ohlcv", where="symbol = ?", params=[symbol])
    if df.empty or "splits" not in df.columns:
        return pd.Series(dtype=float)
    s = pd.Series(pd.to_numeric(df["splits"], errors="coerce").values,
                  index=pd.to_datetime(df["date"], errors="coerce")).dropna()
    return s[(s != 0) & (s != 1)]


def _num(v) -> float:
    n = pd.to_numeric(v, errors="coerce")
    return float(n) if pd.notna(n) else float("nan")


def _compact(values: list[float]) -> tuple[list, str]:
    """Scale raw money values to B/M/K for a readable axis; NaN -> None (a bar gap)."""
    mx = max((abs(v) for v in values if v == v), default=0.0)  # v==v skips NaN
    for div, suf in ((1e9, "B"), (1e6, "M"), (1e3, "K")):
        if mx >= div:
            return [v / div if v == v else None for v in values], suf
    return [v if v == v else None for v in values], ""


def _param_label(key: str) -> str:
    return metrics.RAW_PERIOD_FIELDS.get(key) or _RATIO_LABELS.get(key, key)


def _render_fundamentals_bar(symbols: list[str]) -> None:
    """One symbol × one parameter, as bars across its reported periods (ROADMAP 6.2)."""
    st.subheader("Fundamentals over time")
    st.caption("One symbol, one parameter, across its reported periods. Ratios use the "
               "same formulas as the analysis snapshot; price-based ratios (P/E, yield) "
               "and growth/score metrics aren't shown here.")

    _top = st.columns([2, 3, 2], vertical_alignment="bottom")
    _symbol = _top[0].selectbox("Symbol", symbols, index=0)
    _options = list(metrics.RAW_PERIOD_FIELDS) + list(metrics.RATIO_PERIOD_METRICS)
    _param = _top[1].selectbox("Parameter", _options, index=0, format_func=_param_label)
    _freq_label = _top[2].radio("Period", list(_FREQ), index=0, horizontal=True)
    _freq = _FREQ[_freq_label]

    _mtime = settings.FINANCIALS_DB.stat().st_mtime if settings.FINANCIALS_DB.exists() else 0.0
    _fin = _load_financials(_symbol, _freq, _mtime)
    if _fin.empty:
        st.warning(f"No {_freq_label.lower()} financials found for **{_symbol}**.")
        return

    _labels = [d.strftime("%Y") if _freq == "annual" else d.strftime("%Y-%m")
               for d in _fin["period_end"]]

    # Per-period values. Ratios call the canonical metrics.py functions with each
    # period's reported inputs (one formula, shared with the snapshot); raw money
    # figures are scaled to B/M/K, EPS and ratios are shown as-is.
    if _param in metrics.RATIO_PERIOD_METRICS:
        _fn, _fields, _unit = metrics.RATIO_PERIOD_METRICS[_param]
        _vals = [_fn(*[_num(r.get(f)) for f in _fields]) for _, r in _fin.iterrows()]
        _data = [round(v, 2) if v == v else None for v in _vals]
        _yname = f"{_param_label(_param)} ({_unit})"
    elif _param == "diluted_eps":
        # EPS is per-share and stored split-UNADJUSTED, so a raw series jumps at a
        # split. Rescale to current-share terms with the SAME function the analysis
        # snapshot uses (one definition), feeding the symbol's full split history.
        _eps = pd.Series([_num(r.get(_param)) for _, r in _fin.iterrows()],
                         index=pd.to_datetime(_fin["period_end"].values))
        _o_mtime = settings.OHLCV_DB.stat().st_mtime if settings.OHLCV_DB.exists() else 0.0
        _eps = metrics.split_adjust(_eps, _load_splits(_symbol, _o_mtime))
        _data = [round(float(v), 2) if pd.notna(v) else None for v in _eps.to_numpy()]
        _yname = f"{_param_label(_param)} (split-adjusted)"
    else:
        _raw = [_num(r.get(_param)) for _, r in _fin.iterrows()]
        _scaled, _suf = _compact(_raw)
        _data = [round(v, 2) if v is not None else None for v in _scaled]
        _yname = _param_label(_param) + (f" ({_suf})" if _suf else "")

    if not any(d is not None for d in _data):
        st.info(f"No **{_param_label(_param)}** data reported for {_symbol}.")
        return

    _rotate = 45 if (_freq == "quarterly" and len(_labels) > 8) else 0
    _options_ec = {
        "backgroundColor": _DARK_BG,
        "textStyle": {"color": _DARK_TEXT},
        "tooltip": {"trigger": "axis",
                    "backgroundColor": "rgba(15,18,25,0.92)",
                    "borderColor": "rgba(255,255,255,0.20)",
                    "textStyle": {"color": _DARK_TEXT}},
        "grid": {"left": 8, "right": 18, "top": 50, "bottom": 40, "containLabel": True},
        "xAxis": {"type": "category", "data": _labels,
                  "axisLine": {"lineStyle": {"color": "rgba(255,255,255,0.35)"}},
                  "axisLabel": {"color": _DARK_TEXT, "rotate": _rotate}},
        "yAxis": {"type": "value", "name": _yname, "scale": False,
                  "nameTextStyle": {"color": _DARK_TEXT}, "nameGap": 16,
                  "axisLabel": {"color": _DARK_TEXT},
                  "splitLine": {"show": True, "lineStyle": {"color": _GRID_LINE}}},
        "series": [{
            "type": "bar", "data": _data, "barMaxWidth": 48,
            "itemStyle": {"color": _COLORWAY[1]},
            "label": {"show": len([d for d in _data if d is not None]) <= 8,
                      "position": "top", "color": _DARK_TEXT, "fontSize": 11},
        }],
    }
    st_echarts(options=_options_ec, height="520px", key="fund_bar")
    st.caption(f"**{_symbol}** · {_param_label(_param)} · {_freq_label.lower()} periods. "
               "Missing bars = the metric wasn't reported (or its inputs were absent) "
               "that period.")


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

if view == "fundamentals_bar":
    _render_fundamentals_bar(symbols)
    st.stop()

if view != "price":
    st.info(f"Chart view '{view}' isn't built yet — the normalized price chart and "
            "the fundamentals bar chart are available so far.")
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

# -- layout: period row on top, full-width chart below it ---------------------- #
# The ECharts legend (click a name to show/hide a line) replaces a separate symbol
# selector, so every symbol with data is plotted and toggled from the legend. The
# period container is created first (renders above the chart); its widgets are read
# before the chart is built, which renders into the chart container below.
_period_host = st.container()
_chart_host = st.container()
_checked = _have

# Period control (above the chart): "Period:" label + preset buttons on one row. The
# presets set the data window loaded; arbitrary sub-ranges are handled inside the chart
# by the ECharts dataZoom slider (no custom date pickers — ECharts has no native preset
# selector, but its dataZoom IS a native range selector).
_today = pd.Timestamp(date.today())
with _period_host:
    _pr = st.columns([0.5, 6], vertical_alignment="center")
    _pr[0].markdown("**Period:**")
    _period = _pr[1].radio("Period", ["1Y", "3Y", "5Y"], index=2,
                           horizontal=True, label_visibility="collapsed")
_years = {"1Y": 1, "3Y": 3, "5Y": 5}[_period]
_start, _end = _today - pd.DateOffset(years=_years), _today

# -- build the chart (Apache ECharts via streamlit-echarts) ------------------- #
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
        "lineStyle": {"width": 1.5},
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
        "type": "scroll", "top": 4, "left": "center", "right": 90,
        "data": [s["name"] for s in _series_opt],
        "textStyle": {"color": _DARK_TEXT},
        "inactiveColor": "rgba(255,255,255,0.35)",
        "pageTextStyle": {"color": _DARK_TEXT},
        # "All" turns every line on; "Invert" flips the selection (all-on → all-off).
        "selector": [{"type": "all", "title": "All"}, {"type": "inverse", "title": "Invert"}],
        "selectorPosition": "end",
        "selectorLabel": {"color": _DARK_TEXT, "borderColor": "rgba(255,255,255,0.30)",
                          "backgroundColor": "rgba(255,255,255,0.05)"},
        "selectorButtonGap": 8,
    },
    # Native "reset to full chart": the toolbox restore icon (top-right) clears any
    # zoom/pan and returns to the full view — no custom button needed.
    "toolbox": {
        "right": 10, "top": 2,
        "iconStyle": {"borderColor": _DARK_TEXT},
        "emphasis": {"iconStyle": {"borderColor": "#33BBEE"}},
        "feature": {"dataZoom": {"yAxisIndex": "none"}, "restore": {}},
    },
    # Vertical + horizontal gridlines; ECharts time axis picks the tick granularity
    # (years / months / weeks) for the visible span and re-picks it on zoom.
    "grid": {"left": 8, "right": 18, "top": 44, "bottom": 78, "containLabel": True},
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
    # Range selection: mouse-wheel zoom + drag-pan inside the plot, plus a draggable
    # slider at the bottom (ECharts' native arbitrary-range selector — replaces custom
    # date inputs). Both styled for the dark theme.
    "dataZoom": [
        {"type": "inside"},
        {
            "type": "slider", "bottom": 8, "height": 38,
            "borderColor": "rgba(255,255,255,0.18)",
            "fillerColor": "rgba(51,187,238,0.18)",
            "handleStyle": {"color": "#33BBEE"},
            "moveHandleStyle": {"color": "#33BBEE"},
            "textStyle": {"color": _DARK_TEXT},
            "dataBackground": {"lineStyle": {"color": "rgba(255,255,255,0.25)"},
                               "areaStyle": {"color": "rgba(255,255,255,0.06)"}},
            "selectedDataBackground": {"lineStyle": {"color": "#33BBEE"},
                                       "areaStyle": {"color": "rgba(51,187,238,0.12)"}},
        },
    ],
    "series": _series_opt,
}
with _chart_host:
    st_echarts(options=_options, height=f"{_CHART_HEIGHT}px", key="echarts_price")
    st.caption("Apache ECharts (dark theme). Click a legend name to show/hide that line; "
               "hover shows every symbol's value at the cursor. Zoom with the wheel or the "
               "bottom slider; the ⟳ restore icon (top-right) resets to the full chart.")
