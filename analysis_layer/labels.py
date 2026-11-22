"""
Company labels (fix list §21, Group H part 3, 2026-11-21).

`stock_category` — what kind of stock a company is: growth / income / both /
early / neither.

  * growth  — the average of past revenue-per-share growth (`revenue_ps_growth`)
              and the analysts' next-year revenue forecast (`forward_rev_growth`)
              is at least CATEGORY_GROWTH_MIN %/yr (either alone when the other
              is missing). Per share, so growth bought by issuing shares does not
              count. Yahoo gives no 2-year revenue forecast, so next year only.
              A forecast above the track record is capped at last year's actual
              revenue growth (`revenue_cagr_1y`), or the track record if higher:
              acquisitions and bank revenue-definition jumps had made 42 large
              companies growth (Kimberly-Clark +70% on Kenvue).
  * income  — regular dividend yield (`div_yield`, specials removed) at least
              CATEGORY_INCOME_YIELD_MIN %, with a dividend in each of the last
              CATEGORY_INCOME_YEARS calendar years (`div_years_paid`).
  * both    — growth and income.
  * early   — a loss in the latest fiscal year and in more than half of the last
              up to 3, with revenue under CATEGORY_EARLY_MAX_REVENUE: a big
              forecast from a tiny base is not an established grower. Replaces
              growth / neither (an income label stays).
  * neither — otherwise.

Companies only (stocks / ADRs with an industry); NULL for funds and shells.
`category_growth` stores the averaged growth rate the test used.

The review's downstream test: 74% of Russell 1000 Growth-only stocks came out
growth (plus 9% early); 70% of the Schwab US Dividend Equity ETF came out income.
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd

from config import settings
from analysis_layer import strength

GROWTH, INCOME, BOTH, EARLY, NEITHER = "growth", "income", "both", "early", "neither"

_NAN = float("nan")


def _num(v) -> float:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return _NAN
    return f if math.isfinite(f) else _NAN


def _is_early(tab: pd.DataFrame) -> bool:
    """A loss in the latest fiscal year and in more than half of the last up to 3,
    with the latest annual revenue under the line (no revenue on file = 0, a
    pre-revenue company). Revenue is in the reporting currency: P/S can't convert
    it (TSM's is computed on Taiwan dollars). Most foreign currencies are worth
    less than a dollar, so the error leans toward NOT early."""
    ni = tab["net_income"].dropna().iloc[-3:] if "net_income" in tab.columns else pd.Series(dtype=float)
    if not len(ni) or ni.iloc[-1] >= 0:
        return False
    if (ni < 0).sum() * 2 <= len(ni):
        return False
    rev = tab["total_revenue"].dropna() if "total_revenue" in tab.columns else pd.Series(dtype=float)
    return (float(rev.iloc[-1]) if len(rev) else 0.0) < settings.CATEGORY_EARLY_MAX_REVENUE


def compute(tab: pd.DataFrame | None, ctype: str | None, m: dict, est: dict,
            splits: pd.Series | None = None) -> dict:
    """Per-symbol label columns. `tab` = the one-row-per-year table; `m` = the
    metrics row (div_yield, div_years_paid); `est` = the
    estimates row (forward_rev_growth)."""
    out = {"revenue_ps_growth": _NAN, "category_growth": _NAN, "stock_category": None}
    if tab is None or tab.empty or ctype is None or ctype == "shell":
        return out
    out["revenue_ps_growth"] = strength.per_share_growth(
        tab["total_revenue"], tab["diluted_average_shares"], splits)

    past, fwd = out["revenue_ps_growth"], _num(est.get("forward_rev_growth"))
    last_year = _num(m.get("revenue_cagr_1y"))
    if pd.notna(past) and pd.notna(fwd) and fwd > past and pd.notna(last_year):
        # A forecast above the track record counts only as far as last year's
        # actual growth backs it: an acquisition (Kimberly-Clark +70%, Kenvue) or a
        # bank revenue-definition jump (+56-88%) is not yet in the reported year.
        fwd = min(fwd, max(past, last_year))
    rates = [r for r in (past, fwd) if pd.notna(r)]
    g = float(np.mean(rates)) if rates else _NAN
    out["category_growth"] = g
    growth = pd.notna(g) and g >= settings.CATEGORY_GROWTH_MIN
    years = _num(m.get("div_years_paid"))
    income = (_num(m.get("div_yield")) >= settings.CATEGORY_INCOME_YIELD_MIN
              and pd.notna(years) and years >= settings.CATEGORY_INCOME_YEARS)
    if _is_early(tab):
        growth = False                         # early replaces growth / neither
        out["stock_category"] = INCOME if income else EARLY
        return out
    out["stock_category"] = (BOTH if growth and income else GROWTH if growth
                             else INCOME if income else NEITHER)
    return out
