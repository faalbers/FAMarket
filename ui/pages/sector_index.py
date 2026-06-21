"""
Sector / Industry Index Comparison page (ROADMAP 4.5 — indices consumption, standalone chart).

A normal sidebar page (registered in app.py, just above Parameters) — symbol-free, so
unlike the Charts views it isn't opened from an Output run. It reads the base-100 index
series that `analysis_layer/sector_index.py` builds on full analysis runs into indices.db.

Two View modes:
  * Sectors (default): overlay all 11 Yahoo sectors, each REBASED to 100 at the chosen
    period's start so their growth is directly comparable.
  * Industries: pick one sector, overlay that sector's industries with a Relative/Absolute
    toggle. Relative = industry − parent-sector + 100 (flat 100 line = the sector; above
    beats it) — the same idea as the price chart's relative-strength view. Absolute = each
    industry rebased to 100 at the window start.

Period presets set the window; the ECharts dataZoom slider picks sub-ranges.
"""

from __future__ import annotations

from datetime import date

import pandas as pd
import streamlit as st
from streamlit_echarts import st_echarts

from config import settings
from core.database import Database
from ui.chart_theme import (
    DARK_BG as _DARK_BG,
    DARK_TEXT as _DARK_TEXT,
    GRID_LINE as _GRID_LINE,
    echarts_points as _echarts_points,
    legend_style as _legend_style,
    tooltip_style as _tooltip_style,
)

_CHART_HEIGHT = 600  # px

# A group can hold more lines (Industrials = 24 industries) than the shared 7-color
# COLORWAY, so cycling it would repeat a hue across two series — bad for a comparison
# chart. Use an extended color-blind-safe qualitative set (Paul Tol bright + muted) so as
# many lines as possible get a distinct color (very large sectors still cycle the tail,
# but the legend + hover name each line and lines toggle off individually).
_SECTOR_COLORWAY = (
    "#33BBEE", "#EE7733", "#EE3377", "#009988", "#0077BB", "#CC3311", "#117733",
    "#882255", "#DDCC77", "#AA4499", "#44AA99", "#999933",
)


@st.cache_data(show_spinner=False)
def _load_indices(kind: str, _mtime: float) -> dict[str, pd.Series]:
    """Every base-100 level series of `kind` ('sector'|'industry') from indices.db:
    label -> date-indexed Series. Industry labels are "<Sector> | <Industry>".

    `_mtime` (the db file's mtime) is part of the cache key so a fresh analysis run
    invalidates the cache; it's otherwise unused.
    """
    if not settings.INDICES_DB.exists():
        return {}
    with Database(settings.INDICES_DB) as db:
        if not db.table_exists("sector_industry_index"):
            return {}
        df = db.read("sector_industry_index", where="kind = ?", params=[kind])
    if df.empty:
        return {}
    df["date"] = pd.to_datetime(df["date"], errors="coerce").dt.normalize()
    df["level"] = pd.to_numeric(df["level"], errors="coerce")
    df = df.dropna(subset=["date", "level"])
    out: dict[str, pd.Series] = {}
    for label, g in df.groupby("label", sort=True):
        s = pd.Series(g["level"].to_numpy(), index=g["date"])
        out[str(label)] = s[~s.index.duplicated(keep="last")].sort_index()
    return out


def _line(name: str, dates, values) -> dict:
    """One ECharts line series with the shared style + gap-break points."""
    return {"name": name, "type": "line", "showSymbol": False, "connectNulls": False,
            "lineStyle": {"width": 1.5}, "emphasis": {"focus": "series"},
            "data": _echarts_points(dates, values)}


def _baseline_marker(value: float = 100.0) -> dict:
    """A silent, legend-less series carrying a dashed horizontal reference line at `value`
    (the base-100 line). Name starts with '_' so it stays off the legend (always shown)."""
    return {
        "name": "_baseline", "type": "line", "data": [], "showSymbol": False,
        "silent": True, "tooltip": {"show": False},
        "markLine": {
            "silent": True, "symbol": "none", "label": {"show": False},
            "lineStyle": {"type": "dashed", "color": "rgba(230,230,230,0.55)", "width": 1.5},
            "data": [{"yAxis": value}],
        },
    }


def _end_value(line: dict) -> float:
    """A line's value at the right edge of the window — the last non-null y in its points
    (gap-break markers are null). Used to sort the legend/series leader-first. -inf if the
    line has no finite point (so it sinks to the bottom)."""
    for _, y in reversed(line["data"]):
        if y is not None:
            return float(y)
    return float("-inf")


def _rebased_line(name: str, s: pd.Series, start, end) -> dict | None:
    """One series rebased to 100 at its first bar inside the window (a series whose history
    starts mid-window bases there). None if it has < 2 points in the window or a zero base."""
    win = s[(s.index >= start) & (s.index <= end)]
    if len(win) < 2:
        return None
    base = float(win.iloc[0])
    if base == 0:
        return None
    return _line(name, win.index, win / base * 100.0)


def _relative_line(name: str, ind: pd.Series, sec: pd.Series, start, end) -> dict | None:
    """An industry plotted relative to its parent sector: `ind_norm − sec_norm + 100`, both
    rebased to 100 at their first shared date in the window (a literal difference, so a big
    underperformer can dip below 0). Ported from charts.py::_group_symbol_lines (industry
    plays the symbol's role, the sector index the benchmark's). None if < 2 shared points
    or a zero base."""
    ind = ind[(ind.index >= start) & (ind.index <= end)]
    sec = sec[(sec.index >= start) & (sec.index <= end)]
    common = ind.index.intersection(sec.index).sort_values()
    if len(common) < 2:
        return None
    i_base, s_base = float(ind.loc[common[0]]), float(sec.loc[common[0]])
    if i_base == 0 or s_base == 0:
        return None
    ind_norm = ind.loc[common] / i_base * 100.0
    vals = ind_norm - (sec.loc[common] / s_base * 100.0) + 100.0
    return _line(name, common, vals)


def _chart_options(series_opt: list[dict], yaxis_name: str) -> dict:
    """The shared ECharts options for both views: dark theme, vertical left scroll legend
    (with All/Invert), axis tooltip, zoom slider, restore. Only the series, the legend
    labels (derived from series names) and the y-axis name differ between callers."""
    return {
        "backgroundColor": _DARK_BG,
        "color": list(_SECTOR_COLORWAY),
        "textStyle": {"color": _DARK_TEXT},
        "tooltip": _tooltip_style(trigger="axis", order="valueDesc"),
        "legend": _legend_style(
            [s["name"] for s in series_opt if not s["name"].startswith("_")]),
        "toolbox": {"right": 10, "top": 2, "iconStyle": {"borderColor": _DARK_TEXT},
                    "emphasis": {"iconStyle": {"borderColor": "#33BBEE"}},
                    "feature": {"dataZoom": {"yAxisIndex": "none"}, "restore": {}}},
        # Wide left margin — the legend holds full sector / industry names, not short tickers.
        "grid": {"left": 170, "right": 18, "top": 16, "bottom": 78, "containLabel": True},
        "xAxis": {"type": "time",
                  "axisLine": {"lineStyle": {"color": "rgba(255,255,255,0.35)"}},
                  "axisLabel": {"color": _DARK_TEXT},
                  "splitLine": {"show": True, "lineStyle": {"color": _GRID_LINE}}},
        "yAxis": {"type": "value", "name": yaxis_name, "scale": True,
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
        "series": series_opt,
    }


def _render_chart(series_opt: list[dict], yaxis_name: str, caption: str) -> None:
    """Render one chart + persist the legend show/hide selection across reruns (period /
    mode / sector switches), the same legendselectchanged round-trip the Charts views use.
    The caller resets `secidx_legend_sel` when the view context changes (see the page body),
    so a different series set starts with all lines shown."""
    options = _chart_options(series_opt, yaxis_name)
    legend_sel = st.session_state.get("secidx_legend_sel")
    if legend_sel:
        options["legend"]["selected"] = legend_sel
    ret = st_echarts(
        options=options,
        events={"legendselectchanged": "function(p){ return p.selected; }"},
        height=f"{_CHART_HEIGHT}px", key="sector_index")
    if isinstance(ret, dict) and ret:
        cur = st.session_state.get("secidx_legend_sel") or {}
        merged = {**cur, **ret}
        if merged != cur:
            st.session_state["secidx_legend_sel"] = merged
            st.rerun()
    st.caption(caption)


# --------------------------------------------------------------------------- #
# page
# --------------------------------------------------------------------------- #
st.title("Sector Indices")

_mtime = settings.INDICES_DB.stat().st_mtime if settings.INDICES_DB.exists() else 0.0
_sector_map = _load_indices("sector", _mtime)
if not _sector_map:
    st.warning("No sector indices found — indices are built on **full** analysis runs "
               "only (Fetch Control). Run one, then reopen this page.")
    st.stop()

# Period presets set the loaded window; "Max" spans the full history of the plotted series.
# The ECharts dataZoom slider/wheel pick arbitrary sub-ranges within it. Rendered BEFORE the
# View controls so the Industries sector picker can be ordered by sector performance.
_today = pd.Timestamp(date.today())
_pr = st.columns([0.5, 6], vertical_alignment="center")
_pr[0].markdown("**Period:**")
_period = _pr[1].radio("Period", ["1Y", "3Y", "5Y", "Max"], index=1,
                       horizontal=True, label_visibility="collapsed")
_years = {"1Y": 1, "3Y": 3, "5Y": 5}.get(_period)
_end = _today
_sector_start = (min(s.index.min() for s in _sector_map.values())
                 if _years is None else _today - pd.DateOffset(years=_years))


# Sectors ranked by their value at the window's right edge, descending — the SAME order the
# Sectors chart legend uses. Reused for the Sectors series and to order the Industries sector
# picker, so the dropdown lists strongest-first to match the chart.
def _sector_end(label: str) -> float:
    _ln = _rebased_line(label, _sector_map[label], _sector_start, _end)
    return _end_value(_ln) if _ln is not None else float("-inf")


_sector_rank = sorted(_sector_map, key=_sector_end, reverse=True)

# View mode (default Sectors). In Industries mode: a sector picker + a Relative/Absolute
# toggle. Segmented controls match the price chart's chart_grp_mode (red-active is fine).
_view = st.segmented_control("View", ["Sectors", "Industries"], default="Sectors",
                             key="secidx_view") or "Sectors"

_sector = _rel = None
_ind_map: dict[str, pd.Series] = {}
_by_sector: dict[str, list[str]] = {}
if _view == "Industries":
    _ind_map = _load_indices("industry", _mtime)
    for _lbl in _ind_map:
        _by_sector.setdefault(_lbl.split(" | ", 1)[0], []).append(_lbl)
    if not _by_sector:
        st.warning("No industry indices found — rerun a full analysis (Fetch Control).")
        st.stop()
    # Order the picker strongest-first, the same ranking the Sectors chart uses (so the
    # dropdown matches the sectors view); sectors without industries are simply absent.
    _ordered_sectors = [s for s in _sector_rank if s in _by_sector]
    _ctrl = st.columns([3, 3], vertical_alignment="bottom")
    _sector = _ctrl[0].selectbox("Sector", _ordered_sectors, key="secidx_sector")
    _rel = _ctrl[1].segmented_control("Scale", ["Relative", "Absolute"], default="Absolute",
                                      key="secidx_rel") or "Absolute"

# Reset the legend show/hide selection whenever the view context changes (mode / sector /
# scale), so a different series set starts with every line shown (mirrors charts.py's
# chart_grp_last reset). A period change leaves the context unchanged, so toggles persist.
_ctx = (_view, _sector, _rel)
if _ctx != st.session_state.get("secidx_ctx"):
    st.session_state["secidx_ctx"] = _ctx
    st.session_state.pop("secidx_legend_sel", None)

if _view == "Sectors":
    _start = _sector_start
    _series_opt = [ln for lbl, s in _sector_map.items()
                   if (ln := _rebased_line(lbl, s, _start, _end)) is not None]
    _yaxis = "Indexed (100)"
    _empty = "No sector index data in the selected window — try a longer period."
    _tail = "rebased to 100 at the window start."
else:
    _labels = sorted(_by_sector[_sector])
    _sec_series = _sector_map.get(_sector)
    # "Max" start = earliest bar across the chosen industries (+ the sector, for Relative).
    _pool = [_ind_map[l] for l in _labels] + ([_sec_series] if _sec_series is not None else [])
    _start = (min(s.index.min() for s in _pool)
              if _years is None else _today - pd.DateOffset(years=_years))
    _short = lambda l: l.split(" | ", 1)[1]  # legend = the industry part only  # noqa: E731
    if _rel == "Relative":
        if _sec_series is None:
            st.warning(f"No sector index for **{_sector}** — can't compute relatives.")
            st.stop()
        _series_opt = [ln for l in _labels
                       if (ln := _relative_line(_short(l), _ind_map[l], _sec_series,
                                                _start, _end)) is not None]
        _yaxis = "Relative to sector (100)"
        _tail = (f"relative to the **{_sector}** sector (flat 100 line = the sector; "
                 "above 100 beats it, below lags).")
    else:
        _series_opt = [ln for l in _labels
                       if (ln := _rebased_line(_short(l), _ind_map[l], _start, _end)) is not None]
        _yaxis = "Indexed (100)"
        _tail = "rebased to 100 at the window start."
    _empty = "No industry data in the selected window — try a longer period."

if not _series_opt:
    st.info(_empty)
    st.stop()
# Order the legend (left column) + series leader-first: highest value at the window's right
# edge at the top, descending — matches the tooltip's valueDesc order. Recomputed each
# period since the end value depends on the window.
_series_opt.sort(key=_end_value, reverse=True)
_series_opt = [*_series_opt, _baseline_marker(100.0)]

_n = len(_series_opt) - 1
_what = "sectors" if _view == "Sectors" else f"{_sector} industries"
_caption = f"{_n} {_what} · {_tail}"
_render_chart(_series_opt, _yaxis, _caption)
