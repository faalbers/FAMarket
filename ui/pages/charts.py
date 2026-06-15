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
# Sector / industry relative-strength selector (ROADMAP 4.5 — indices consumption)
# --------------------------------------------------------------------------- #
@st.cache_data(show_spinner=False)
def _load_symbol_groups(symbols: tuple[str, ...], _mtime: float):
    """From analysis.db, group the charted symbols by sector → industries.

    Returns (tree, sym2sector, sym2industry):
      tree         {sector: ['Sector | Industry', ...]} — sorted, sector-tagged only
      sym2sector   {symbol: sector}
      sym2industry {symbol: 'Sector | Industry'}   (matches indices.db labels)
    """
    empty: tuple[dict, dict, dict] = ({}, {}, {})
    if not symbols or not settings.ANALYSIS_DB.exists():
        return empty
    with Database(settings.ANALYSIS_DB) as db:
        if not db.table_exists("analysis"):
            return empty
        ph = ",".join("?" * len(symbols))
        df = db.read("analysis", where=f"symbol IN ({ph})", params=list(symbols))
    if df.empty or "sector" not in df.columns:
        return empty
    tree: dict[str, set] = {}
    sym2sec: dict[str, str] = {}
    sym2ind: dict[str, str] = {}
    for r in df.itertuples(index=False):
        sec, ind, sym = getattr(r, "sector", None), getattr(r, "industry", None), r.symbol
        if not isinstance(sec, str) or not sec:
            continue
        sym2sec[sym] = sec
        tree.setdefault(sec, set())
        if isinstance(ind, str) and ind:
            lbl = f"{sec} | {ind}"
            tree[sec].add(lbl)
            sym2ind[sym] = lbl
    return {s: sorted(v) for s, v in sorted(tree.items())}, sym2sec, sym2ind


@st.cache_data(show_spinner=False)
def _load_index_series(kind: str, label: str, _mtime: float) -> pd.Series:
    """One group's base-100 level series from indices.db, date-indexed; empty if absent."""
    if not settings.INDICES_DB.exists():
        return pd.Series(dtype=float)
    with Database(settings.INDICES_DB) as db:
        if not db.table_exists("sector_industry_index"):
            return pd.Series(dtype=float)
        df = db.read("sector_industry_index", where="kind = ? AND label = ?",
                     params=[kind, label])
    if df.empty:
        return pd.Series(dtype=float)
    s = pd.Series(pd.to_numeric(df["level"], errors="coerce").to_numpy(),
                  index=pd.to_datetime(df["date"], errors="coerce").dt.normalize()).dropna()
    return s[~s.index.duplicated(keep="last")].sort_index()


_GRP_CB = "grpcb::"      # checkbox widget-key prefix
_GRP_ARROW = "grparrow::"  # expand-arrow button-key prefix


def _grp_toggle(key: str) -> None:
    """Single-select + click-active-to-clear, from the just-clicked checkbox's state.

    Flags `chart_grp_changed` so the selector fragment escalates to a full app rerun
    (redrawing the chart). Expand/collapse does NOT set this, so it stays fragment-local.
    """
    on = st.session_state.get(_GRP_CB + key, False)
    st.session_state["chart_grp_sel"] = key if on else None
    st.session_state["chart_grp_changed"] = True


def _grp_toggle_open(sector: str) -> None:
    open_ = st.session_state.setdefault("chart_grp_open", set())
    open_.discard(sector) if sector in open_ else open_.add(sector)


@st.fragment
def _render_group_selector(tree: dict) -> None:
    """Collapsed-by-default expander with sectors (▸/▾ arrow) + non-collapsible industry
    sublists, each a checkbox; single-select across the whole tree → session_state.

    Runs as a FRAGMENT: expanding/collapsing a sector reruns only this fragment, so the
    chart (built in the main script) is NOT recomputed/redrawn. A selection change sets
    `chart_grp_changed` (via `_grp_toggle`), which escalates to a full app rerun here so
    the chart updates. The selected key is the single source of truth; each checkbox's
    state is synced to it before render, so turning one on clears the rest.
    """
    with st.expander("Compare to a sector / industry index", expanded=False):
        sel = st.session_state.setdefault("chart_grp_sel", None)
        st.session_state.setdefault("chart_grp_open", set())
        open_ = st.session_state["chart_grp_open"]
        st.caption("Pick one (click it again to clear). The chart then shows only that "
                   "group's symbols, each relative to its index (the flat 100 line is it).")
        with st.container(height=min(len(tree) * 36 + 80, 340)):
            for sec in tree:
                skey = f"S::{sec}"
                is_open = sec in open_
                row = st.columns([0.06, 0.94], vertical_alignment="center")
                row[0].button("▾" if is_open else "▸", key=_GRP_ARROW + sec,
                              on_click=_grp_toggle_open, args=(sec,))
                st.session_state[_GRP_CB + skey] = (sel == skey)
                row[1].checkbox(f"**{sec}**", key=_GRP_CB + skey,
                                on_change=_grp_toggle, args=(skey,))
                if is_open:
                    for lbl in tree[sec]:
                        ikey = f"I::{lbl}"
                        sub = st.columns([0.12, 0.88], vertical_alignment="center")
                        st.session_state[_GRP_CB + ikey] = (sel == ikey)
                        sub[1].checkbox(lbl.split(" | ", 1)[1], key=_GRP_CB + ikey,
                                        on_change=_grp_toggle, args=(ikey,))
    # A selection (not an expand/collapse) escalates to a full app rerun → chart redraws.
    if st.session_state.pop("chart_grp_changed", False):
        st.rerun(scope="app")


def _line(name: str, dates, values) -> dict:
    """One ECharts line series with the shared style + gap-break points."""
    return {"name": name, "type": "line", "showSymbol": False, "connectNulls": False,
            "lineStyle": {"width": 1.5}, "emphasis": {"focus": "series"},
            "data": _echarts_points(dates, values)}


def _normalized_series(prices: pd.DataFrame, symbols: list[str], start, end) -> list[dict]:
    """Each symbol's adj_close indexed to 100 at the window start (the default view)."""
    win = prices[(prices["date"] >= start) & (prices["date"] <= end)]
    out = []
    for sym in symbols:
        s = win[win["symbol"] == sym]
        if s.empty:
            continue
        base = float(s["adj_close"].iloc[0])
        if base == 0:
            continue
        out.append(_line(sym, s["date"], s["adj_close"] / base * 100.0))
    return out


def _group_symbol_lines(prices: pd.DataFrame, group: list[str], idx: pd.Series,
                        start, end, relative: bool) -> list[dict]:
    """In-group symbols over the period∩index window, rebased to 100 at each line's first
    date shared with the index.

    relative=True  → symbol_norm − index_norm + 100 (above 100 beats the group index).
    relative=False → symbol_norm alone (the same symbols/anchor, without subtracting).
    Both share the same window and anchor, so toggling between them keeps start points.
    """
    if not group or idx.empty:
        return []
    idx = idx[(idx.index >= start) & (idx.index <= end)]
    if idx.empty:
        return []
    win = prices[(prices["date"] >= start) & (prices["date"] <= end)]
    out = []
    for sym in group:
        s = win[win["symbol"] == sym]
        if s.empty:
            continue
        ss = pd.Series(s["adj_close"].to_numpy(),
                       index=pd.to_datetime(s["date"]).dt.normalize())
        ss = ss[~ss.index.duplicated(keep="last")]
        common = ss.index.intersection(idx.index).sort_values()
        if len(common) < 2:
            continue
        s_base, i_base = ss.loc[common[0]], idx.loc[common[0]]
        if s_base == 0 or i_base == 0:
            continue
        sym_norm = ss.loc[common] / s_base * 100.0
        vals = sym_norm - (idx.loc[common] / i_base * 100.0) + 100.0 if relative else sym_norm
        out.append(_line(sym, common, vals))
    return out


def _baseline_marker(value: float = 100.0) -> dict:
    """A silent, legend-less series carrying a dashed horizontal reference line at `value`.

    Used on every view (all base-100): the normal normalized chart, the group
    Symbols/Index views, and Relative (where the 100 line is the index). Name starts with
    '_' so it's filtered out of the legend (always shown, not toggleable).
    """
    return {
        "name": "_baseline", "type": "line", "data": [], "showSymbol": False,
        "silent": True, "tooltip": {"show": False},
        "markLine": {
            "silent": True, "symbol": "none", "label": {"show": False},
            "lineStyle": {"type": "dashed", "color": "rgba(230,230,230,0.55)", "width": 1.5},
            "data": [{"yAxis": value}],
        },
    }


def _index_line(idx: pd.Series, start, end, label: str) -> list[dict]:
    """The group index itself, normalized to 100 at the first date in the window."""
    idx = idx[(idx.index >= start) & (idx.index <= end)]
    if len(idx) < 2:
        return []
    base = idx.iloc[0]
    if base == 0:
        return []
    return [_line(label, idx.index, idx / base * 100.0)]


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

# -- selector (above), then period row, then full-width chart ------------------ #
# The sector/industry selector sits above everything; its selection
# (st.session_state['chart_grp_sel']) switches the chart into relative-strength mode
# and is re-read every rerun, so changing the Period below recomputes the relative view.
_a_mtime = settings.ANALYSIS_DB.stat().st_mtime if settings.ANALYSIS_DB.exists() else 0.0
_tree, _sym2sec, _sym2ind = _load_symbol_groups(tuple(_have), _a_mtime)
# The selector renders BELOW the chart (further down), but its selection lives in
# session_state, so the chart can read it here (set by last run's checkbox callbacks).
_sel = st.session_state.get("chart_grp_sel")

# Changing the selected group starts the legend fresh (all symbols on); the per-line
# on/off state is only meant to persist across the view toggle / period, not across a
# different sector/industry. (A mode or period change leaves _sel unchanged, so this
# doesn't fire then.)
if _sel != st.session_state.get("chart_grp_last"):
    st.session_state["chart_grp_last"] = _sel
    st.session_state.pop("chart_legend_sel", None)

# The ECharts legend (click a name to show/hide a line) replaces a separate symbol
# selector. Containers render in creation order: period row, then the (group-only) view
# toggle, then the chart — all read before the chart is built below.
_period_host = st.container()
_toggle_host = st.container()
_chart_host = st.container()
_selector_host = st.container()  # sector/industry selector renders here, below the chart

# Period control (above the chart): "Period:" label + preset buttons on one row. The
# presets set the data window loaded; arbitrary sub-ranges are handled inside the chart
# by the ECharts dataZoom slider (no custom date pickers — ECharts has no native preset
# selector, but its dataZoom IS a native range selector).
_today = pd.Timestamp(date.today())
with _period_host:
    _pr = st.columns([0.5, 6], vertical_alignment="center")
    _pr[0].markdown("**Period:**")
    _period = _pr[1].radio("Period", ["1Y", "3Y", "5Y"], index=1,
                           horizontal=True, label_visibility="collapsed")
_years = {"1Y": 1, "3Y": 3, "5Y": 5}[_period]
_start, _end = _today - pd.DateOffset(years=_years), _today

# -- build the series: relative-to-index when a group is selected, else normalized -
# Relative mode plots only the in-group symbols as (symbol_norm − index_norm + 100) over
# the period ∩ index window (see _relative_series); deselecting returns the normal view.
if _sel:
    _kind = "sector" if _sel.startswith("S::") else "industry"
    _label = _sel[3:]
    _group = [s for s in _have
              if (_sym2sec if _kind == "sector" else _sym2ind).get(s) == _label]
    _i_mtime = settings.INDICES_DB.stat().st_mtime if settings.INDICES_DB.exists() else 0.0
    _idx = _load_index_series(_kind, _label, _i_mtime)
    # 3-way view toggle (above the chart): same window + base-100, only content changes.
    with _toggle_host:
        _mode = st.segmented_control(
            "View", ["Relative", "Symbols", "Index"], default="Relative",
            key="chart_grp_mode", label_visibility="collapsed") or "Relative"
    if _mode == "Index":
        _series_opt = _index_line(_idx, _start, _end, _label)
        _yaxis_name = "Indexed (100)"
        _head = f"**{_label}** index — normalized to 100 at the window start."
    else:
        _relative = _mode == "Relative"
        _series_opt = _group_symbol_lines(prices, _group, _idx, _start, _end, _relative)
        _yaxis_name = "Relative to index (100)" if _relative else "Indexed (100)"
        _head = (f"**{_label}** — each symbol relative to its index (symbol − index + 100). "
                 "Above 100 beats the group, below lags; the flat 100 line is the index."
                 if _relative else
                 f"**{_label}** symbols — normalized to 100 at the window start "
                 "(not relative to the index).")
    if not _series_opt:
        _chart_host.info(
            f"Nothing to plot for **{_label}**: no charted symbols in this group, or no "
            "overlapping dates between them and the group's index history.")
        st.stop()
else:
    _series_opt = _normalized_series(prices, _have, _start, _end)
    _yaxis_name = "Indexed (100)"
    _head = "Normalized adjusted close — every line indexed to 100 at the window start."
    if not _series_opt:
        _chart_host.info("No data in the selected window.")
        st.stop()

# Dashed horizontal baseline at 100 — every view is base-100 (the normal normalized
# chart, the group Symbols/Index views, and Relative where the 100 line is the index).
# Excluded from the legend (name starts with "_") so it's always shown.
_series_opt = [*_series_opt, _baseline_marker(100.0)]

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
        "data": [s["name"] for s in _series_opt if not s["name"].startswith("_")],
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
        "type": "value", "name": _yaxis_name, "scale": True,
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
# Persist which legend lines are toggled on/off across reruns (mode/period switches): the
# chart reports legendselectchanged back to Python, we remember it, and re-apply it as
# legend.selected. Names absent from the stored map default to shown, so a different
# series set (e.g. the Index view) is unaffected.
_legend_sel = st.session_state.get("chart_legend_sel")
if _legend_sel:
    _options["legend"]["selected"] = _legend_sel

with _chart_host:
    st.caption(_head)
    _ret = st_echarts(
        options=_options,
        events={"legendselectchanged": "function(p){ return p.selected; }"},
        height=f"{_CHART_HEIGHT}px", key="echarts_price")
    if isinstance(_ret, dict) and _ret:
        _cur = st.session_state.get("chart_legend_sel") or {}
        _merged = {**_cur, **_ret}
        if _merged != _cur:  # store the new on/off state and redraw with it applied
            st.session_state["chart_legend_sel"] = _merged
            st.rerun()
    st.caption("Apache ECharts (dark theme). Click a legend name to show/hide that line "
               "(kept across the view toggle); hover shows every symbol's value at the "
               "cursor. Zoom with the wheel or the bottom slider; the ⟳ restore icon "
               "(top-right) resets to the full chart.")

# Sector/industry selector — below the chart, collapsed by default, and a FRAGMENT:
# expand/collapse reruns only it (chart untouched), a selection triggers a full rerun.
if _tree:
    with _selector_host:
        _render_group_selector(_tree)
