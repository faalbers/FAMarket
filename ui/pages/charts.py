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
the full view. Line breaks mark data gaps (no interpolation).

view=fundamentals_bar: one symbol × one parameter, across its reported periods.
view=fundamentals_line: one parameter × every selected symbol, as lines over reported
periods (Indexed-to-100 or Actual), on a time axis so different fiscal calendars align.
view=radar: the five 0-100 category scores (Value/Quality/Growth/Momentum/Income), one
polygon per selected symbol, read from the analysis.db snapshot.
view=dividend_line: dividend yield per calendar period (annual/quarterly), one line per
selected symbol on a time axis. Yield = period dividends ÷ the period-end RAW close × 100
(nominal divided by contemporaneous price). The dividend bar + heat-map views land next.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import streamlit as st
from streamlit_echarts import JsCode, st_echarts

import reporting
from reporting import ai_news_report
from analysis_layer import metrics
from analysis_layer import scoring_rules as _SR
from config import settings
from config import param_hints
from core.database import Database
from data_layer import news as _news
from ui import param_picker as P
from ui import selection_io as SEL
from ui.filter_engine import (
    load_filterset_from, evaluate, _block_mask,
    resolve_column, is_complete,
)
from ui.file_io import ask_open_path
from ui.chart_theme import (
    COLORWAY as _COLORWAY,
    DARK_BG as _DARK_BG,
    DARK_TEXT as _DARK_TEXT,
    GRID_LINE as _GRID_LINE,
    HEAT_RAMP as _HEAT_RAMP,
    echarts_points as _echarts_points,
    legend_style as _legend_style,
    tooltip_style as _tooltip_style,
)

_CHART_HEIGHT = 600  # px; the symbol list's scroll box is capped to this so they align

# Every legend interaction that changes which lines show must report the FULL selected
# map so the server can persist it (and the Save/Add Selection bar can read it). Individual
# clicks fire legendselectchanged; the All / Invert selector BUTTONS fire their own events
# (legendselectall / legendinverseselect) — without these two, an Invert silently leaves
# the server's legend map stale, which broke additive Add.
_LEGEND_EVENTS = {
    "legendselectchanged": "function(p){ return p.selected; }",
    "legendselectall": "function(p){ return p.selected; }",
    "legendinverseselect": "function(p){ return p.selected; }",
}

# Fundamentals-bar view (ROADMAP 6.2): one symbol × one parameter, plotted over its
# reported periods. Ratios reuse metrics.RATIO_PERIOD_METRICS — the SAME formula
# functions the analysis snapshot uses — so a ratio is defined once, never twice.
# These are display labels only (a UI concern); the formulas live in metrics.py.
_RATIO_LABELS = {
    "gross_margin": "Gross margin", "operating_margin": "Operating margin",
    "net_margin": "Net margin", "fcf_margin": "FCF margin", "roe": "ROE", "roa": "ROA",
    "asset_turnover": "Asset turnover", "equity_multiplier": "Equity multiplier",
    "roe_roa_gap": "ROE-ROA gap",
    "debt_to_equity": "Debt / equity", "debt_to_ebitda": "Debt / EBITDA",
    "current_ratio": "Current ratio", "interest_coverage": "Interest coverage",
}
_FREQ = {"Annual": "annual", "Quarterly": "quarterly"}

# Radar view (ROADMAP 6.2): the five 0-100 category scores per symbol, read straight
# from the analysis.db snapshot (no recompute). (score column, axis label), in the
# order they ring the radar.
_RADAR_CATEGORIES = [
    ("value_score", "Value"), ("quality_score", "Quality"), ("growth_score", "Growth"),
    ("momentum_score", "Momentum"), ("income_score", "Income"),
]

def _score_help(col: str) -> str | None:
    """The category-score hint for a *_score column, as markdown for the st.metric
    tooltip — the column name is already the metric label, so drop the header and
    Peers line. Formatting lives in config.param_hints; one source of truth."""
    if param_hints.get_hint(col) is None:
        return None
    return param_hints.hint_markdown(
        col, header=False, sections=("what_it_is", "how_to_use")) or None


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


@st.cache_data(show_spinner=False)
def _load_div_prices(symbol: str, _mtime: float) -> pd.DataFrame:
    """One symbol's full daily RAW close + dividend events from ohlcv.db, date-indexed.

    Uses the raw `close`, NOT adj_close: the `dividends` column is the nominal per-share
    cash paid at the time, so each period's yield must divide it by the contemporaneous
    (unadjusted) close — back-adjusted prices would inflate historical yields. Full
    history (not the analysis window) so every calendar period can form a yield.
    """
    if not settings.OHLCV_DB.exists():
        return pd.DataFrame()
    with Database(settings.OHLCV_DB) as db:
        if not db.table_exists("ohlcv"):
            return pd.DataFrame()
        df = db.read("ohlcv", where="symbol = ?", params=[symbol])
    if df.empty or "close" not in df.columns:
        return pd.DataFrame()
    df = df.copy()
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    df["close"] = pd.to_numeric(df["close"], errors="coerce")
    df["dividends"] = (pd.to_numeric(df["dividends"], errors="coerce")
                       if "dividends" in df.columns else 0.0)
    df = df.dropna(subset=["date", "close"])
    df["dividends"] = df["dividends"].fillna(0.0)
    return df.set_index("date").sort_index()[["close", "dividends"]]


def _period_yields(df: pd.DataFrame, freq: str) -> tuple[list, list[float]]:
    """Calendar-period dividend yields (%) from a _load_div_prices frame, oldest→newest.

    Per period: summed dividends ÷ the period-end close × 100. `freq` is 'annual'
    (calendar year) or 'quarterly' (calendar quarter); TTM is intentionally excluded from
    the growth views. A period with a price but no payout yields a real 0%. The current,
    still-running period is dropped — its dividends/price aren't final yet (its period-end
    label falls past the last available bar), so only completed periods are returned.
    Returns (period_end dates, yields) aligned to the price history's span.
    """
    if df.empty:
        return [], []
    rule = "YE" if freq == "annual" else "QE"
    px_end = df["close"].resample(rule).last()
    div_sum = df["dividends"].resample(rule).sum()
    last_bar = df.index.max()
    mask = px_end.notna() & (px_end > 0) & (px_end.index <= last_bar)
    yld = div_sum[mask] / px_end[mask] * 100.0
    return list(yld.index), [float(v) for v in yld.to_numpy()]


def _num(v) -> float:
    n = pd.to_numeric(v, errors="coerce")
    return float(n) if pd.notna(n) else float("nan")


def _scale_factor(maxabs: float) -> tuple[float, str]:
    """Pick a B/M/K divisor + suffix for the largest magnitude on an axis (1, none)."""
    for div, suf in ((1e9, "B"), (1e6, "M"), (1e3, "K")):
        if maxabs >= div:
            return div, suf
    return 1.0, ""


def _compact(values: list[float]) -> tuple[list, str]:
    """Scale raw money values to B/M/K for a readable axis; NaN -> None (a bar gap)."""
    mx = max((abs(v) for v in values if v == v), default=0.0)  # v==v skips NaN
    _div, _suf = _scale_factor(mx)
    return [v / _div if v == v else None for v in values], _suf


def _param_label(key: str) -> str:
    return metrics.RAW_PERIOD_FIELDS.get(key) or _RATIO_LABELS.get(key, key)


def _fund_category(key: str) -> str:
    """Group label for the shared param picker — ratios vs raw statement items."""
    return "Ratios" if key in metrics.RATIO_PERIOD_METRICS else "Statement items"


def _fund_info_html(key: str) -> str:
    """Inline info HTML for a fundamentals parameter, reusing the picker's param_hints
    renderer (a SimpleNamespace stands in for a filter_registry Base). Ratio keys have
    real hints; raw statement items fall back to name · category."""
    unit = metrics.RATIO_PERIOD_METRICS[key][2] if key in metrics.RATIO_PERIOD_METRICS else ""
    shim = SimpleNamespace(key=key, name=_param_label(key), category=_fund_category(key),
                           unit=unit)
    return P.hint_html(shim)


def _period_values(fin: pd.DataFrame, param: str, symbol: str,
                   o_mtime: float) -> tuple[list[float], str, str]:
    """Per-period values for `param` across `fin`'s rows (oldest→newest), with its kind.

    Returns (values, kind, label): `kind` in {'ratio','eps','money'} tells the caller
    whether to scale (money → B/M/K) or show as-is; `label` is the y-axis label without a
    scale suffix. Ratios call the canonical metrics.py formulas (the SAME ones the
    analysis snapshot uses); diluted EPS is split-adjusted to current shares; money is
    raw native units. Shared by the fundamentals bar and growth-line views so a value is
    derived once, never twice.
    """
    if param in metrics.RATIO_PERIOD_METRICS:
        _fn, _fields, _unit = metrics.RATIO_PERIOD_METRICS[param]
        _vals = [_fn(*[_num(r.get(f)) for f in _fields]) for _, r in fin.iterrows()]
        return _vals, "ratio", f"{_param_label(param)} ({_unit})"
    if param == "diluted_eps":
        _eps = pd.Series([_num(r.get(param)) for _, r in fin.iterrows()],
                         index=pd.to_datetime(fin["period_end"].values))
        _eps = metrics.split_adjust(_eps, _load_splits(symbol, o_mtime))
        return ([float(v) if pd.notna(v) else float("nan") for v in _eps.to_numpy()],
                "eps", f"{_param_label(param)} (split-adjusted)")
    return [_num(r.get(param)) for _, r in fin.iterrows()], "money", _param_label(param)


def _cb_fund_param(key: str) -> None:
    st.session_state["fund_param"] = key


def _render_fundamentals_bar(symbols: list[str], picked: list[str] | None = None) -> None:
    """One symbol × one parameter, as bars across its reported periods (ROADMAP 6.2)."""
    st.subheader("Fundamentals over time")

    _options = list(metrics.RAW_PERIOD_FIELDS) + list(metrics.RATIO_PERIOD_METRICS)
    # On first arrival, default to the first Output-shown column this view supports.
    _initial = next((c for c in (picked or []) if c in _options), _options[0])
    _sel = st.session_state.setdefault("fund_param", _initial)
    if _sel not in _options:  # options are static, but guard anyway
        _sel = st.session_state["fund_param"] = _options[0]

    # Period on the LEFT (fixed), parameter picker on the RIGHT — the picker's box
    # hugs its selected-param label and so resizes, which would otherwise shove the
    # Period radio around; keeping it left of the growing box pins it in place.
    # Parameter selection uses the SAME popover browser as the Filter page (search box,
    # category groups, per-row ▸ info from param_hints) — single-select (close on pick).
    _top = st.columns([1, 4], vertical_alignment="bottom")
    # Right-align the radio inside its column (keyed "fundperiod" → CSS in app.py) so it
    # abuts the parameter box rather than leaving a wide gap, whatever the column width.
    _freq_label = _top[0].container(key="fundperiod").radio(
        "Period", list(_FREQ), index=0, horizontal=True)
    _freq = _FREQ[_freq_label]
    with _top[1]:
        st.caption("Parameter")
        P.render(
            st.container(),
            opt_keys=_options,
            label=f"📊  {_param_label(_sel)}",
            keyp="fundparam",
            category_of=_fund_category,
            name_of=_param_label,
            info_html_of=_fund_info_html,
            search_text_of=lambda k: f"{_param_label(k)} {k}".lower(),
            is_selected=lambda k: k == st.session_state.get("fund_param"),
            on_pick=_cb_fund_param,
            close_on_pick=True,
            exclude_selected=False,  # keep the full list visible, current row primary-styled
            trigger_width="content",  # hug the selected-param label instead of filling the column
        )
        P.scroll_to_current()
    _param = st.session_state["fund_param"]

    # Symbol list as a vertical single-select radio on the LEFT, chart on the right — only
    # one symbol charts at a time, so no legend / All-Invert needed (unlike the other charts).
    _left, _right = st.columns([1, 7], gap="medium")
    _symbol = _left.radio("Symbol", symbols, index=0)

    _mtime = settings.FINANCIALS_DB.stat().st_mtime if settings.FINANCIALS_DB.exists() else 0.0
    _fin = _load_financials(_symbol, _freq, _mtime)
    if _fin.empty:
        _right.warning(f"No {_freq_label.lower()} financials found for **{_symbol}**.")
        return

    _labels = [d.strftime("%Y") if _freq == "annual" else d.strftime("%Y-%m")
               for d in _fin["period_end"]]

    # Per-period values via the shared deriver (ratios use the snapshot's formulas; EPS
    # is split-adjusted). Raw money figures are then scaled to B/M/K for the axis; ratios
    # and EPS are shown as-is.
    _o_mtime = settings.OHLCV_DB.stat().st_mtime if settings.OHLCV_DB.exists() else 0.0
    _vals, _kind, _yname = _period_values(_fin, _param, _symbol, _o_mtime)
    if _kind == "money":
        _scaled, _suf = _compact(_vals)
        _data = [round(v, 2) if v is not None else None for v in _scaled]
        if _suf:
            _yname += f" ({_suf})"
    else:
        _data = [round(v, 2) if v == v else None for v in _vals]

    if not any(d is not None for d in _data):
        _right.info(f"No **{_param_label(_param)}** data reported for {_symbol}.")
        return

    _rotate = 45 if (_freq == "quarterly" and len(_labels) > 8) else 0
    _options_ec = {
        "backgroundColor": _DARK_BG,
        "textStyle": {"color": _DARK_TEXT},
        "tooltip": _tooltip_style(trigger="axis"),
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
    with _right:
        st_echarts(options=_options_ec, height="560px", key="fund_bar")
        st.caption(f"**{_symbol}** · {_param_label(_param)} · {_freq_label.lower()} periods")


# --------------------------------------------------------------------------- #
# Fundamentals growth-line view (ROADMAP 6.2) — one parameter, many symbols, lines
# --------------------------------------------------------------------------- #
# A period-aware gap break. chart_theme.echarts_points uses a 7-day threshold (built for
# daily prices); reporting periods sit a quarter/year apart, so a *missing* period is a
# much wider hole — break the line only when more than one period is skipped.
_PERIOD_GAP_DAYS = {"annual": 550, "quarterly": 135}


def _period_label(d, freq: str) -> str:
    """Calendar-period label naming the period a value was computed FOR: "2025" (annual)
    or "2025-Q3" (quarterly). Period-ends are Dec-31 / quarter-ends, so a Dec-31 close
    belongs to its own year, not the next one a time axis would visually push it toward."""
    ts = pd.Timestamp(d)
    if freq == "annual":
        return f"{ts.year}"
    return f"{ts.year}-Q{(ts.month - 1) // 3 + 1}"


def _period_points(dates, values, freq: str) -> list[list]:
    """ECharts [date, value] points for one symbol's periods, with a null inserted where
    a reporting period is missing (>1 period gap) so the line breaks there instead of
    interpolating across the hole. NaN values pass through as nulls (also breaks)."""
    gap = _PERIOD_GAP_DAYS.get(freq, 135)
    pts: list[list] = []
    prev = None
    for d, v in zip(dates, values):
        val = round(float(v), 2) if (v is not None and v == v) else None
        if prev is not None and (d - prev).days > gap:
            pts.append([(prev + (d - prev) / 2).strftime("%Y-%m-%d"), None])
        pts.append([d.strftime("%Y-%m-%d"), val])
        prev = d
    return pts


def _last_clean_run_start(dates, values, gap: int) -> int:
    """Index where the most recent unbroken run of periods begins.

    A break is a NaN/None value or a period spaced more than `gap` days from the
    previous one — the same conditions that break the rendered line in
    _period_points. Returns len(dates) if even the newest period is a break.
    """
    start = 0
    prev = None
    for i, (d, v) in enumerate(zip(dates, values)):
        valid = v is not None and v == v
        if prev is not None and (d - prev).days > gap:
            start = i        # time gap before i -> run restarts at i
        if not valid:
            start = i + 1    # NaN at i -> run restarts after i
        prev = d
    return start


def _line_series(name: str, points: list[list]) -> dict:
    """One ECharts line series for the growth-line view. Markers shown (periods are
    sparse, so each reported point is worth seeing); nulls break the line."""
    return {"name": name, "type": "line", "showSymbol": True, "symbolSize": 5,
            "connectNulls": False, "lineStyle": {"width": 1.8},
            "emphasis": {"focus": "series"}, "data": points}


def _growth_line_options(series: list[dict], yname: str,
                         x_categories: list[str] | None = None,
                         x_label_formatter: str | None = None) -> dict:
    """Shared ECharts options for the growth-line charts (fundamentals growth + dividend
    yield): dark theme, unified axis tooltip, a left vertical scroll legend with All/Invert,
    and a dataZoom slider + restore. The y-axis name and series differ between callers.
    Series whose name starts with "_" (e.g. a baseline marker) stay off the legend but are
    always drawn. By default the x-axis is a TIME axis (aligns differing fiscal calendars by
    date); pass `x_categories` to instead use a CATEGORY axis whose ticks/labels name each
    calendar period (dividend yield) — series data must then be values aligned to it.
    `x_label_formatter` is a JS function body (str) applied to the category axis labels — used
    to thin crowded labels (e.g. show only Q1) while leaving the full category for tooltips."""
    _xaxis = ({"type": "category", "data": x_categories, "boundaryGap": False}
              if x_categories is not None else {"type": "time"})
    _axislabel = {"color": _DARK_TEXT}
    if x_categories is not None:
        _axislabel["fontSize"] = 10
    if x_label_formatter is not None:
        _axislabel.update({"interval": 0, "formatter": JsCode(x_label_formatter).js_code})
    _xaxis.update({"axisLine": {"lineStyle": {"color": "rgba(255,255,255,0.35)"}},
                   "axisLabel": _axislabel,
                   "splitLine": {"show": True, "lineStyle": {"color": _GRID_LINE}}})
    return {
        "backgroundColor": _DARK_BG,
        "color": list(_COLORWAY),
        "textStyle": {"color": _DARK_TEXT},
        "tooltip": _tooltip_style(trigger="axis", order="valueDesc"),
        "legend": _legend_style(
            [s["name"] for s in series if not s["name"].startswith("_")]),
        "toolbox": {"right": 10, "top": 2, "iconStyle": {"borderColor": _DARK_TEXT},
                    "emphasis": {"iconStyle": {"borderColor": "#33BBEE"}},
                    "feature": {"dataZoom": {"yAxisIndex": "none"}, "restore": {}}},
        "grid": {"left": 96, "right": 18, "top": 16, "bottom": 78, "containLabel": True},
        "xAxis": _xaxis,
        "yAxis": {"type": "value", "name": yname, "scale": True,
                  "nameTextStyle": {"color": _DARK_TEXT},
                  "axisLabel": {"color": _DARK_TEXT},
                  "splitLine": {"show": True, "lineStyle": {"color": _GRID_LINE}}},
        "dataZoom": [
            {"type": "inside"},
            {"type": "slider", "bottom": 8, "height": 38,
             "borderColor": "rgba(255,255,255,0.18)",
             "fillerColor": "rgba(51,187,238,0.18)",
             "handleStyle": {"color": "#33BBEE"}, "moveHandleStyle": {"color": "#33BBEE"},
             "textStyle": {"color": _DARK_TEXT},
             "dataBackground": {"lineStyle": {"color": "rgba(255,255,255,0.25)"},
                                "areaStyle": {"color": "rgba(255,255,255,0.06)"}},
             "selectedDataBackground": {"lineStyle": {"color": "#33BBEE"},
                                        "areaStyle": {"color": "rgba(51,187,238,0.12)"}}},
        ],
        "series": series,
    }


def _cb_fundline_param(key: str) -> None:
    st.session_state["fundline_param"] = key


def _render_fundamentals_line(symbols: list[str], picked: list[str] | None = None) -> None:
    """One parameter across all selected symbols, as lines over reported periods (ROADMAP
    6.2 — Fundamentals growth line). A TIME x-axis lets symbols on different fiscal
    calendars align by date; missing periods break the line (no interpolation)."""
    st.subheader("Fundamentals growth — over time")

    _opts = list(metrics.RAW_PERIOD_FIELDS) + list(metrics.RATIO_PERIOD_METRICS)
    # On first arrival, default to the first Output-shown column this view supports.
    _initial = next((c for c in (picked or []) if c in _opts), _opts[0])
    _sel = st.session_state.setdefault("fundline_param", _initial)
    if _sel not in _opts:  # options are static, but guard anyway
        _sel = st.session_state["fundline_param"] = _opts[0]

    # Period radio (left, fixed) + parameter picker (right, content-width) — same layout
    # and shared "fundperiod" CSS hook as the bar view, so the picker box can grow without
    # shoving the radio. The picker is the SAME popover browser as the Filter page.
    _top = st.columns([1, 4], vertical_alignment="bottom")
    _freq_label = _top[0].container(key="fundperiod").radio(
        "Period", list(_FREQ), index=0, horizontal=True)
    _freq = _FREQ[_freq_label]
    with _top[1]:
        st.caption("Parameter")
        P.render(
            st.container(),
            opt_keys=_opts,
            label=f"📊  {_param_label(_sel)}",
            keyp="fundlineparam",
            category_of=_fund_category,
            name_of=_param_label,
            info_html_of=_fund_info_html,
            search_text_of=lambda k: f"{_param_label(k)} {k}".lower(),
            is_selected=lambda k: k == st.session_state.get("fundline_param"),
            on_pick=_cb_fundline_param,
            close_on_pick=True,
            exclude_selected=False,
            trigger_width="content",
        )
        P.scroll_to_current()
    _param = st.session_state["fundline_param"]

    _mode = st.segmented_control(
        "Scale", ["Actual", "Normalized"], default="Actual",
        key="fundline_mode", label_visibility="collapsed") or "Actual"
    _indexed = _mode == "Normalized"

    _f_mtime = settings.FINANCIALS_DB.stat().st_mtime if settings.FINANCIALS_DB.exists() else 0.0
    _o_mtime = settings.OHLCV_DB.stat().st_mtime if settings.OHLCV_DB.exists() else 0.0

    # Gather each symbol's (period_end dates, values) for the chosen parameter. The kind
    # and y-axis label are the same for every symbol (one parameter), so the last set wins.
    _per_symbol: list[tuple[str, list, list[float]]] = []
    _kind, _ylabel = "money", _param_label(_param)
    _no_data: list[str] = []
    for _sym in symbols:
        _fin = _load_financials(_sym, _freq, _f_mtime)
        if _fin.empty:
            _no_data.append(_sym)
            continue
        _vals, _kind, _ylabel = _period_values(_fin, _param, _sym, _o_mtime)
        if not any(v == v and v != 0 for v in _vals):  # all-NaN or all-zero for this param
            _no_data.append(_sym)
            continue
        _per_symbol.append((_sym, list(_fin["period_end"]), _vals))

    if not _per_symbol:
        st.warning(f"No {_freq_label.lower()} **{_param_label(_param)}** data reported for "
                   "any selected symbol.")
        return

    # Trim to the most recent unbroken run. Per symbol find where its last gap (a missing
    # period OR a NaN value) ends; cut every symbol at the LOWEST (earliest) such start so
    # they share one window. A gap-free parameter keeps full history (cut = earliest period).
    _gap = _PERIOD_GAP_DAYS.get(_freq, 135)
    _starts = [d[si] for _, d, v in _per_symbol
               if (si := _last_clean_run_start(d, v, _gap)) < len(d)]
    _cut = min(_starts) if _starts else None
    if _cut is not None:
        _trimmed: list[tuple[str, list, list[float]]] = []
        for _sym, _dates, _vals in _per_symbol:
            _keep = [(d, v) for d, v in zip(_dates, _vals) if d >= _cut]
            if _keep and any(v == v for _, v in _keep):
                _ds, _vs = map(list, zip(*_keep))
                _trimmed.append((_sym, _ds, _vs))
            else:
                _no_data.append(_sym)
        _per_symbol = _trimmed

    # Build the lines. Indexed: rebase each symbol to 100 at its first POSITIVE period (a
    # negative/zero base can't be indexed meaningfully → skip with a note). Actual: pick
    # ONE B/M/K divisor across every symbol's money values so the lines share a readable
    # axis (ratios/EPS need no scaling).
    _series: list[dict] = []
    _skipped: list[str] = []
    if _indexed:
        _yname = f"{_ylabel} — normalized to 100"
        for _sym, _dates, _vals in _per_symbol:
            _base = next((v for v in _vals if v == v and v > 0), None)
            if _base is None:
                _skipped.append(_sym)
                continue
            _norm = [v / _base * 100.0 if v == v else float("nan") for v in _vals]
            _series.append(_line_series(_sym, _period_points(_dates, _norm, _freq)))
    else:
        if _kind == "money":
            _mx = max((abs(v) for _, _, _vs in _per_symbol for v in _vs if v == v),
                      default=0.0)
            _div, _suf = _scale_factor(_mx)
            _yname = _ylabel + (f" ({_suf})" if _suf else "")
        else:
            _div, _yname = 1.0, _ylabel
        for _sym, _dates, _vals in _per_symbol:
            _scaled = [v / _div if v == v else float("nan") for v in _vals]
            _series.append(_line_series(_sym, _period_points(_dates, _scaled, _freq)))

    if _no_data:
        st.caption("No data for: " + ", ".join(_no_data))
    if _skipped:
        st.caption("Can't normalize (first value ≤ 0) — switch to **Actual** to see: "
                   + ", ".join(_skipped))
    if not _series:
        st.info("Nothing to plot in **Normalized** mode (every symbol's first value is ≤ 0). "
                "Switch to **Actual**.")
        return

    if _indexed:  # dashed reference line at 100 (the index base)
        _series = [*_series, _baseline_marker(100.0)]

    _options_ec = _growth_line_options(_series, _yname)
    # Keep the legend show/hide selection across the Actual/Normalized + period switches:
    # the chart reports legendselectchanged, we remember it, and re-apply it as
    # legend.selected (names absent from the map default to shown).
    _legend_sel = st.session_state.get("fundline_legend_sel")
    if _legend_sel:
        _options_ec["legend"]["selected"] = _legend_sel
    _symbol_selection_bar(symbols, keyp="fundline", legend_key="fundline_legend_sel")
    _ret = st_echarts(
        options=_options_ec,
        events=_LEGEND_EVENTS,
        height=f"{_CHART_HEIGHT}px",
        key=f"fund_line_{st.session_state.get('echarts_bump', 0)}")
    if isinstance(_ret, dict) and _ret:
        _cur = st.session_state.get("fundline_legend_sel") or {}
        _merged = {**_cur, **_ret}
        if _merged != _cur:
            st.session_state["fundline_legend_sel"] = _merged
            st.rerun()
    _scale_note = ("normalized to 100 at the shared start" if _indexed
                   else "actual reported values")
    _cut_note = f" · from {_cut:%Y-%m-%d} (after last gap)" if _cut is not None else ""
    st.caption(f"{_param_label(_param)} · {_freq_label.lower()} periods · {_scale_note}{_cut_note}")


# --------------------------------------------------------------------------- #
# Dividend-yield growth-line view (ROADMAP 6.2) — yield over calendar periods
# --------------------------------------------------------------------------- #
def _render_dividend_line(symbols: list[str]) -> None:
    """Dividend yield across all selected symbols, one line each over calendar periods
    (ROADMAP 6.2 — dividend yield growth line). Yield per period = summed dividends ÷ the
    period-end close × 100; annual = calendar year, quarterly = calendar quarter (TTM is
    excluded from the growth view by design). TIME x-axis so symbols align by date."""
    st.subheader("Dividend yield — over time")

    _freq_label = st.container(key="fundperiod").radio(
        "Period", list(_FREQ), index=0, horizontal=True)
    _freq = _FREQ[_freq_label]
    _mode = st.segmented_control(
        "Scale", ["Actual", "Normalized"], default="Actual",
        key="divline_mode", label_visibility="collapsed") or "Actual"
    _normalized = _mode == "Normalized"
    _o_mtime = settings.OHLCV_DB.stat().st_mtime if settings.OHLCV_DB.exists() else 0.0

    _per_symbol: list[tuple[str, list, list[float]]] = []
    _no_data: list[str] = []
    for _sym in symbols:
        _df = _load_div_prices(_sym, _o_mtime)
        _dates, _vals = _period_yields(_df, _freq) if not _df.empty else ([], [])
        if not _dates or not any(v == v and v != 0 for v in _vals):  # never paid → all-zero
            _no_data.append(_sym)
            continue
        _per_symbol.append((_sym, _dates, _vals))

    if _no_data:
        st.caption("No price/dividend data for: " + ", ".join(_no_data))
    if not _per_symbol:
        st.warning("No dividend/price history for any selected symbol.")
        return

    # Trim to the most recent unbroken run, same rule as the fundamentals growth line: cut
    # every symbol at the LOWEST first-date-after-its-last-gap so they share one window.
    # (Yields rarely gap — a non-payment is a real 0%, not a hole — so this mostly no-ops.)
    _gap = _PERIOD_GAP_DAYS.get(_freq, 135)
    _starts = [d[si] for _, d, v in _per_symbol
               if (si := _last_clean_run_start(d, v, _gap)) < len(d)]
    _cut = min(_starts) if _starts else None
    if _cut is not None:
        _trimmed: list[tuple[str, list, list[float]]] = []
        for _sym, _dates, _vals in _per_symbol:
            _keep = [(d, v) for d, v in zip(_dates, _vals) if d >= _cut]
            if _keep and any(v == v for _, v in _keep):
                _ds, _vs = map(list, zip(*_keep))
                _trimmed.append((_sym, _ds, _vs))
            else:
                _no_data.append(_sym)
        _per_symbol = _trimmed

    # Render on a CATEGORY x-axis labelled per calendar period ("2025" / "2025-Q3") so each
    # tick names the period the yield was computed FOR — a time axis pushes a Dec-31
    # period-end visually under the next year. Calendar periods are shared across symbols, so
    # build one sorted category list and align each symbol to it (None where it has no period,
    # which breaks the line the same way the time-axis gap markers did).
    _all_dates = sorted({d for _, ds, _ in _per_symbol for d in ds})
    _cats = [_period_label(d, _freq) for d in _all_dates]
    _pos = {d: i for i, d in enumerate(_all_dates)}
    # Normalized: rebase each symbol's yield series to 100 at its first POSITIVE period (same
    # rule as the fundamentals growth line); a symbol that never paid has no base → skip it.
    _series = []
    _skipped: list[str] = []
    for _sym, _dates, _vals in _per_symbol:
        if _normalized:
            _base = next((v for v in _vals if v == v and v > 0), None)
            if _base is None:
                _skipped.append(_sym)
                continue
            _vals = [v / _base * 100.0 if v == v else float("nan") for v in _vals]
        _data: list = [None] * len(_all_dates)
        for d, v in zip(_dates, _vals):
            _data[_pos[d]] = round(float(v), 2) if (v is not None and v == v) else None
        _series.append(_line_series(_sym, _data))

    if _skipped:
        st.caption("Never paid a dividend (can't normalize) — switch to **Actual** to see: "
                   + ", ".join(_skipped))
    if not _series:
        st.info("Nothing to plot in **Normalized** mode (no symbol has a positive yield). "
                "Switch to **Actual**.")
        return
    if _normalized:  # dashed reference line at the 100 base
        _series.append(_baseline_marker(100.0))

    # Quarterly axes get crowded — label only Q1 of each year (full "YYYY-Qn" still shows in
    # the tooltip); annual labels every tick.
    _xfmt = ("function(v){ return v.indexOf('-Q1') >= 0 ? v : ''; }"
             if _freq == "quarterly" else None)
    _yname = "Dividend yield — normalized to 100" if _normalized else "Dividend yield (%)"
    _options_ec = _growth_line_options(_series, _yname,
                                        x_categories=_cats, x_label_formatter=_xfmt)
    # Keep the legend show/hide selection across the Actual/Normalized + period switches.
    _legend_sel = st.session_state.get("divline_legend_sel")
    if _legend_sel:
        _options_ec["legend"]["selected"] = _legend_sel
    _ret = st_echarts(
        options=_options_ec,
        events={"legendselectchanged": "function(p){ return p.selected; }"},
        height=f"{_CHART_HEIGHT}px", key="div_line")
    if isinstance(_ret, dict) and _ret:
        _cur = st.session_state.get("divline_legend_sel") or {}
        _merged = {**_cur, **_ret}
        if _merged != _cur:
            st.session_state["divline_legend_sel"] = _merged
            st.rerun()
    _scale_note = "normalized to 100" if _normalized else "actual yield"
    _cut_note = f" · from {_cut:%Y-%m-%d} (after last gap)" if _cut is not None else ""
    st.caption(f"Dividend yield · {_freq_label.lower()} periods · {_scale_note}{_cut_note}")


# --------------------------------------------------------------------------- #
# Radar view (ROADMAP 6.2) — five category scores, one polygon per symbol
# --------------------------------------------------------------------------- #
@st.cache_data(show_spinner=False)
def _load_scores(symbols: tuple[str, ...], _mtime: float) -> pd.DataFrame:
    """The category-score columns for `symbols` from analysis.db, indexed by symbol.

    `_mtime` (the db file's mtime) is part of the cache key so a fresh analysis run
    invalidates the cache; it's otherwise unused.
    """
    if not symbols or not settings.ANALYSIS_DB.exists():
        return pd.DataFrame()
    with Database(settings.ANALYSIS_DB) as db:
        if not db.table_exists("analysis"):
            return pd.DataFrame()
        ph = ",".join("?" * len(symbols))
        df = db.read("analysis", where=f"symbol IN ({ph})", params=list(symbols))
    if df.empty or "symbol" not in df.columns:
        return pd.DataFrame()
    return df.drop_duplicates("symbol").set_index("symbol")


def _render_radar(symbols: list[str]) -> None:
    """The five 0-100 category scores per symbol, one polygon each (ROADMAP 6.2)."""
    st.subheader("Category scores — radar")

    _mtime = settings.ANALYSIS_DB.stat().st_mtime if settings.ANALYSIS_DB.exists() else 0.0
    _df = _load_scores(tuple(symbols), _mtime)
    if _df.empty:
        st.warning("No analysis scores found — run an analysis first (Fetch Control).")
        return

    _missing = [s for s in symbols if s not in _df.index]
    if _missing:
        st.caption("No analysis row for: " + ", ".join(_missing))

    # One radar datum per symbol; a category with no score becomes None (a gap in that
    # symbol's polygon). Symbols whose every category is missing are skipped entirely.
    _data = []
    for _sym in [s for s in symbols if s in _df.index]:
        _row = _df.loc[_sym]
        _vals = []
        for _col, _ in _RADAR_CATEGORIES:
            _v = pd.to_numeric(_row.get(_col), errors="coerce")
            _vals.append(round(float(_v), 1) if pd.notna(_v) else None)
        if all(v is None for v in _vals):
            continue
        _data.append({"name": _sym, "value": _vals})
    if not _data:
        st.info("None of the selected symbols have category scores "
                "(funds/ETFs often score only some categories).")
        return

    # Info row above the chart: one cell per category, each with a hover ⓘ hint
    # explaining the score. With a single symbol plotted each cell shows that symbol's
    # value; with several, the per-symbol numbers stay on the chart so the cells show
    # just the category + hint (a dash placeholder, since no one value fits all).
    _single = _data[0]["value"] if len(_data) == 1 else None
    _info_cols = st.columns(len(_RADAR_CATEGORIES))
    for _i, (_col_key, _lbl) in enumerate(_RADAR_CATEGORIES):
        _v = _single[_i] if _single is not None else None
        _info_cols[_i].metric(_lbl, f"{_v:.0f}" if _v is not None else "—",
                              help=_score_help(_col_key))

    _options_ec = {
        "backgroundColor": _DARK_BG,
        "color": list(_COLORWAY),
        "textStyle": {"color": _DARK_TEXT},
        # Show ONLY the symbol name (not the per-category value list). The shared airy
        # translucent tooltip (chart_theme.tooltip_style) originated here.
        "tooltip": _tooltip_style(trigger="item",
                                  formatter=JsCode("function(p){ return p.name; }").js_code),
        # Vertical scroll list pinned to the left column (the radar is shifted right
        # to make room); many symbols scroll with the page arrows.
        "legend": _legend_style([d["name"] for d in _data]),
        "radar": {
            "indicator": [{"name": lbl, "max": 100} for _, lbl in _RADAR_CATEGORIES],
            "shape": "polygon", "splitNumber": 5, "center": ["56%", "54%"], "radius": "78%",
            "axisName": {"color": _DARK_TEXT},
            "splitLine": {"lineStyle": {"color": _GRID_LINE}},
            "splitArea": {"areaStyle": {"color": ["rgba(255,255,255,0.02)",
                                                  "rgba(255,255,255,0.05)"]}},
            "axisLine": {"lineStyle": {"color": "rgba(255,255,255,0.25)"}},
        },
        "series": [{
            "type": "radar", "data": _data, "symbolSize": 4,
            # Dim by default so the hovered polygon pops; emphasis brightens it and
            # `focus: self` fades the others (blur) while you hover one.
            "lineStyle": {"width": 1.6, "opacity": 0.55},
            "areaStyle": {"opacity": 0.03},
            "itemStyle": {"opacity": 0.55},
            "emphasis": {"focus": "self",
                         "lineStyle": {"width": 3, "opacity": 1.0},
                         "areaStyle": {"opacity": 0.18},
                         "itemStyle": {"opacity": 1.0}},
            "blur": {"lineStyle": {"opacity": 0.12}, "areaStyle": {"opacity": 0.0},
                     "itemStyle": {"opacity": 0.12}},
        }],
    }
    # Persist legend show/hide across reruns (same pattern as the line charts) so the
    # selection survives and the Save/Add Selection bar can drive it.
    _legend_sel = st.session_state.get("radar_legend_sel")
    if _legend_sel:
        _options_ec["legend"]["selected"] = _legend_sel
    _symbol_selection_bar(symbols, keyp="radar", legend_key="radar_legend_sel")
    # Taller than the line charts so the radar fills the wide Category-scores screen.
    _ret = st_echarts(
        options=_options_ec,
        events=_LEGEND_EVENTS,
        height="760px", key=f"radar_{st.session_state.get('echarts_bump', 0)}")
    if isinstance(_ret, dict) and _ret:
        _cur = st.session_state.get("radar_legend_sel") or {}
        _merged = {**_cur, **_ret}
        if _merged != _cur:
            st.session_state["radar_legend_sel"] = _merged
            st.rerun()


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
# Metrics heat map (ROADMAP 6.2) — symbols × metrics, colored by scoring rules
# --------------------------------------------------------------------------- #
@st.cache_data(show_spinner=False)
def _load_analysis_full(_mtime: float) -> pd.DataFrame:
    """The whole analysis snapshot — needed so peer/universe goodness ranks each cell
    against the FULL universe, not just the handful of charted symbols."""
    if not settings.ANALYSIS_DB.exists():
        return pd.DataFrame()
    with Database(settings.ANALYSIS_DB) as db:
        if not db.table_exists("analysis"):
            return pd.DataFrame()
        return db.read("analysis")


# Heat map default columns (shown if present): a spread across the categories incl. a
# sweet-spot (current ratio / payout) so the two-sided coloring is visible.
_HEAT_DEFAULTS = ["overall_score", "pe", "pb", "roe", "net_margin", "revenue_cagr_3y",
                  "debt_to_equity", "current_ratio", "div_yield_ttm", "rs_rank"]


def _load_heatmap_frame():
    """The analysis frame + rules shared by both heat maps; None on an empty DB."""
    _mtime = settings.ANALYSIS_DB.stat().st_mtime if settings.ANALYSIS_DB.exists() else 0.0
    _df = _load_analysis_full(_mtime)
    if _df.empty or "symbol" not in _df.columns:
        st.warning("No analysis scores found — run an analysis first (Fetch Control).")
        return None, None
    return _df, _SR.load_rules()


def _heat_name(k: str) -> str:
    h = param_hints.get_hint(k)
    return h["name"] if h else k.replace("_", " ").title()


def _render_heatmap(symbols: list[str], picked: list[str] | None = None) -> None:
    """Symbols × metrics grid, each cell colored by its scoring rule (orange = strong,
    blue = weak). Columns default to the Output table's shown params (`picked`)."""
    _df, _rules = _load_heatmap_frame()
    if _df is None:
        return
    # Selectable columns: the tunable rule params + the (rule-less) category scores, which
    # are colorable as-is via rule_for's `*_score` fallback even though they aren't rules.
    _cols = set(_df.columns)
    _opts: list[str] = []
    for _keys in _SR.RULE_CATEGORIES.values():
        _opts += [k for k in _keys if k in _cols]
    _opts += [k for k in _SR.SCORE_COLUMNS if k in _cols and k not in _opts]
    # Default the columns to what the Output table showed (picked, order preserved),
    # keeping only metrics that are scorable here; fall back to the fixed defaults.
    _picked = [k for k in (picked or []) if k in _opts]
    _default = _picked or [k for k in _HEAT_DEFAULTS if k in _cols] or _opts[:8]
    _heatmap_core(
        symbols, _df, _rules, _opts, _default, key="heatmap",
        title="Metrics heat map", metric_label="Metrics (columns)")


def _render_scores_heatmap(symbols: list[str]) -> None:
    """Symbols × the category scores + Overall + RS Rank, colored by strength. Same grid +
    click-to-sort as the metrics heat map, but a fixed score-column set (not Output params)."""
    _df, _rules = _load_heatmap_frame()
    if _df is None:
        return
    _cols = set(_df.columns)
    _opts = [k for k in (list(_SR.SCORE_COLUMNS) + ["rs_rank"]) if k in _cols]
    if not _opts:
        st.warning("No score columns found — run an analysis first (Fetch Control).")
        return
    _heatmap_core(
        symbols, _df, _rules, _opts, _opts, key="scores_heatmap",
        title="Scores heat map", metric_label="Scores (columns)")


def _heatmap_core(symbols: list[str], _df, _rules, _opts: list[str], _default: list[str],
                  *, key: str, title: str, metric_label: str) -> None:
    """Shared symbols × metrics heat map: column picker, rule-goodness coloring and
    click-a-column-to-sort-rows. `key` namespaces the widget/session-state so two heat
    maps don't collide. `_opts`/`_default` define the column pool + initial selection."""
    st.subheader(title)
    _name = _heat_name

    # Column picker = the shared popover param browser (search + category groups + per-row
    # ▸ info from param_hints), same as the Filter / Output pickers. Multi-select: clicking
    # a row toggles it; selected rows are primary-styled. Selection lives in session_state.
    _cols_key = f"{key}_cols"
    if _cols_key not in st.session_state:
        st.session_state[_cols_key] = list(_default)
    st.session_state[_cols_key] = [k for k in st.session_state[_cols_key] if k in _opts]

    def _toggle_col(k: str) -> None:
        sel = st.session_state[_cols_key]
        sel.remove(k) if k in sel else sel.append(k)

    P.render(
        st.container(),
        opt_keys=_opts,
        label=f"📊  {metric_label}",
        keyp=f"{key}_pk",
        category_of=lambda k: (param_hints.get_hint(k) or {}).get("category") or "Other",
        name_of=_name,
        info_html_of=lambda k: param_hints.hint_html(
            k, fallback={"name": _name(k), "category": "", "unit": ""}),
        search_text_of=lambda k: f"{_name(k)} {k}".lower(),
        is_selected=lambda k: k in st.session_state[_cols_key],
        on_pick=_toggle_col,
        close_on_pick=False,
        exclude_selected=False,   # show every option; selected ones primary-styled
        trigger_width="content",
    )
    P.scroll_to_current()

    _metrics = [k for k in st.session_state[_cols_key] if k in _opts]
    if not _metrics:
        st.info("Pick at least one column from the picker above.")
        return
    st.caption("Columns: " + ", ".join(_name(k) for k in _metrics))

    _idx = _df.set_index("symbol")
    _have = [s for s in symbols if s in _idx.index]
    _missing = [s for s in symbols if s not in _idx.index]
    if _missing:
        st.caption("No analysis row for: " + ", ".join(_missing))
    if not _have:
        st.warning("None of the selected symbols have an analysis row.")
        return

    # Goodness per metric over the universe, then pick out the charted symbols.
    _good = {m: _SR.metric_goodness(_df, m, _rules) for m in _metrics}
    _good = {m: g.set_axis(_df["symbol"].values) for m, g in _good.items()}

    _units = {m: (param_hints.get_hint(m) or {}).get("unit", "") for m in _metrics}
    _verdicts = {m: _SR.verdict(0, _SR.rule_for(m, _rules) or {}) for m in _metrics}

    # Row order: click a column header to sort the symbols by that metric's strength
    # (single-column; click again flips direction). Stored in session_state; the click
    # itself is captured from the chart's xAxis-label event below. NaN strength sorts last.
    _sort_key, _t_key = f"{key}_sort", f"{key}_sort_t"
    _sort = st.session_state.get(_sort_key)
    if _sort and _sort.get("col") not in _metrics:   # selected metric removed → drop the sort
        _sort = st.session_state[_sort_key] = None
    _topdown = list(_have)                            # default = selection order, top→bottom
    if _sort:
        _scol, _desc = _sort["col"], _sort["desc"]

        def _skey(sym: str) -> tuple:
            v = _good[_scol].get(sym)
            if pd.isna(v):
                return (1, 0.0)                       # NaN always last, both directions
            return (0, -float(v) if _desc else float(v))
        _topdown = sorted(_have, key=_skey)

    # x-axis labels carry a ▼/▲ on the active sort column; the click returns the DISPLAYED
    # label, so map displayed-label → metric from these exact strings.
    def _xlabel(m: str) -> str:
        base = _name(m)
        if _sort and _sort["col"] == m:
            base += "  " + ("▼" if _sort["desc"] else "▲")
        return base
    _xlabels = [_xlabel(m) for m in _metrics]
    _metric_of_label = {lab: m for lab, m in zip(_xlabels, _metrics)}

    # ECharts heatmap data: [xMetricIdx, ySymbolIdx, goodness] + raw/verdict for tooltip.
    # y is drawn bottom→top, so reverse the top→bottom order for the axis.
    _ysyms = list(reversed(_topdown))
    _data = []
    for _xi, _m in enumerate(_metrics):
        _col = pd.to_numeric(_idx[_m], errors="coerce")
        for _yi, _sym in enumerate(_ysyms):
            _g = _good[_m].get(_sym)
            _raw = _col.get(_sym)
            _data.append({
                "value": [_xi, _yi, round(float(_g), 1) if pd.notna(_g) else None],
                "raw": (f"{float(_raw):,.2f}{_units[_m]}" if pd.notna(_raw) else "—"),
                "verdict": _verdicts[_m],
            })

    _small = len(_metrics) * len(_ysyms) <= 120
    _fmt = JsCode(
        "function(p){"
        "var m=" + repr([_name(m) for m in _metrics]) + ";"
        "var s=" + repr(_ysyms) + ";"
        "var g=p.data.value[2];"
        "return '<b>'+s[p.data.value[1]]+'</b> · '+m[p.data.value[0]]+'<br/>'"
        "+'Value: '+p.data.raw+'<br/>'"
        "+'Strength: '+(g==null?'—':g)+' / 100<br/>'"
        "+'<span style=\"opacity:.7\">'+p.data.verdict+'</span>';}"
    ).js_code

    _options = {
        "backgroundColor": _DARK_BG,
        "textStyle": {"color": _DARK_TEXT},
        "tooltip": _tooltip_style(position="top", formatter=_fmt),
        "grid": {"left": 8, "right": 24, "top": 8, "bottom": 90, "containLabel": True},
        "xAxis": {"type": "category", "data": _xlabels, "position": "top",
                  "triggerEvent": True,  # make the column labels clickable (sort the rows)
                  "splitArea": {"show": True},
                  "axisLabel": {"color": _DARK_TEXT, "rotate": 0, "interval": 0,
                                "fontSize": 11},
                  "axisLine": {"lineStyle": {"color": "rgba(255,255,255,0.35)"}}},
        "yAxis": {"type": "category", "data": _ysyms, "splitArea": {"show": True},
                  "axisLabel": {"color": _DARK_TEXT},
                  "axisLine": {"lineStyle": {"color": "rgba(255,255,255,0.35)"}}},
        "visualMap": {"min": 0, "max": 100, "calculable": True, "orient": "horizontal",
                      "left": "center", "bottom": 10, "itemWidth": 14, "itemHeight": 160,
                      "text": ["strong", "weak"], "textStyle": {"color": _DARK_TEXT},
                      "inRange": {"color": list(_HEAT_RAMP)}},
        "series": [{
            "type": "heatmap", "data": _data,
            "label": {"show": _small, "color": "#10131a", "fontSize": 10,
                      "formatter": JsCode("function(p){return p.data.raw;}").js_code},
            "itemStyle": {"borderColor": _DARK_BG, "borderWidth": 1},
            "emphasis": {"itemStyle": {"borderColor": "#fff", "borderWidth": 1.5}},
        }],
    }
    _height = max(320, 36 * len(_ysyms) + 150)
    # Capture a click on a column label. Date.now() makes each click a distinct return so
    # Streamlit always reruns; we dedup by that timestamp so a rerun doesn't re-toggle.
    _ret = st_echarts(
        options=_options, height=f"{_height}px", key=key,
        events={"click": "function(p){ return p.componentType==='xAxis' "
                         "? {col: p.value, t: Date.now()} : null; }"},
    )
    if isinstance(_ret, dict) and _ret.get("t") != st.session_state.get(_t_key):
        st.session_state[_t_key] = _ret.get("t")
        _clicked = _metric_of_label.get(_ret.get("col"))
        if _clicked:
            _prev = st.session_state.get(_sort_key)
            st.session_state[_sort_key] = (
                {"col": _clicked, "desc": not _prev["desc"]}
                if _prev and _prev["col"] == _clicked
                else {"col": _clicked, "desc": True})
            st.rerun()

    if _sort:
        st.caption(f"Sorted by **{_name(_sort['col'])}** "
                   f"({'strongest' if _sort['desc'] else 'weakest'} on top)")


def _symbol_selection_bar(symbols: list[str], *, keyp: str,
                          legend_key: str | None = None) -> None:
    """Save this chart's shown symbols to a .syms, or Add a saved set to turn its symbols
    on in the legend. Mirrors the Output screen's Save / Add Selection. `keyp` namespaces
    the buttons per view.

    `legend_key` is the session_state key of this view's legend on/off map ({name: bool}).
    Save writes only the VISIBLE symbols (legend on; absent from the map = shown). Add is
    ADDITIVE — it keeps the currently-shown lines on AND turns the loaded symbols on,
    without changing the chart's symbol set. It writes a COMPLETE map (every chart symbol
    gets an explicit on/off) so a freshly-mounted chart can't default absent symbols to
    shown (the "all get selected after Invert" bug).
    """
    _lmap = st.session_state.get(legend_key) or {} if legend_key else {}
    _save = [s for s in symbols if _lmap.get(s, True)]  # visible symbols only
    _c = st.columns([1.5, 1.5, 5], vertical_alignment="center")
    if _c[0].button("💾 Save selection", key=f"savesel_{keyp}", width="stretch",
                    disabled=not _save, help="Save the shown symbols to a .syms file"):
        p = SEL.save_dialog(kind="symbols", items=SEL.symbol_info(_save),
                            default_name="_".join(_save[:4]))
        if p:
            st.toast(f"Saved {p.name}")
    if _c[1].button("📂 Add Selection", key=f"addsel_{keyp}", width="stretch",
                    disabled=not legend_key,
                    help="Load a .syms and turn its symbols ON (keeps the shown lines on)"):
        data = SEL.load_dialog(kind="symbols")
        if data:
            loaded = {s.strip().upper() for s in data["items"].keys() if s.strip()}
            _on = [s for s in symbols if s in loaded]  # symbols on this chart in the file
            # ADDITIVE + COMPLETE map: every chart symbol explicit — currently shown stay
            # shown (absent from the map = shown), plus the loaded ones turned on. Writing
            # every key stops a freshly-mounted chart defaulting absent symbols to shown.
            _cur = st.session_state.get(legend_key) or {}
            _lmap = {s: (_cur.get(s, True) or s in loaded) for s in symbols}
            st.session_state[legend_key] = _lmap
            # Remount the chart (bump its key) so it reads the new legend selection fresh.
            # Without this the component re-applies its own stale last-event selection and
            # overrides our change — the same frontend-vs-programmatic clash as the Output
            # grid. A fresh mount returns no event, so the capture below is skipped.
            st.session_state["echarts_bump"] = st.session_state.get("echarts_bump", 0) + 1
            st.toast(f"Turned on {len(_on)} symbol{'' if len(_on) == 1 else 's'}")
            st.rerun()
    _c[2].caption("Save the shown symbols, or Add a saved set to turn those symbols on "
                  "(the shown lines stay on; the chart's symbol set doesn't change).")


# --------------------------------------------------------------------------- #
# view=news: aggregated, de-duplicated recent headlines for the selected symbols
# (yfinance + Polygon + finviz), one sortable table with clickable titles. Fetched
# on demand — NOT part of the main fetch runs — and cached briefly per tab.
# --------------------------------------------------------------------------- #
@st.cache_data(ttl=900, show_spinner=False)
def _load_news(symbols: tuple[str, ...], sources: tuple[str, ...]) -> pd.DataFrame:
    return _news.fetch_news(symbols, sources)


def _toggle_flag(key: str) -> None:
    """Flip a session boolean — for the self-managed per-symbol collapse headers
    (st.expander can mount a dataframe at 0-width / snap shut on interaction)."""
    st.session_state[key] = not st.session_state.get(key, False)


def _render_news(symbols: list[str]) -> None:
    st.subheader(f"📰 Latest news — {', '.join(symbols[:12])}"
                 + ("…" if len(symbols) > 12 else ""))
    sources = tuple(settings.NEWS_SOURCES)
    with st.spinner("Fetching news… (Polygon is rate-limited to 5/min, so larger "
                    "selections take a moment)"):
        df = _load_news(tuple(symbols), sources)
    if df.empty:
        st.info("No recent news found for the selected symbols.")
        return
    # Code-only relevance split: does the article name/center on the company, or is it
    # about its broader environment (sector / peers / market)? Uses Company name etc.
    sym_meta = SEL.symbol_info(list(dict.fromkeys(symbols)))
    df = _news.classify_relevance(df, sym_meta)
    st.caption(f"Sources: {', '.join(sources)} · newest first · duplicate stories "
               "merged · split into company-specific vs broader-context news")

    # One table per symbol, in the selection's order. The article URL is its own narrow
    # 🔗 column — only that icon is the clickable link (LinkColumn with a constant icon:
    # no parentheses, so Streamlit shows it literally, not as a regex). The Title beside
    # it is plain, non-clickable text. A clean URL in its own column also avoids the
    # invalid-URL breakage you get from embedding the headline into the link.
    col_cfg = {
        "Open": st.column_config.LinkColumn("🔗", display_text="🔗", width=48),
        "Published": st.column_config.DatetimeColumn(
            "Published (UTC)", format="YYYY-MM-DD HH:mm", width="small"),
        "Title": st.column_config.TextColumn("Title", width="large"),
        "Publisher": st.column_config.TextColumn("Publisher", width="small"),
        "Sentiment": st.column_config.TextColumn("Sentiment", width="small"),
    }
    order = ["Open", "Title", "Published", "Publisher", "Sentiment"]

    def _table(rows: pd.DataFrame) -> None:
        disp = rows.drop(columns=["Symbol"]).rename(columns={"Url": "Open"})
        st.dataframe(disp, hide_index=True, width="stretch",
                     column_config=col_cfg, column_order=order)

    for sym in dict.fromkeys(symbols):  # selection order, de-duplicated
        sub = df[df["Symbol"] == sym]
        # Self-managed collapse: a header button toggling a session flag (collapsed by
        # default), body under `if is_open` — not st.expander (see _toggle_flag).
        open_key = f"news_open_{sym}"
        is_open = st.session_state.setdefault(open_key, False)
        st.button(f"{'▾' if is_open else '▸'}  {sym}  ·  {len(sub)} article(s)",
                  key=f"news_tgl_{sym}", on_click=_toggle_flag, args=(open_key,),
                  width="stretch")
        if not is_open:
            continue
        if sub.empty:
            st.caption("No recent news.")
            continue
        about = sub[sub["Relevance"] == "Company"]
        context = sub[sub["Relevance"] == "Context"]
        st.markdown(f"**About {sym}**")
        if about.empty:
            st.caption("No company-specific articles.")
        else:
            _table(about)
        st.markdown("**Broader context** (sector · peers · market)")
        if context.empty:
            st.caption("None.")
        else:
            _table(context)

    # --- PDF report: the same news, same order, headlines as clickable links ---
    st.divider()
    if st.button("📄 Generate news PDF", key="news_pdf_gen"):
        pdf = reporting.generate("news", df=df, sources=list(sources),
                                 order=list(dict.fromkeys(symbols)), sym_meta=sym_meta)
        path = reporting.store.save(pdf, name="News Report")
        st.session_state["news_pdf"] = {"bytes": pdf, "path": str(path)}

    made = st.session_state.get("news_pdf")
    if made:
        st.success(f"Saved to {made['path']}")
        st.download_button("⬇️ Download PDF", data=made["bytes"],
                           file_name=Path(made["path"]).name, mime="application/pdf",
                           key="news_pdf_dl")

    # --- AI news reports: scrape the "About" article bodies into per-symbol .md ---
    if st.button("📝 Generate AI news reports", key="ai_news_gen"):
        with st.spinner("Scraping article text… (some publishers block scraping)"):
            paths = ai_news_report.generate_reports(
                df, order=list(dict.fromkeys(symbols)), sym_meta=sym_meta)
        st.session_state["ai_news_paths"] = [str(p) for p in paths]

    ai_paths = st.session_state.get("ai_news_paths")
    if ai_paths:
        st.success(f"Wrote {len(ai_paths)} report(s) to {settings.AI_NEWS_REPORTS_DIR}")
        st.code("\n".join(Path(p).name for p in ai_paths), language="text")


# --------------------------------------------------------------------------- #
# view=filter_fail: per-symbol diagnostic — which filter blocks each symbol
# failed, and the actual value vs. threshold for each failure.
# --------------------------------------------------------------------------- #
@st.cache_data(show_spinner=False)
def _load_analysis_ff(symbols: tuple[str, ...], _mtime: float) -> pd.DataFrame:
    if not settings.ANALYSIS_DB.exists():
        return pd.DataFrame()
    db = Database(settings.ANALYSIS_DB)
    ph = ",".join("?" * len(symbols))
    return db.read("analysis", where=f"symbol IN ({ph})", params=list(symbols))


def _block_label(block: dict) -> str:
    """Readable string for one filter block including any OR children."""
    def _one(b: dict) -> str:
        col = resolve_column(b.get("param", "?"), b.get("window"), b.get("compare", "value"))
        op = b.get("op", "?")
        v, v2 = b.get("value", ""), b.get("value2", "")
        if op in ("is null", "is not null"):
            return f"{col} {op}"
        if op == "between":
            return f"{col} between {v} and {v2}"
        if op in ("is any of", "is none of"):
            items = list(v) if isinstance(v, (list, tuple)) else ([v] if v not in (None, "") else [])
            return f"{col} {op} [{', '.join(str(x) for x in items)}]"
        return f"{col} {op} {v}"

    parts: list[str] = []
    if is_complete(block):
        parts.append(_one(block))
    for child in block.get("or_children", []):
        if child.get("enabled", True) and is_complete(child):
            parts.append(_one(child))
    return "  OR  ".join(parts) if parts else "(no conditions)"


def _build_fail_report(
    df: pd.DataFrame,
    fset: dict,
    filt_path: Path,
    symbols: list[str],
) -> str:
    selected_types: list[str] = fset.get("selected_types", [])
    blocks: list[dict] = fset.get("blocks", [])
    SEP = "─" * 32

    L: list[str] = [
        "FILTER FAIL REPORT",
        "=" * 40,
        f"Filter:  {filt_path.name}",
        f"Symbols: {len(symbols)} selected",
        "",
    ]

    in_db: set[str] = (
        set(df["symbol"].tolist()) if not df.empty and "symbol" in df.columns else set()
    )
    not_in_db = [s for s in symbols if s not in in_db]

    if df.empty:
        L.append("No analysis data found for selected symbols.")
        if not_in_db:
            L.append(f"  Symbols not in DB: {', '.join(not_in_db)}")
        return "\n".join(L)

    if "screen_type" not in df.columns:
        df = df.copy()
        df["screen_type"] = df["security_type"] if "security_type" in df.columns else "standard"

    L += ["SCOPE", SEP]

    if selected_types:
        L.append(f"  Filter targets: {', '.join(selected_types)}")
        in_mask = df["screen_type"].isin(selected_types)
        df_scope = df[in_mask].copy()
        df_out = df[~in_mask]
        in_syms = df_scope["symbol"].tolist()
        out_syms = df_out["symbol"].tolist()
        if in_syms:
            L.append(f"  In scope:     {', '.join(in_syms)}  ({len(in_syms)})")
        if out_syms:
            t_map = dict(zip(df_out["symbol"], df_out["screen_type"]))
            L.append("  Out of scope: " + ", ".join(f"{s} ({t_map.get(s,'?')})" for s in out_syms))
    else:
        L.append("  No type filter — all symbols in scope")
        df_scope = df.copy()

    if not_in_db:
        L.append(f"  Not in DB:    {', '.join(not_in_db)}")
    L.append("")

    if df_scope.empty:
        L.append("No in-scope symbols to evaluate.")
        return "\n".join(L)

    L += ["RESULT  (in-scope symbols)", SEP]
    active = [b for b in blocks if b.get("enabled", True)]
    overall = evaluate(df_scope, active)
    pass_syms = df_scope.loc[overall, "symbol"].tolist()
    fail_syms = df_scope.loc[~overall, "symbol"].tolist()
    L.append(f"  Pass: {', '.join(pass_syms) if pass_syms else '(none)'}  ({len(pass_syms)})")
    L.append(f"  Fail: {', '.join(fail_syms) if fail_syms else '(none)'}  ({len(fail_syms)})")
    L.append("")

    n_dis = sum(1 for b in blocks if not b.get("enabled", True))
    L += ["BLOCK BREAKDOWN", SEP,
          f"{len(blocks)} block(s)  ·  {len(active)} enabled  ·  {n_dis} disabled", ""]

    def _fmt(v) -> str:
        if v is None:
            return "N/A"
        try:
            if pd.isna(v):
                return "N/A"
        except (TypeError, ValueError):
            pass
        return f"{v:.2f}" if isinstance(v, float) else str(v)

    for i, block in enumerate(blocks, 1):
        label = _block_label(block)
        if not block.get("enabled", True):
            L.append(f"[{i}] {label}  [disabled]")
            continue
        has_own = is_complete(block)
        has_child = any(
            c.get("enabled", True) and is_complete(c)
            for c in block.get("or_children", [])
        )
        if not has_own and not has_child:
            L.append(f"[{i}] {label}  [incomplete — skipped]")
            continue
        bm = _block_mask(df_scope, block)
        if bm is None:
            L.append(f"[{i}] {label}  [no conditions]")
            continue

        fail_b = df_scope.loc[~bm, "symbol"].tolist()
        pass_b = df_scope.loc[bm, "symbol"].tolist()
        L.append(f"[{i}] {label}")

        col = resolve_column(
            block.get("param", "?"), block.get("window"), block.get("compare", "value")
        )
        op_str = block.get("op", "?")
        v1, v2 = block.get("value", ""), block.get("value2", "")
        if op_str == "between":
            thresh = f"between {v1} and {v2}"
        elif op_str in ("is null", "is not null"):
            thresh = op_str
        elif op_str in ("is any of", "is none of"):
            items = list(v1) if isinstance(v1, (list, tuple)) else ([v1] if v1 not in (None, "") else [])
            thresh = f"{op_str} [{', '.join(str(x) for x in items[:5])}{'…' if len(items) > 5 else ''}]"
        else:
            thresh = f"{op_str} {v1}"

        col_ok = col in df_scope.columns
        if not fail_b:
            L.append("    All pass")
        else:
            for sym in fail_b:
                row = df_scope[df_scope["symbol"] == sym]
                actual = (_fmt(row.iloc[0][col]) if not row.empty and col_ok
                          else ("(column not in DB)" if not col_ok else "N/A"))
                L.append(f"    ✗  {sym:<8}  {col} = {actual}   (needs {thresh})")
            if pass_b:
                if len(pass_b) <= 8:
                    pv = []
                    for sym in pass_b:
                        row = df_scope[df_scope["symbol"] == sym]
                        pv.append(f"{sym} ({_fmt(row.iloc[0][col])})" if not row.empty and col_ok
                                  else sym)
                    L.append(f"    ✓  {',  '.join(pv)}")
                else:
                    L.append(f"    ✓  {len(pass_b)} symbols pass")
        L.append("")

    return "\n".join(L)


def _render_filter_fail(symbols: list[str]) -> None:
    st.subheader("🔍 Filter Fail Analysis")
    _key = "_".join(sorted(symbols))
    _ss_path = f"ff_path_{_key}"
    _ss_report = f"ff_report_{_key}"

    if st.button("📂 Select Filter File…", key=f"ff_pick_{_key}"):
        path = ask_open_path(
            initialdir=settings.FILTERS_DIR,
            filetypes=[("Filter files", "*.filt"), ("All files", "*.*")],
            title="Select filter to diagnose",
        )
        if path:
            st.session_state[_ss_path] = Path(path)
            st.session_state[_ss_report] = None

    filt_path: Path | None = st.session_state.get(_ss_path)
    if filt_path is None:
        st.info("Select a .filt file to see which conditions the selected symbols failed.")
        return

    report: str | None = st.session_state.get(_ss_report)
    if report is None:
        fset = load_filterset_from(filt_path)
        _mtime = settings.ANALYSIS_DB.stat().st_mtime if settings.ANALYSIS_DB.exists() else 0.0
        df = _load_analysis_ff(tuple(symbols), _mtime)
        report = _build_fail_report(df, fset, filt_path, symbols)
        st.session_state[_ss_report] = report

    st.caption(f"Filter: {filt_path.name}")
    st.text_area(
        "Filter Fail Report",
        value=report,
        height=600,
        disabled=True,
        key=f"ff_out_{_key}",
        label_visibility="collapsed",
    )


# --------------------------------------------------------------------------- #
# page
# --------------------------------------------------------------------------- #
st.title("Charts")

_raw = st.query_params.get("symbols", "")
symbols = [s.strip().upper() for s in _raw.split(",") if s.strip()]
view = st.query_params.get("view", "price")
# Param-driven views (heat map, fundamentals bar/line) default their picker to the
# columns the Output table had SHOWN — passed as ?cols=... (hidden columns excluded).
_cols_raw = st.query_params.get("cols", "")
picked_cols = [c.strip() for c in _cols_raw.split(",") if c.strip()]

if not symbols:
    st.info("Open this page from an **Output** run: select rows, then "
            "**Action → Normalized price chart**. It opens here in its own tab.")
    st.stop()

if view == "fundamentals_bar":
    _render_fundamentals_bar(symbols, picked_cols)
    st.stop()

if view == "fundamentals_line":
    _render_fundamentals_line(symbols, picked_cols)
    st.stop()

if view == "radar":
    _render_radar(symbols)
    st.stop()

if view == "dividend_line":
    _render_dividend_line(symbols)
    st.stop()

if view == "heatmap":
    _render_heatmap(symbols, picked_cols)
    st.stop()

if view == "scores_heatmap":
    _render_scores_heatmap(symbols)
    st.stop()

if view == "news":
    _render_news(symbols)
    st.stop()

if view == "filter_fail":
    _render_filter_fail(symbols)
    st.stop()

if view != "price":
    st.info(f"Chart view '{view}' isn't built yet — the normalized price chart, the "
            "fundamentals bar + growth-line charts, the category-scores radar, the "
            "dividend-yield growth line, the metrics + scores heat maps and the news "
            "table are available so far.")
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
    "tooltip": _tooltip_style(trigger="axis", order="valueDesc"),
    # Vertical scroll list pinned to the left column (grid.left is opened up to make
    # room); many symbols scroll with the page arrows. Same layout as the radar.
    # "All" turns every line on; "Invert" flips the selection (all-on -> all-off).
    "legend": _legend_style(
        [s["name"] for s in _series_opt if not s["name"].startswith("_")],
        selectorButtonGap=8),
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
    # Left margin opened up for the vertical legend column; top tightened now that the
    # legend no longer sits across the top.
    "grid": {"left": 96, "right": 18, "top": 16, "bottom": 78, "containLabel": True},
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
    _symbol_selection_bar(symbols, keyp="price", legend_key="chart_legend_sel")
    st.caption(_head)
    _ret = st_echarts(
        options=_options,
        events=_LEGEND_EVENTS,
        height=f"{_CHART_HEIGHT}px",
        key=f"echarts_price_{st.session_state.get('echarts_bump', 0)}")
    if isinstance(_ret, dict) and _ret:
        _cur = st.session_state.get("chart_legend_sel") or {}
        _merged = {**_cur, **_ret}
        if _merged != _cur:  # store the new on/off state and redraw with it applied
            st.session_state["chart_legend_sel"] = _merged
            st.rerun()

# Sector/industry selector — below the chart, collapsed by default, and a FRAGMENT:
# expand/collapse reruns only it (chart untouched), a selection triggers a full rerun.
if _tree:
    with _selector_host:
        _render_group_selector(_tree)
