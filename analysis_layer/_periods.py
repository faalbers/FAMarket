"""
Period-series extraction (Analysis build #1) — the financials.db time-series layer.

Every fundamental metric ultimately reads a *series* of reported values out of
financials.db: annual revenue for a 5y CAGR, the last four quarters for a TTM
ratio, the latest balance-sheet equity for book value. This module is the single
place that turns the wide financials table into clean, sorted, NaN-free series,
so metrics.py / intrinsic_value.py never touch raw rows.

It is deliberately pure *extraction* — no growth math. CAGR, polyfit-residual
volatility, R², CV and YoY all live in metrics.py; here we only hand back the
series (and the one derived value, TTM, that needs reporting-structure knowledge
to compute correctly).

Two structural facts from the data drive the design:

  * Annual and quarterly rows can share a `period_end` (a fiscal-year-end quarter
    is filed as both). Always filter by `freq` first — TTM uses quarterly rows
    ONLY, or it double-counts the year-end.
  * Quarterly rows are discrete 3-month values (four of them sum to the fiscal
    year), so TTM = sum of the last four quarters — but only when those four are
    actually consecutive (a missing quarter makes the sum span >1 year and lie).

Flow vs stock: income-statement / cash-flow items are *flows* (TTM = sum of 4Q);
balance-sheet items are *stocks* (current value = latest reported, never summed).
The caller declares which via `current(..., kind=FLOW|STOCK)`.

Caveat: per-share fields (EPS) are reported split-UNADJUSTED, so an EPS series
crossing a stock split has a discontinuity. That is a metrics.py concern (prefer
net-income growth, or split-adjust); this layer returns values as filed.
"""

from __future__ import annotations

import pandas as pd

ANNUAL = "annual"
QUARTERLY = "quarterly"

FLOW = "flow"    # income-statement / cash-flow item — TTM is a 4-quarter sum
STOCK = "stock"  # balance-sheet item — current value is the latest reported

TTM_QUARTERS = 4
# A clean trailing-four-quarter window steps ~3 months between filings; a gap
# larger than this between any two of the four means a quarter is missing and the
# sum would silently span more than a year. ~120d tolerates 13/14-week fiscal
# quarters and slightly irregular filing dates without admitting a real gap.
_MAX_QUARTER_GAP_DAYS = 120


def by_symbol(fin: pd.DataFrame, symbol: str) -> pd.DataFrame:
    """The financials rows for one symbol (empty frame if the symbol has none)."""
    if fin.empty or "symbol" not in fin.columns:
        return fin.iloc[0:0]
    return fin[fin["symbol"] == symbol]


class SymbolPeriods:
    """One symbol's financials pre-split by freq, period_end-indexed and sorted.

    Built ONCE per symbol (see prepare()) so the ~50 P.* calls a metrics pass
    makes don't each re-filter by freq, re-parse period_end, and re-sort the
    same frame — that repetition profiled at ~2/3 of the whole analysis loop.
    """

    __slots__ = ("annual", "quarterly")

    def __init__(self, annual: pd.DataFrame, quarterly: pd.DataFrame):
        self.annual = annual
        self.quarterly = quarterly


_EMPTY = pd.DataFrame()


def prepare(fin: "pd.DataFrame | SymbolPeriods") -> SymbolPeriods:
    """Pre-split a symbol's financials rows by freq (idempotent).

    Uses the loader's vectorized `period_end_dt` column when present, else
    parses `period_end` here. Rows with an unparseable period_end are dropped.
    """
    if isinstance(fin, SymbolPeriods):
        return fin
    if fin.empty or "period_end" not in fin.columns or "freq" not in fin.columns:
        return SymbolPeriods(_EMPTY, _EMPTY)
    pe = (fin["period_end_dt"] if "period_end_dt" in fin.columns
          else pd.to_datetime(fin["period_end"], errors="coerce"))
    freqs = fin["freq"]
    split: dict[str, pd.DataFrame] = {}
    for freq in (ANNUAL, QUARTERLY):
        mask = (freqs == freq) & pe.notna()
        sub = fin.loc[mask].set_index(pd.DatetimeIndex(pe[mask]))
        split[freq] = sub.sort_index()
    return SymbolPeriods(split[ANNUAL], split[QUARTERLY])


def _series(fin: "pd.DataFrame | SymbolPeriods", freq: str, field: str) -> pd.Series:
    """Ascending period_end -> value for one freq+field; NaN/non-numeric dropped.

    Index is a DatetimeIndex. Empty Series when the symbol lacks the column or
    has no reported values for it (both are normal — coverage varies by symbol).
    """
    p = prepare(fin)
    df = p.annual if freq == ANNUAL else p.quarterly
    if df.empty or field not in df.columns:
        return pd.Series(dtype="float64")
    s = pd.to_numeric(df[field], errors="coerce").dropna()
    if s.empty:
        return pd.Series(dtype="float64")
    return s.astype("float64")


def annual(df: "pd.DataFrame | SymbolPeriods", field: str) -> pd.Series:
    """Annual reported values for `field`, oldest -> newest."""
    return _series(df, ANNUAL, field)


def quarterly(df: "pd.DataFrame | SymbolPeriods", field: str) -> pd.Series:
    """Quarterly reported values for `field`, oldest -> newest."""
    return _series(df, QUARTERLY, field)


def latest(df: "pd.DataFrame | SymbolPeriods", field: str, freq: str = QUARTERLY) -> float:
    """Most recent reported value (for a STOCK / balance-sheet item).

    Defaults to the quarterly series so the value is as current as filings allow,
    falling back to annual when no quarterly value exists for the field.
    """
    s = _series(df, freq, field)
    if len(s):
        return float(s.iloc[-1])
    if freq == QUARTERLY:  # fall back to annual for fields filed only yearly
        a = _series(df, ANNUAL, field)
        if len(a):
            return float(a.iloc[-1])
    return float("nan")


def ttm(df: "pd.DataFrame | SymbolPeriods", field: str) -> float:
    """Trailing-twelve-month sum of the last four quarters (for a FLOW item).

    NaN unless four quarterly values exist AND they form a consecutive window
    (no missing quarter), so the result is always a true 12-month figure.
    """
    s = quarterly(df, field)
    if len(s) < TTM_QUARTERS:
        return float("nan")
    last4 = s.iloc[-TTM_QUARTERS:]
    gaps = last4.index.to_series().diff().dropna().dt.days
    if (gaps > _MAX_QUARTER_GAP_DAYS).any():
        return float("nan")
    return float(last4.sum())


def current(df: "pd.DataFrame | SymbolPeriods", field: str, kind: str) -> float:
    """Current value of `field`: FLOW -> TTM sum, STOCK -> latest reported."""
    return ttm(df, field) if kind == FLOW else latest(df, field)


def latest_period_end(
    df: "pd.DataFrame | SymbolPeriods", freq: str = QUARTERLY
) -> pd.Timestamp | None:
    """Most recent reported period_end for a freq (for 'financials as of' display)."""
    p = prepare(df)
    sub = p.annual if freq == ANNUAL else p.quarterly
    return None if sub.empty else sub.index.max()
