"""
Settings page (Topic 3.4) — GUI over config/settings.py.

Both the UI and hand-edits write the SAME file: on Save, only the values that
changed are rewritten in place (comments/layout preserved), a versioned backup of
settings.py is taken first, and the live `settings` module is updated so the change
takes effect immediately (config/settings_io.py does the work).

Scope (first cut): the safe-to-edit knobs — scoring weights, indicator params,
fetch/abandonment, intrinsic value, peer comparison, reconcile tolerance, rate
limits. Paths and structural collections (FRED series, external sites, the MA / peer
metric tuples) stay file-only — they're rarely changed and risky to edit blind.
"""

from __future__ import annotations

import streamlit as st

from config import settings
from config.settings_io import SettingsWriteError, update_settings

# new_values accumulates every widget's current value, keyed by the dotted settings
# path; Save diffs it against the live settings and writes only what changed.
_new: dict[str, object] = {}

# Tooltip text per settings path — surfaced as the widget's hover `help`. One place
# to maintain every explanation; the render helpers pull from here automatically.
_HELP: dict[str, str] = {
    # Overall category weights
    "OVERALL_SCORE_WEIGHTS.quality": "How much the Quality category counts toward the Overall Score (profitability + balance-sheet strength).",
    "OVERALL_SCORE_WEIGHTS.growth": "How much the Growth category counts toward the Overall Score (revenue / EPS / FCF growth).",
    "OVERALL_SCORE_WEIGHTS.momentum": "How much the Momentum category counts toward the Overall Score (price trend, RS rank).",
    "OVERALL_SCORE_WEIGHTS.value": "How much the Value category counts toward the Overall Score (valuation multiples).",
    "OVERALL_SCORE_WEIGHTS.income": "How much the Income category counts toward the Overall Score (dividend yield / growth / safety).",
    # Indicators
    "RSI_PERIOD": "Look-back window (trading days) for the RSI momentum oscillator. Standard is 14.",
    "MACD_FAST": "Fast EMA length for MACD. Standard is 12.",
    "MACD_SLOW": "Slow EMA length for MACD. Standard is 26.",
    "MACD_SIGNAL": "Signal-line EMA length (EMA of the MACD line). Standard is 9.",
    "MACD_CROSSOVER_PERSIST_DAYS": "A MACD bull/bear crossover must hold this many days before it's flagged, to filter whipsaws.",
    "BOLLINGER_PERIOD": "Moving-average window (days) at the centre of the Bollinger Bands. Standard is 20.",
    "BOLLINGER_STD": "Band half-width in standard deviations above/below the moving average. Standard is 2.",
    "ATR_PERIOD": "Window (days) for Average True Range, a volatility measure. Standard is 14.",
    "VOLUME_AVG_PERIOD": "Window (days) for the average-volume baseline used in volume comparisons.",
    "RS_RANK_MIN_HISTORY_DAYS": "Minimum price history (days) required to compute RS rank; below this it's left blank (~1 year = 252).",
    "RS_RANK_QUARTER_DAYS": "Length (trading days) of each of the four look-back windows in the RS-rank return (~63 ≈ 3 months).",
    "RS_RANK_WEIGHTS": "Weights applied to the four quarters (newest → oldest) when blending the trailing return; heaviest on the most recent.",
    "PEAK_PROMINENCE": "Minimum prominence (as a fraction of price) for find_peaks to count a swing high/low.",
    "PEAK_DISTANCE": "Minimum spacing (trading days) between two detected peaks.",
    "GROWTH_TREND_YEARS": "Recent annual points used for the growth trend-quality stats (R², residual volatility, CV).",
    # Fetch behaviour
    "FETCH_LOCK_DAYS": "After a successful fetch, skip that symbol+fetcher for this many days (the weekly run falls outside the window).",
    "DEFAULT_BATCH_SIZE": "Rows fetched/committed per batch for batched APIs (some APIs override this in code).",
    "OHLCV_INITIAL_YEARS": "Years of price history pulled on a symbol's first OHLCV load.",
    "FETCH_ABANDONMENT_ENABLED": "Master switch for the abandonment policy. Off = a run touches every symbol, ignoring strikes and staleness.",
    "MAX_NO_DATA_FETCHES": "Abandon a symbol after this many consecutive fetches that return no data at all. Resets the moment data returns.",
    "OHLCV_STALE_WEEKS": "Stop fetching a symbol whose newest stored OHLCV bar is older than this many weeks.",
    "FINANCIALS_QUARTERLY_STALE_QUARTERS": "Stop fetching when the newest stored quarterly statement is older than this many quarters.",
    "FINANCIALS_YEARLY_STALE_QUARTERS": "Stop fetching when the newest stored annual statement is older than this many quarters.",
    "OHLCV_INACTIVE_AFTER_WEEKS": "Mark a symbol invalid (drop it from analysis) if its newest OHLCV bar is older than this. Distinct from the staleness probe, which stops fetching.",
    "RETRY_MAX_ATTEMPTS": "Max attempts (tenacity) on a transient API failure before giving up on that call.",
    "RETRY_WAIT_SECONDS": "Wait between retry attempts on a transient API failure.",
    # Intrinsic value
    "GRAHAM_MULTIPLIER": "Graham number constant (22.5 = 15 P/E × 1.5 P/B); fair value = √(mult × EPS × book value per share).",
    "LYNCH_GROWTH_CAP": "Cap on growth % used as the Peter Lynch fair P/E (fair P/E = min(growth %, cap)).",
    "DCF_PROJECTION_YEARS": "Years of explicit free-cash-flow projection before the terminal value.",
    "DCF_TERMINAL_GROWTH": "Perpetual FCF growth rate after the projection horizon (decimal, e.g. 0.025 = 2.5%).",
    "DCF_EQUITY_RISK_PREMIUM": "Equity risk premium added to the risk-free rate via beta (CAPM) to set the discount rate (decimal).",
    "DCF_DEFAULT_BETA": "Beta used when a symbol has no reported beta (1.0 = moves with the market).",
    "DCF_GROWTH_CAP": "Upper cap on historical FCF growth used in the projection, to avoid runaway valuations (decimal).",
    "DCF_MIN_DISCOUNT_SPREAD": "Floor on (discount rate − terminal growth) so the terminal value stays finite (decimal).",
    # Peer comparison & reconcile
    "MIN_PEERS_FOR_MEDIAN": "Fewest peers needed before a sector/industry median is trusted (below this it's too noisy).",
    "MIN_PEERS_FOR_PERCENTILE": "Fewest peers needed to rank within the group; below this, scoring falls back to the whole universe.",
    "RECONCILE_TOLERANCE_PCT": "Max fractional gap between a computed ratio and yfinance's before a divergence WARNING is logged (0.10 = 10%).",
}


def _cur(path: str) -> object:
    """Current value of a (possibly nested) settings path, e.g. RATE_LIMITS.fred."""
    parts = path.split(".")
    val = getattr(settings, parts[0])
    for key in parts[1:]:
        val = val[key]
    return val


def _int(col, path: str, label: str, *, min_value: int = 0, max_value: int | None = None, step: int = 1, help: str | None = None) -> None:
    col.number_input(
        label, value=int(_cur(path)), min_value=min_value, max_value=max_value, step=step,
        help=help or _HELP.get(path), key=f"set::{path}",
    )
    _new[path] = int(st.session_state[f"set::{path}"])


def _float(col, path: str, label: str, *, min_value: float = 0.0, max_value: float | None = None, step: float = 0.01, fmt: str = "%.3f", help: str | None = None) -> None:
    col.number_input(
        label, value=float(_cur(path)), min_value=min_value, max_value=max_value, step=step,
        format=fmt, help=help or _HELP.get(path), key=f"set::{path}",
    )
    _new[path] = float(st.session_state[f"set::{path}"])


def _bool(col, path: str, label: str, *, help: str | None = None) -> None:
    col.checkbox(label, value=bool(_cur(path)), help=help or _HELP.get(path), key=f"set::{path}")
    _new[path] = bool(st.session_state[f"set::{path}"])


def _slider(col, path: str, label: str, *, lo: float = 0.0, hi: float = 1.0, step: float = 0.05, help: str | None = None) -> None:
    col.slider(label, min_value=lo, max_value=hi, value=float(_cur(path)), step=step, help=help or _HELP.get(path), key=f"set::{path}")
    _new[path] = float(st.session_state[f"set::{path}"])


# --------------------------------------------------------------------------- #
st.title("Settings")
st.caption(
    "Edits write back to `config/settings.py` (a backup is taken first) and take "
    "effect immediately. Hand-edits to that file work just the same."
)

# -- Scoring weights -------------------------------------------------------- #
with st.expander("Scoring — Overall category weights", expanded=True):
    st.caption(
        "Weight of each category in the Overall Score. Renormalized across the "
        "categories a symbol actually has, so relative sizes are what matter."
    )
    cols = st.columns(len(settings.OVERALL_SCORE_WEIGHTS))
    for col, cat in zip(cols, settings.OVERALL_SCORE_WEIGHTS):
        _slider(col, f"OVERALL_SCORE_WEIGHTS.{cat}", cat.capitalize(), lo=0.0, hi=1.0, step=0.05)
    _total = sum(_new[f"OVERALL_SCORE_WEIGHTS.{c}"] for c in settings.OVERALL_SCORE_WEIGHTS)
    st.caption(f"Sum: **{_total:.2f}** (need not equal 1.0 — it's renormalized).")

with st.expander("Scoring — Metric weights within each category"):
    st.caption("Relative weight of each metric inside its category score (0 disables it).")
    for cat, metrics in settings.CATEGORY_METRIC_WEIGHTS.items():
        st.markdown(f"**{cat.capitalize()}**")
        items = list(metrics)
        for i in range(0, len(items), 3):
            row = st.columns(3)
            for col, metric in zip(row, items[i : i + 3]):
                _float(
                    col, f"CATEGORY_METRIC_WEIGHTS.{cat}.{metric}", metric,
                    min_value=0.0, max_value=2.0, step=0.05, fmt="%.2f",
                    help=f"Relative weight of '{metric}' within the {cat} score. 0 removes it from the blend.",
                )

# -- Indicator parameters (grouped by the indicator they drive) ------------- #
with st.expander("Analysis — Indicator parameters"):
    st.markdown("**RSI** — momentum oscillator")
    c1, c2, c3 = st.columns(3)
    _int(c1, "RSI_PERIOD", "Period", min_value=2, max_value=200)

    st.markdown("**MACD** — trend / crossover")
    c1, c2, c3 = st.columns(3)
    _int(c1, "MACD_FAST", "Fast EMA", min_value=1, max_value=200)
    _int(c2, "MACD_SLOW", "Slow EMA", min_value=1, max_value=400)
    _int(c3, "MACD_SIGNAL", "Signal EMA", min_value=1, max_value=200)
    _int(c1, "MACD_CROSSOVER_PERSIST_DAYS", "Crossover persist (days)", min_value=1, max_value=60)

    st.markdown("**Bollinger Bands** — volatility envelope")
    c1, c2, c3 = st.columns(3)
    _int(c1, "BOLLINGER_PERIOD", "Period", min_value=2, max_value=200)
    _float(c2, "BOLLINGER_STD", "Std devs", min_value=0.5, max_value=4.0, step=0.1, fmt="%.1f")

    st.markdown("**ATR & Volume** — range / liquidity")
    c1, c2, c3 = st.columns(3)
    _int(c1, "ATR_PERIOD", "ATR period", min_value=2, max_value=200)
    _int(c2, "VOLUME_AVG_PERIOD", "Volume avg period", min_value=2, max_value=400)

    st.markdown("**Relative Strength (RS Rank)** — weighted trailing-return percentile")
    c1, c2, c3 = st.columns(3)
    _int(c1, "RS_RANK_MIN_HISTORY_DAYS", "Min history (days)", min_value=20, max_value=2000)
    _int(c2, "RS_RANK_QUARTER_DAYS", "Quarter length (days)", min_value=10, max_value=200)
    wkey = "RS_RANK_WEIGHTS"
    wcur = tuple(_cur(wkey))
    st.caption("Quarter weights (newest → oldest)")
    wcols = st.columns(len(wcur))
    wnew = []
    for i, (col, w) in enumerate(zip(wcols, wcur)):
        col.number_input(f"Q{i + 1}", value=float(w), min_value=0.0, max_value=1.0, step=0.05, format="%.2f", help=_HELP.get(wkey), key=f"set::{wkey}.{i}")
        wnew.append(float(st.session_state[f"set::{wkey}.{i}"]))
    _new[wkey] = tuple(wnew)

    st.markdown("**Peak detection** — support/resistance pivots")
    c1, c2, c3 = st.columns(3)
    _float(c1, "PEAK_PROMINENCE", "Prominence", min_value=0.0, max_value=1.0, step=0.01)
    _int(c2, "PEAK_DISTANCE", "Min distance (days)", min_value=1, max_value=250)

    st.markdown("**Growth trend** — fundamentals trend window")
    c1, c2, c3 = st.columns(3)
    _int(c1, "GROWTH_TREND_YEARS", "Trend window (years)", min_value=2, max_value=30)

# -- Fetch / abandonment (grouped by purpose) ------------------------------- #
with st.expander("Fetch — behaviour & viability/abandonment"):
    st.markdown("**Scheduling & batching**")
    c1, c2, c3 = st.columns(3)
    _int(c1, "FETCH_LOCK_DAYS", "Re-fetch lock (days)", min_value=0, max_value=60)
    _int(c2, "DEFAULT_BATCH_SIZE", "Batch size", min_value=1, max_value=2000)
    _int(c3, "OHLCV_INITIAL_YEARS", "OHLCV initial load (years)", min_value=1, max_value=50)

    st.markdown("**Abandonment** — stop fetching symbols that stop producing data")
    _bool(st, "FETCH_ABANDONMENT_ENABLED", "Policy enabled (master switch)")
    st.caption("No-data strikes — for symbols that return nothing at all")
    c1, c2, c3 = st.columns(3)
    _int(c1, "MAX_NO_DATA_FETCHES", "Max no-data fetches before abandon", min_value=1, max_value=20)
    st.caption("Staleness — for symbols that return only old data")
    c1, c2, c3 = st.columns(3)
    _int(c1, "OHLCV_STALE_WEEKS", "OHLCV stale (weeks)", min_value=1, max_value=104)
    _int(c2, "FINANCIALS_QUARTERLY_STALE_QUARTERS", "Quarterly stale (quarters)", min_value=1, max_value=20)
    _int(c3, "FINANCIALS_YEARLY_STALE_QUARTERS", "Annual stale (quarters)", min_value=1, max_value=40)

    st.markdown("**Validation** — drop a symbol from the analysis universe")
    c1, c2, c3 = st.columns(3)
    _int(c1, "OHLCV_INACTIVE_AFTER_WEEKS", "OHLCV inactive after (weeks)", min_value=1, max_value=104)

    st.markdown("**Retries** — transient API failure (tenacity)")
    c1, c2, c3 = st.columns(3)
    _int(c1, "RETRY_MAX_ATTEMPTS", "Max attempts", min_value=1, max_value=10)
    _float(c2, "RETRY_WAIT_SECONDS", "Wait between (seconds)", min_value=0.0, max_value=60.0, step=0.5, fmt="%.1f")

with st.expander("Fetch — API rate limits (calls per period)"):
    st.caption("Max calls allowed per period (seconds) for each API's throttle.")
    for api in settings.RATE_LIMITS:
        calls, period = settings.RATE_LIMITS[api]
        c0, c1, c2 = st.columns([1, 1, 1])
        c0.markdown(f"**{api}**")
        c1.number_input("calls", value=int(calls), min_value=1, step=1, help=f"Max {api} API calls allowed within each period window.", key=f"set::RATE_LIMITS.{api}.calls")
        c2.number_input("period (s)", value=int(period), min_value=1, step=1, help=f"Length (seconds) of the {api} rate-limit window; calls are capped per window.", key=f"set::RATE_LIMITS.{api}.period")
        _new[f"RATE_LIMITS.{api}"] = (
            int(st.session_state[f"set::RATE_LIMITS.{api}.calls"]),
            int(st.session_state[f"set::RATE_LIMITS.{api}.period"]),
        )

# -- Intrinsic value (grouped by model) ------------------------------------- #
with st.expander("Analysis — Intrinsic value (DCF / Graham / Lynch)"):
    st.markdown("**Graham & Lynch** — fair-value rules of thumb")
    c1, c2, c3 = st.columns(3)
    _float(c1, "GRAHAM_MULTIPLIER", "Graham multiplier", min_value=1.0, max_value=100.0, step=0.5, fmt="%.1f")
    _float(c2, "LYNCH_GROWTH_CAP", "Lynch growth cap", min_value=1.0, max_value=100.0, step=1.0, fmt="%.1f")

    st.markdown("**Discounted Cash Flow (DCF)**")
    c1, c2, c3 = st.columns(3)
    _int(c1, "DCF_PROJECTION_YEARS", "Projection (years)", min_value=1, max_value=30)
    _float(c2, "DCF_TERMINAL_GROWTH", "Terminal growth", min_value=0.0, max_value=0.1, step=0.005)
    _float(c3, "DCF_EQUITY_RISK_PREMIUM", "Equity risk premium", min_value=0.0, max_value=0.2, step=0.005)
    _float(c1, "DCF_DEFAULT_BETA", "Default beta", min_value=0.0, max_value=3.0, step=0.05, fmt="%.2f")
    _float(c2, "DCF_GROWTH_CAP", "FCF growth cap", min_value=0.0, max_value=1.0, step=0.01)
    _float(c3, "DCF_MIN_DISCOUNT_SPREAD", "Min discount spread", min_value=0.0, max_value=0.2, step=0.005)

# -- Peer comparison & reconcile -------------------------------------------- #
with st.expander("Analysis — Peer comparison & reconcile"):
    st.markdown("**Peer comparison** — sector/industry baselines")
    c1, c2, c3 = st.columns(3)
    _int(c1, "MIN_PEERS_FOR_MEDIAN", "Min peers for median", min_value=1, max_value=50)
    _int(c2, "MIN_PEERS_FOR_PERCENTILE", "Min peers for percentile", min_value=1, max_value=50)

    st.markdown("**Reconcile** — computed-vs-yfinance ratio divergence")
    c1, c2, c3 = st.columns(3)
    _float(c1, "RECONCILE_TOLERANCE_PCT", "Tolerance (fraction)", min_value=0.0, max_value=1.0, step=0.01)

# -- Save ------------------------------------------------------------------- #
st.divider()
if st.button("💾 Save changes", type="primary"):
    changed: dict[str, object] = {}
    for path, value in _new.items():
        cur = _cur(path)
        if isinstance(cur, tuple):
            cur = tuple(cur)
        if value != cur:
            changed[path] = value
    if not changed:
        st.info("No changes to save.")
    else:
        try:
            update_settings(changed)
        except SettingsWriteError as exc:
            st.error(f"Save failed — settings.py was not modified. {exc}")
        else:
            st.success(f"Saved {len(changed)} change(s) to config/settings.py (versioned backup taken).")
            with st.expander("What changed", expanded=True):
                st.dataframe(
                    {"Setting": list(changed), "New value": [str(v) for v in changed.values()]},
                    hide_index=True, use_container_width=True,
                )
