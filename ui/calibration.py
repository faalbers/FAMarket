"""
Peak-detection calibration tool (ROADMAP Topic 4.2).

Originally its own sidebar page; now rendered as a section INSIDE the Settings page
(`ui/pages/settings_page.py` calls `render()`), since it tunes two `config/settings.py`
knobs and belongs with the other settings. Kept as a plain module of helpers + a single
`render()` entry point (no module-level Streamlit calls) so Settings can embed it.

The `trend` column in analysis.db is a peak-detection classification (HH/HL vs LL/LH on
swing highs/lows found by `scipy.signal.find_peaks`). Two knobs control how sensitive that
detection is — `PEAK_PROMINENCE` (min swing size, as a fraction of the window's mean price)
and `PEAK_DISTANCE` (min trading days between peaks).

This tool lets Frank slide those two knobs and watch the detected peaks move on a real
price chart, flip through a spread of representative stocks (clear-trend / choppy /
volatile / calm — chosen by price behavior, not the fundamental-growth R²), and Save the
values to `settings.local.json` (the same override file the rest of Settings writes). The
detection here calls the *same* `technical.trend_signals` the analysis pipeline uses, so
the peaks shown are exactly the ones a run would find.

Changing the knobs only affects the `trend` column on the NEXT analysis run — it does not
retroactively re-label existing rows.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import streamlit as st
from streamlit_echarts import st_echarts

from analysis_layer.technical import _TREND_WINDOW, trend_signals
from config import settings
from config import settings_overrides
from core.database import Database
from ui.chart_theme import (
    DARK_BG, DARK_TEXT, GRID_LINE, echarts_points, tooltip_style,
    LEGEND_ICON, LEGEND_ITEM_W, LEGEND_ITEM_H, LEGEND_INACTIVE,
)

_POOL_LIQUID = 300   # most-liquid equities to consider (avoids gappy penny names)
_POOL_FIT = 120      # of those, how many get a price-fit R² computed
_SAMPLE_DAYS = 400   # calendar days of history to load per candidate (>~252 sessions)
_EQUITY_TYPES = ("stock", "adr")  # real price action; mutual funds are flat NAV
_ATR_MAX = 25.0      # %; above this is a reverse-split / illiquid data artifact, not volatility
_HIGH_COLOR = "#33BBEE"
_LOW_COLOR = "#EE7733"
_CHART_HEIGHT = 540


# --------------------------------------------------------------------------- #
# data
# --------------------------------------------------------------------------- #
@st.cache_data(show_spinner="Picking representative stocks…")
def _pick_samples(_a_mtime: float, _o_mtime: float) -> list[dict]:
    """A spread of liquid equities chosen by price behavior, for calibration.

    Reads a liquid equity pool from analysis.db, computes a linear-fit R² on each
    one's recent price window (trend clarity), then picks the clearest trends, the
    choppiest, the most volatile (`atr_pct`) and the calmest. `_a_mtime`/`_o_mtime`
    (the db file mtimes) are cache keys so a fresh run repicks; otherwise unused.
    """
    if not settings.ANALYSIS_DB.exists() or not settings.OHLCV_DB.exists():
        return []
    with Database(settings.ANALYSIS_DB) as db:
        if not db.table_exists("analysis"):
            return []
        ana = db.read("analysis")
    keep = [c for c in ("symbol", "name", "security_type", "atr_pct", "vol_20d_avg")
            if c in ana.columns]
    ana = ana[keep].copy()
    ana = ana[ana["security_type"].isin(_EQUITY_TYPES)]
    for col in ("atr_pct", "vol_20d_avg"):
        ana[col] = pd.to_numeric(ana[col], errors="coerce")
    ana = ana.dropna(subset=["atr_pct", "vol_20d_avg"])
    ana = ana[(ana["vol_20d_avg"] > 0) & (ana["atr_pct"].between(0, _ATR_MAX))]
    if ana.empty:
        return []

    # Liquidity gate (recognizable, well-behaved series), then bound the fit cost.
    pool = ana.nlargest(_POOL_LIQUID, "vol_20d_avg")
    if len(pool) > _POOL_FIT:  # span the volatility range within the liquid pool
        idx = np.linspace(0, len(pool) - 1, _POOL_FIT).round().astype(int)
        pool = pool.sort_values("atr_pct").iloc[np.unique(idx)]
    syms = pool["symbol"].tolist()

    prices = _load_window(tuple(syms), settings.OHLCV_DB.stat().st_mtime)
    if prices.empty:
        return []
    r2 = {s: _fit_r2(g["adj_close"]) for s, g in prices.groupby("symbol", sort=False)}
    pool = pool.assign(r2=pool["symbol"].map(r2)).dropna(subset=["r2"])
    if pool.empty:
        return []

    picks: dict[str, str] = {}  # symbol -> tag (first tag wins on overlap)
    def _take(frame: pd.DataFrame, tag: str, n: int = 2) -> None:
        for sym in frame["symbol"].head(n):
            picks.setdefault(sym, tag)

    _take(pool.sort_values("r2", ascending=False), "clear trend")
    _take(pool.sort_values("r2", ascending=True), "choppy")
    _take(pool.sort_values("atr_pct", ascending=False), "volatile")
    _take(pool.sort_values("atr_pct", ascending=True), "calm")

    names = pool.set_index("symbol")["name"].to_dict()
    return [{"symbol": s, "name": names.get(s) or "", "tag": t} for s, t in picks.items()]


@st.cache_data(show_spinner=False)
def _load_window(symbols: tuple[str, ...], _mtime: float) -> pd.DataFrame:
    """Recent adj_close history (~_SAMPLE_DAYS) for `symbols` → (symbol, date, adj_close)."""
    if not symbols or not settings.OHLCV_DB.exists():
        return pd.DataFrame()
    cutoff = (pd.Timestamp.today() - pd.Timedelta(days=_SAMPLE_DAYS)).strftime("%Y-%m-%d")
    with Database(settings.OHLCV_DB) as db:
        if not db.table_exists("ohlcv"):
            return pd.DataFrame()
        ph = ",".join("?" * len(symbols))
        df = db.read("ohlcv", where=f"symbol IN ({ph}) AND date >= ?",
                     params=[*symbols, cutoff])
    if df.empty:
        return df
    df = df[["symbol", "date", "adj_close"]].copy()
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    df["adj_close"] = pd.to_numeric(df["adj_close"], errors="coerce")
    return (df.dropna(subset=["date", "adj_close"])
              .sort_values(["symbol", "date"], kind="stable"))


def _fit_r2(close: pd.Series) -> float:
    """R² of a straight-line fit over the trend window — how cleanly the price trends."""
    y = close.tail(_TREND_WINDOW).to_numpy(dtype=float)
    if len(y) < 2:
        return float("nan")
    x = np.arange(len(y), dtype=float)
    fit = np.polyfit(x, y, 1)
    resid = y - np.polyval(fit, x)
    ss_tot = float(np.sum((y - y.mean()) ** 2))
    return 1.0 - float(np.sum(resid ** 2)) / ss_tot if ss_tot > 0 else float("nan")


_TREND_LABELS = {
    "strong_uptrend": "🟢 Strong uptrend (higher highs **and** higher lows)",
    "weak_uptrend": "🟢 Weak uptrend (one of: higher high / higher low)",
    "sideways": "⚪ Sideways (no confirmed swing pattern)",
    "weak_downtrend": "🔴 Weak downtrend (one of: lower low / lower high)",
    "strong_downtrend": "🔴 Strong downtrend (lower lows **and** lower highs)",
}


# --------------------------------------------------------------------------- #
# render — called by the Settings page (inside an expander)
# --------------------------------------------------------------------------- #
def render() -> None:
    """The peak-detection calibrator UI. Embedded as a Settings section, so it uses early
    `return`s (not `st.stop()`) to bail without killing the rest of the Settings page."""
    _samples = _pick_samples(
        settings.ANALYSIS_DB.stat().st_mtime if settings.ANALYSIS_DB.exists() else 0.0,
        settings.SETTINGS_OVERRIDES_PATH.stat().st_mtime
        if settings.SETTINGS_OVERRIDES_PATH.exists() else 0.0,
    )
    if not _samples:
        st.warning(
            "No representative stocks yet — run an analysis first so `analysis.db` has "
            "equities with price history. You can still type a symbol below to inspect it."
        )

    # -- which symbol ----------------------------------------------------------
    if "calib_idx" not in st.session_state:
        st.session_state.calib_idx = 0
    st.session_state.calib_idx %= max(len(_samples), 1)

    _nav = st.columns([1, 4, 1], vertical_alignment="center")
    if _nav[0].button("◀ Prev", use_container_width=True, disabled=not _samples):
        st.session_state.calib_idx = (st.session_state.calib_idx - 1) % len(_samples)
    if _nav[2].button("Next ▶", use_container_width=True, disabled=not _samples):
        st.session_state.calib_idx = (st.session_state.calib_idx + 1) % len(_samples)

    _cur = _samples[st.session_state.calib_idx] if _samples else None
    if _cur:
        _nav[1].markdown(
            f"<div style='text-align:center'>Sample <b>{st.session_state.calib_idx + 1}"
            f"</b> of {len(_samples)} — <b>{_cur['symbol']}</b> "
            f"{('· ' + _cur['name']) if _cur['name'] else ''} "
            f"<span style='opacity:0.7'>({_cur['tag']})</span></div>",
            unsafe_allow_html=True,
        )

    _manual = st.text_input(
        "…or inspect any symbol", value="", placeholder="e.g. AAPL",
        help="Overrides the sample above while it has a value.",
    ).strip().upper()
    _symbol = _manual or (_cur["symbol"] if _cur else "")

    # -- knobs -----------------------------------------------------------------
    _k = st.columns(2)
    _prom = _k[0].slider(
        "PEAK_PROMINENCE — min swing size (× mean price)",
        min_value=0.005, max_value=0.20, step=0.005,
        value=float(settings.PEAK_PROMINENCE),
        help="Bigger = only large swings count as peaks (fewer, cleaner peaks). "
             "Scaled by the window's average price so it works across price levels.",
    )
    _dist = _k[1].slider(
        "PEAK_DISTANCE — min trading days between peaks",
        min_value=5, max_value=60, step=1, value=int(settings.PEAK_DISTANCE),
        help="Bigger = peaks must be further apart in time (filters out closely-spaced wiggles).",
    )

    # -- chart -----------------------------------------------------------------
    if not _symbol:
        st.info("Pick a sample or type a symbol to begin.")
        return

    _px = _load_window((_symbol,), settings.OHLCV_DB.stat().st_mtime
                       if settings.OHLCV_DB.exists() else 0.0)
    if _px.empty:
        st.warning(f"No price history found for **{_symbol}**.")
        return

    # The window the analysis actually judges the trend on — show exactly that, so the
    # peaks line up with what a run would compute.
    _win = _px.tail(_TREND_WINDOW).reset_index(drop=True)
    _highs, _lows, _label = trend_signals(_win["adj_close"], _prom, _dist)

    def _pts(idx):
        return [[_win["date"].iloc[i].strftime("%Y-%m-%d"),
                 round(float(_win["adj_close"].iloc[i]), 2)] for i in idx]

    st.markdown(f"### {_TREND_LABELS.get(_label, _label)}")
    st.caption(f"{len(_highs)} swing high(s) · {len(_lows)} swing low(s) detected over "
               f"the last {len(_win)} sessions. A trend needs ≥2 of each; otherwise it "
               f"falls back to *sideways*. **Swing-break:** a trend also reverts to "
               f"*sideways* once price closes below the last swing low (uptrend) or above "
               f"the last swing high (downtrend) — i.e. the trend broke after the last peak.")

    _options = {
        "backgroundColor": DARK_BG,
        "textStyle": {"color": DARK_TEXT},
        "tooltip": tooltip_style(trigger="axis"),
        "legend": {"top": 4, "data": ["Adj close", "Swing high", "Swing low"],
                   # Filled swatch + dimmed inactive so the on/off state is easy to read
                   # (matches ui.chart_theme.legend_style; this legend is horizontal so it
                   # can't use that vertical-scroll helper directly).
                   "icon": LEGEND_ICON, "itemWidth": LEGEND_ITEM_W, "itemHeight": LEGEND_ITEM_H,
                   "inactiveColor": LEGEND_INACTIVE, "inactiveBorderColor": LEGEND_INACTIVE,
                   "textStyle": {"color": DARK_TEXT}},
        "grid": {"left": 8, "right": 18, "top": 44, "bottom": 64, "containLabel": True},
        "xAxis": {"type": "time",
                  "axisLine": {"lineStyle": {"color": "rgba(255,255,255,0.35)"}},
                  "axisLabel": {"color": DARK_TEXT},
                  "splitLine": {"show": True, "lineStyle": {"color": GRID_LINE}}},
        "yAxis": {"type": "value", "scale": True, "name": "Adj close",
                  "nameTextStyle": {"color": DARK_TEXT},
                  "axisLabel": {"color": DARK_TEXT},
                  "splitLine": {"show": True, "lineStyle": {"color": GRID_LINE}}},
        "dataZoom": [{"type": "inside"},
                     {"type": "slider", "bottom": 8, "height": 22,
                      "borderColor": "rgba(255,255,255,0.18)",
                      "fillerColor": "rgba(51,187,238,0.18)",
                      "handleStyle": {"color": "#33BBEE"},
                      "textStyle": {"color": DARK_TEXT}}],
        "series": [
            {"name": "Adj close", "type": "line", "showSymbol": False,
             "connectNulls": False, "lineStyle": {"width": 1.5, "color": "#8899AA"},
             "itemStyle": {"color": "#8899AA"},
             "data": echarts_points(_win["date"], _win["adj_close"])},
            {"name": "Swing high", "type": "scatter", "symbol": "triangle",
             "symbolSize": 13, "itemStyle": {"color": _HIGH_COLOR}, "data": _pts(_highs)},
            {"name": "Swing low", "type": "scatter", "symbol": "triangle",
             "symbolRotate": 180, "symbolSize": 13, "itemStyle": {"color": _LOW_COLOR},
             "data": _pts(_lows)},
        ],
    }
    st_echarts(options=_options, height=f"{_CHART_HEIGHT}px", key="calib_chart")

    # -- save ------------------------------------------------------------------
    _changed = (_prom != float(settings.PEAK_PROMINENCE)
                or _dist != int(settings.PEAK_DISTANCE))
    _save = st.columns([1, 3], vertical_alignment="center")
    if _save[0].button("💾 Save calibration", type="primary", disabled=not _changed,
                       use_container_width=True):
        try:
            settings_overrides.update_settings(
                {"PEAK_PROMINENCE": float(_prom), "PEAK_DISTANCE": int(_dist)})
            st.success(f"Saved PEAK_PROMINENCE={_prom}, PEAK_DISTANCE={_dist}. "
                       "Applies on the next analysis run.")
        except settings_overrides.SettingsWriteError as exc:
            st.error(f"Could not save: {exc}")
    _save[1].caption(
        f"Current saved: prominence **{settings.PEAK_PROMINENCE}**, distance "
        f"**{settings.PEAK_DISTANCE}**.  Code default: 0.05 / 20."
        + ("" if _changed else "  _(sliders match the saved values)_"))
