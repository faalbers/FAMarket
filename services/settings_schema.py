"""
Declarative description of the UI-editable settings.

`config/settings.py` stays the committed DEFAULTS and is never written. The UI
saves only changed keys to the machine-local `settings.local.json`, which
`config/settings_overrides.py` lays over the defaults at import.

This schema is the one place a field's label, bounds and help text live, so the
front end renders whatever the backend declares rather than hardcoding a form.
Deliberately out of scope (file-only): paths, FRED series, external sites, and
the MA / peer metric tuples.
"""

from __future__ import annotations

from typing import Any, Literal

from config import settings

Kind = Literal["int", "float", "bool", "slider"]

# path -> hover help. Carried over verbatim from the Streamlit page so the
# wording people already know doesn't change.
HELP: dict[str, str] = {
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
    "ANALYSIS_OHLCV_LOOKBACK_DAYS": "Calendar days of daily price history the analysis loads per run (730 ≈ 2 years). Indicators need at most ~1 year; dividends/splits are always read in full. Raise it only if a new metric needs deeper price history — RAM use grows with it.",
    "RS_RANK_QUARTER_DAYS": "Length (trading days) of each of the four look-back windows in the RS-rank return (~63 ≈ 3 months).",
    "PEAK_PROMINENCE": "Minimum prominence (as a fraction of price) for find_peaks to count a swing high/low.",
    "PEAK_DISTANCE": "Minimum spacing (trading days) between two detected peaks.",
    "GROWTH_TREND_YEARS": "Recent annual points used for the growth trend-quality stats (R², residual volatility, CV).",
    "FETCH_LOCK_DAYS": "After a successful fetch, skip that symbol+fetcher for this many days.",
    "DEFAULT_BATCH_SIZE": "Rows fetched/committed per batch for batched APIs (some APIs override this in code).",
    "OHLCV_INITIAL_YEARS": "Years of price history pulled on a symbol's first OHLCV load.",
    "FETCH_ABANDONMENT_ENABLED": "Master switch for the abandonment policy. Off = a run touches every symbol, ignoring strikes and staleness.",
    "MAX_NO_DATA_FETCHES": "Abandon a symbol after this many consecutive fetches that return no data at all. Resets the moment data returns.",
    "OHLCV_STALE_WEEKS": "Stop fetching a symbol whose newest stored OHLCV bar is older than this many weeks.",
    "FINANCIALS_QUARTERLY_STALE_QUARTERS": "Stop fetching when the newest stored quarterly statement is older than this many quarters.",
    "FINANCIALS_YEARLY_STALE_QUARTERS": "Stop fetching when the newest stored annual statement is older than this many quarters.",
    "FINANCIALS_REPORT_LAG_DAYS": "Filing-window buffer for the financials due-date gate. SEC 10-Q deadline is 40-45 days.",
    "OHLCV_INACTIVE_AFTER_WEEKS": "Mark a symbol invalid (drop it from analysis) if its newest OHLCV bar is older than this.",
    "RETRY_MAX_ATTEMPTS": "Max attempts on a transient API failure before giving up on that call.",
    "RETRY_WAIT_SECONDS": "Wait between retry attempts on a transient API failure.",
    "GRAHAM_MULTIPLIER": "Graham number constant (22.5 = 15 P/E × 1.5 P/B); fair value = √(mult × EPS × book value per share).",
    "LYNCH_GROWTH_CAP": "Cap on growth % used as the Peter Lynch fair P/E (fair P/E = min(growth %, cap)).",
    "DCF_PROJECTION_YEARS": "Years of explicit free-cash-flow projection before the terminal value.",
    "DCF_TERMINAL_GROWTH": "Perpetual FCF growth rate after the projection horizon (decimal, e.g. 0.025 = 2.5%).",
    "DCF_EQUITY_RISK_PREMIUM": "Equity risk premium added to the risk-free rate via beta (CAPM) to set the discount rate (decimal).",
    "DCF_DEFAULT_BETA": "Beta used when a symbol has no reported beta (1.0 = moves with the market).",
    "DCF_FADE_START_CAP": "Upper bound on the year-1 growth rate the projection starts from, before it fades to terminal growth (decimal). Guards against corrupted trend inputs.",
    "DCF_MIN_DISCOUNT_SPREAD": "Floor on (discount rate − terminal growth) so the terminal value stays finite (decimal).",
    "MIN_PEERS_FOR_MEDIAN": "Fewest peers needed before a sector/industry median is trusted.",
    "MIN_PEERS_FOR_PERCENTILE": "Fewest peers needed to rank within the group; below this, scoring falls back to the whole universe.",
    "RECONCILE_TOLERANCE_PCT": "Max fractional gap between a computed ratio and yfinance's before a divergence WARNING is logged (0.10 = 10%).",
}


def _field(
    path: str,
    label: str,
    kind: Kind,
    *,
    lo: float | None = None,
    hi: float | None = None,
    step: float = 1,
    group: str = "",
    help_text: str = "",
) -> dict[str, Any]:
    return {
        "path": path,
        "label": label,
        "kind": kind,
        "min": lo,
        "max": hi,
        "step": step,
        "group": group,
        "help": help_text or HELP.get(path, ""),
        "value": current(path),
    }


def current(path: str) -> Any:
    """Live value of a (possibly nested) settings path, e.g. RATE_LIMITS.fred."""
    parts = path.split(".")
    value = getattr(settings, parts[0])
    for key in parts[1:]:
        value = value[int(key)] if isinstance(value, (list, tuple)) else value[key]
    return list(value) if isinstance(value, tuple) else value


def sections() -> list[dict[str, Any]]:
    """Every editable setting, grouped the way the page presents them."""
    scoring_weights = [
        _field(
            f"OVERALL_SCORE_WEIGHTS.{category}",
            category.capitalize(),
            "slider",
            lo=0.0,
            hi=1.0,
            step=0.05,
            group="Overall category weights",
            help_text=f"How much the {category.capitalize()} category counts toward the Overall Score.",
        )
        for category in settings.OVERALL_SCORE_WEIGHTS
    ]

    metric_weights = [
        _field(
            f"CATEGORY_METRIC_WEIGHTS.{category}.{metric}",
            metric,
            "float",
            lo=0.0,
            hi=2.0,
            step=0.05,
            group=category.capitalize(),
            help_text=f"Relative weight of '{metric}' within the {category} score. 0 removes it from the blend.",
        )
        for category, metrics in settings.CATEGORY_METRIC_WEIGHTS.items()
        for metric in metrics
    ]

    rate_limits = [
        field
        for api in settings.RATE_LIMITS
        for field in (
            _field(
                f"RATE_LIMITS.{api}.0",
                f"{api} — calls",
                "int",
                lo=1,
                group=api,
                help_text=f"Max {api} API calls allowed within each period window.",
            ),
            _field(
                f"RATE_LIMITS.{api}.1",
                f"{api} — period (s)",
                "int",
                lo=1,
                group=api,
                help_text=f"Length (seconds) of the {api} rate-limit window.",
            ),
        )
    ]

    rs_weights = [
        _field(
            f"RS_RANK_WEIGHTS.{i}",
            f"Q{i + 1}",
            "float",
            lo=0.0,
            hi=1.0,
            step=0.05,
            group="RS Rank — quarter weights (newest → oldest)",
            help_text="Weights applied to the four quarters when blending the trailing return.",
        )
        for i in range(len(settings.RS_RANK_WEIGHTS))
    ]

    return [
        {"section": "Scoring weights", "fields": scoring_weights},
        {"section": "Metric weights within each category", "fields": metric_weights},
        {
            "section": "Indicator parameters",
            "fields": [
                _field("ANALYSIS_OHLCV_LOOKBACK_DAYS", "OHLCV lookback (days)", "int", lo=400, hi=4000, group="Analysis window"),
                _field("RSI_PERIOD", "RSI period", "int", lo=2, hi=200, group="RSI"),
                _field("MACD_FAST", "Fast EMA", "int", lo=1, hi=200, group="MACD"),
                _field("MACD_SLOW", "Slow EMA", "int", lo=1, hi=400, group="MACD"),
                _field("MACD_SIGNAL", "Signal EMA", "int", lo=1, hi=200, group="MACD"),
                _field("MACD_CROSSOVER_PERSIST_DAYS", "Crossover persist (days)", "int", lo=1, hi=60, group="MACD"),
                _field("BOLLINGER_PERIOD", "Period", "int", lo=2, hi=200, group="Bollinger Bands"),
                _field("BOLLINGER_STD", "Std devs", "float", lo=0.5, hi=4.0, step=0.1, group="Bollinger Bands"),
                _field("ATR_PERIOD", "ATR period", "int", lo=2, hi=200, group="ATR & Volume"),
                _field("VOLUME_AVG_PERIOD", "Volume avg period", "int", lo=2, hi=400, group="ATR & Volume"),
                _field("RS_RANK_MIN_HISTORY_DAYS", "Min history (days)", "int", lo=20, hi=2000, group="Relative Strength"),
                _field("RS_RANK_QUARTER_DAYS", "Quarter length (days)", "int", lo=10, hi=200, group="Relative Strength"),
                *rs_weights,
                _field("PEAK_PROMINENCE", "Prominence", "float", lo=0.0, hi=1.0, step=0.005, group="Peak detection"),
                _field("PEAK_DISTANCE", "Min distance (days)", "int", lo=1, hi=250, group="Peak detection"),
                _field("GROWTH_TREND_YEARS", "Trend window (years)", "int", lo=2, hi=30, group="Growth trend"),
            ],
        },
        {
            "section": "Fetch behaviour & abandonment",
            "fields": [
                _field("FETCH_LOCK_DAYS", "Re-fetch lock (days)", "int", lo=0, hi=60, group="Scheduling & batching"),
                _field("DEFAULT_BATCH_SIZE", "Batch size", "int", lo=1, hi=2000, group="Scheduling & batching"),
                _field("OHLCV_INITIAL_YEARS", "OHLCV initial load (years)", "int", lo=1, hi=50, group="Scheduling & batching"),
                _field("FETCH_ABANDONMENT_ENABLED", "Policy enabled (master switch)", "bool", group="Abandonment"),
                _field("MAX_NO_DATA_FETCHES", "Max no-data fetches", "int", lo=1, hi=20, group="Abandonment"),
                _field("OHLCV_STALE_WEEKS", "OHLCV stale (weeks)", "int", lo=1, hi=104, group="Staleness"),
                _field("FINANCIALS_QUARTERLY_STALE_QUARTERS", "Quarterly stale (quarters)", "int", lo=1, hi=20, group="Staleness"),
                _field("FINANCIALS_YEARLY_STALE_QUARTERS", "Annual stale (quarters)", "int", lo=1, hi=40, group="Staleness"),
                _field("FINANCIALS_REPORT_LAG_DAYS", "Filing lag (days)", "int", lo=0, hi=120, group="Due-date gate"),
                _field("OHLCV_INACTIVE_AFTER_WEEKS", "OHLCV inactive after (weeks)", "int", lo=1, hi=104, group="Validation"),
                _field("RETRY_MAX_ATTEMPTS", "Max attempts", "int", lo=1, hi=10, group="Retries"),
                _field("RETRY_WAIT_SECONDS", "Wait between (seconds)", "float", lo=0.0, hi=60.0, step=0.5, group="Retries"),
            ],
        },
        {"section": "API rate limits", "fields": rate_limits},
        {
            "section": "Intrinsic value",
            "fields": [
                _field("GRAHAM_MULTIPLIER", "Graham multiplier", "float", lo=1.0, hi=100.0, step=0.5, group="Graham & Lynch"),
                _field("LYNCH_GROWTH_CAP", "Lynch growth cap", "float", lo=1.0, hi=100.0, step=1.0, group="Graham & Lynch"),
                _field("DCF_PROJECTION_YEARS", "Projection (years)", "int", lo=1, hi=30, group="DCF"),
                _field("DCF_TERMINAL_GROWTH", "Terminal growth", "float", lo=0.0, hi=0.1, step=0.005, group="DCF"),
                _field("DCF_EQUITY_RISK_PREMIUM", "Equity risk premium", "float", lo=0.0, hi=0.2, step=0.005, group="DCF"),
                _field("DCF_DEFAULT_BETA", "Default beta", "float", lo=0.0, hi=3.0, step=0.05, group="DCF"),
                _field("DCF_FADE_START_CAP", "Fade start cap", "float", lo=0.0, hi=3.0, step=0.05, group="DCF"),
                _field("DCF_MIN_DISCOUNT_SPREAD", "Min discount spread", "float", lo=0.0, hi=0.2, step=0.005, group="DCF"),
            ],
        },
        {
            "section": "Peer comparison & reconcile",
            "fields": [
                _field("MIN_PEERS_FOR_MEDIAN", "Min peers for median", "int", lo=1, hi=50, group="Peer comparison"),
                _field("MIN_PEERS_FOR_PERCENTILE", "Min peers for percentile", "int", lo=1, hi=50, group="Peer comparison"),
                _field("RECONCILE_TOLERANCE_PCT", "Tolerance (fraction)", "float", lo=0.0, hi=1.0, step=0.01, group="Reconcile"),
            ],
        },
    ]


def collapse(changes: dict[str, Any]) -> dict[str, Any]:
    """Turn indexed paths back into the tuples the settings module expects.

    `RATE_LIMITS.fred.0` and `RS_RANK_WEIGHTS.2` address one slot of a tuple;
    the override writer needs the WHOLE tuple, so rebuild it from the live value
    with the changed slots applied.
    """
    out: dict[str, Any] = {}
    tuples: dict[str, dict[int, Any]] = {}

    for path, value in changes.items():
        head, _, tail = path.rpartition(".")
        if head and tail.isdigit():
            tuples.setdefault(head, {})[int(tail)] = value
        else:
            out[path] = value

    for base, slots in tuples.items():
        existing = list(current(base))
        for index, value in slots.items():
            if 0 <= index < len(existing):
                existing[index] = value
        out[base] = tuple(existing)
    return out
