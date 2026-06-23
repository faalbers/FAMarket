"""
Forward-looking analyst-estimate metrics (Topic 4.1 extension).

Reads each symbol's rows from **estimates.db** (tidy, one row per
(symbol, horizon)) and derives the forward signals the historical financials
can't give:

  * next-fiscal-year consensus growth (EPS + revenue),
  * a forward PEG (forward P/E over that forward growth),
  * analyst-revision **momentum** (how the current-year consensus EPS estimate
    has drifted over the last 1 / 3 months) and **breadth** (net analysts
    raising vs cutting) — empirically among the strongest predictors of forward
    returns, and the gap flagged as "biggest true gap" in the analysis guide.

Horizons present: 0q / +1q (quarters), 0y / +1y (fiscal years), LTG.

LTG CAVEAT: yfinance's `LTG` row carries ONLY the *index* long-term trend — a
single market-wide constant (~12% for every symbol), never a per-stock
`stockTrend`. So a genuine LTG-based PEG/Lynch is NOT derivable from this feed;
we use the **+1y (next fiscal year)** consensus growth as the forward rate
instead. See [[analyst-estimates-feature]].

Percent convention (see [[param-unit-percent-storage]]): yfinance growth fields
are fractions -> stored as percent numbers (0.10 -> 10.0). Revision-momentum
metrics are computed directly in percent. All inputs are currency-neutral
(growth rates, % drift, counts), so no currency gate is needed.
"""

from __future__ import annotations

import pandas as pd

# The horizon labels we read (yfinance's own index values, stored verbatim).
_FY0 = "0y"     # current fiscal year — the consensus being revised
_FY1 = "+1y"    # next fiscal year — the forward growth rate

# Columns this module emits, in display order — also the NaN template for symbols
# with no analyst coverage (funds, micro-caps), so the column set is stable.
COLUMNS = (
    "forward_eps_growth", "forward_rev_growth", "forward_peg",
    "eps_revision_1m", "eps_revision_3m", "eps_revision_breadth", "analyst_count",
)


def _at(est: pd.DataFrame | None, horizon: str, col: str) -> float:
    """One numeric cell at (horizon, col), or NaN when absent/blank/non-numeric."""
    if est is None or col not in est.columns or horizon not in est.index:
        return float("nan")
    v = pd.to_numeric(est.at[horizon, col], errors="coerce")
    return float(v) if pd.notna(v) else float("nan")


def _pct(x: float) -> float:
    """Fraction -> stored percent number (0.10 -> 10.0)."""
    return x * 100 if pd.notna(x) else float("nan")


def _revision_pct(current: float, past: float) -> float:
    """Percent drift of the consensus estimate vs `past` (abs denom keeps the sign
    right across loss-makers, NaN when the base is missing or zero)."""
    if pd.isna(current) or pd.isna(past) or past == 0:
        return float("nan")
    return (current - past) / abs(past) * 100


def compute(symbol: str, est: pd.DataFrame | None, *, forward_pe: float = float("nan")) -> dict:
    """All forward estimate metrics for one symbol.

    `est` is this symbol's estimates rows indexed by `horizon` (or None when the
    symbol has no estimates row — funds, ADRs without coverage, etc.); `forward_pe`
    is the snapshot's forward P/E (from metrics.compute) used for the forward PEG.
    Every output is NaN ("not applicable") when its inputs are missing.
    """
    out: dict[str, float] = {c: float("nan") for c in COLUMNS}
    if est is None or len(est) == 0:
        return out

    # -- forward growth (next fiscal year consensus) ------------------------- #
    out["forward_eps_growth"] = _pct(_at(est, _FY1, "eps_growth"))
    out["forward_rev_growth"] = _pct(_at(est, _FY1, "rev_growth"))

    # -- forward PEG: forward P/E over next-year EPS growth % (guard non-pos) - #
    g = out["forward_eps_growth"]
    if pd.notna(forward_pe) and pd.notna(g) and g > 0:
        out["forward_peg"] = forward_pe / g

    # -- revision momentum on the current-year (FY0) consensus EPS estimate -- #
    current = _at(est, _FY0, "eps_trend_current")
    out["eps_revision_1m"] = _revision_pct(current, _at(est, _FY0, "eps_trend_30d"))
    out["eps_revision_3m"] = _revision_pct(current, _at(est, _FY0, "eps_trend_90d"))

    # -- revision breadth: net analysts raising minus cutting (last 30 days) - #
    up = _at(est, _FY0, "eps_rev_up_30d")
    dn = _at(est, _FY0, "eps_rev_down_30d")
    if pd.notna(up) or pd.notna(dn):
        out["eps_revision_breadth"] = (0.0 if pd.isna(up) else up) - (0.0 if pd.isna(dn) else dn)

    # -- coverage: number of analysts behind the FY0 EPS estimate ------------ #
    out["analyst_count"] = _at(est, _FY0, "eps_num_analysts")
    return out
