"""
Intrinsic value (Topic 4.1) — per-symbol fair-value estimates -> analysis.db.

Three independent estimates plus a blended margin of safety:

  * intrinsic_value_graham — √(22.5 · EPS · book value per share). Graham's classic
    conservative floor; deliberately low for asset-light / high-ROE businesses.
  * intrinsic_value_lynch  — EPS · fair P/E, where fair P/E is the earnings growth
    rate (Lynch: a fairly priced grower trades at P/E ≈ growth%). Growth capped.
  * intrinsic_value_dcf    — simple FCF DCF: project TTM FCF at capped historical
    growth over a horizon, Gordon terminal value, discount at a CAPM rate
    (risk-free from macro.db + beta · equity-risk-premium), less net debt, ÷ shares.
  * margin_of_safety       — how far price sits below the MEAN of the available
    estimates (positive = undervalued), in percent.

Reuses metrics' work: EPS and growth come from the already-computed metrics dict
`m` (so EPS is split-adjusted and consistent). When metrics fell back to yfinance
for valuation (`valuation_basis != "computed"`, i.e. a foreign filer whose
fundamentals are in another currency), intrinsic values would mix a USD price with
non-USD fundamentals, so we skip them entirely.
"""

from __future__ import annotations

import math

import pandas as pd

from config import settings
from analysis_layer import _periods as P
from analysis_layer.metrics import fcf_ttm


def _num(x) -> float:
    return float(x) if x is not None and pd.notna(x) else float("nan")


def _shares(quote: pd.Series | None, fin: pd.DataFrame) -> float:
    if quote is not None and pd.notna(quote.get("sharesOutstanding")):
        return float(quote["sharesOutstanding"])
    for col in ("ordinary_shares_number", "share_issued"):
        v = P.latest(fin, col)
        if pd.notna(v):
            return v
    return float("nan")


def compute(
    symbol: str,
    fin: pd.DataFrame,
    quote: pd.Series | None,
    price: float,
    m: dict,
    risk_free: float,
) -> dict:
    """Intrinsic values for one symbol. `risk_free` is an annual fraction (0.043).

    `m` is this symbol's metrics dict (for split-adjusted EPS and growth). Missing
    inputs / non-stock securities yield NaN; foreign-currency filers are skipped.
    """
    out = {
        "intrinsic_value_graham": float("nan"),
        "intrinsic_value_lynch": float("nan"),
        "intrinsic_value_dcf": float("nan"),
        "margin_of_safety": float("nan"),
    }
    if m.get("valuation_basis") != "computed":  # currency mismatch -> not comparable
        return out

    eps = _num(m.get("eps_ttm"))
    shares = _shares(quote, fin)
    equity = P.latest(fin, "stockholders_equity")
    bvps = equity / shares if pd.notna(equity) and pd.notna(shares) and shares > 0 else float("nan")

    out["intrinsic_value_graham"] = _graham(eps, bvps)
    out["intrinsic_value_lynch"] = _lynch(eps, m)
    out["intrinsic_value_dcf"] = _dcf(fin, m, quote, shares, risk_free)

    estimates = [out[k] for k in
                 ("intrinsic_value_graham", "intrinsic_value_lynch", "intrinsic_value_dcf")
                 if pd.notna(out[k]) and out[k] > 0]
    if estimates and price and price > 0:
        fair = sum(estimates) / len(estimates)
        out["margin_of_safety"] = (fair - price) / fair * 100
    return out


def _graham(eps: float, bvps: float) -> float:
    if pd.isna(eps) or pd.isna(bvps) or eps <= 0 or bvps <= 0:
        return float("nan")
    return math.sqrt(settings.GRAHAM_MULTIPLIER * eps * bvps)


def _lynch(eps: float, m: dict) -> float:
    """EPS × fair P/E, fair P/E = earnings growth% (3y CAGR, 5y fallback), capped."""
    growth = m.get("eps_cagr_3y")
    if pd.isna(growth):
        growth = m.get("eps_cagr_5y")
    if pd.isna(eps) or eps <= 0 or pd.isna(growth) or growth <= 0:
        return float("nan")
    return eps * min(growth, settings.LYNCH_GROWTH_CAP)


def _dcf(fin: pd.DataFrame, m: dict, quote: pd.Series | None, shares: float, risk_free: float) -> float:
    fcf0 = fcf_ttm(fin)
    if pd.isna(fcf0) or fcf0 <= 0 or pd.isna(shares) or shares <= 0:
        return float("nan")

    beta = float(quote["beta"]) if quote is not None and pd.notna(quote.get("beta")) else settings.DCF_DEFAULT_BETA
    discount = risk_free + beta * settings.DCF_EQUITY_RISK_PREMIUM
    gt = settings.DCF_TERMINAL_GROWTH
    if discount - gt < settings.DCF_MIN_DISCOUNT_SPREAD:
        discount = gt + settings.DCF_MIN_DISCOUNT_SPREAD

    # growth estimate from historical FCF CAGR (5y, 3y fallback), floored at 0.
    g_pct = m.get("fcf_cagr_5y")
    if pd.isna(g_pct):
        g_pct = m.get("fcf_cagr_3y")
    g = max(min((g_pct / 100) if pd.notna(g_pct) else 0.0, settings.DCF_GROWTH_CAP), 0.0)

    n = settings.DCF_PROJECTION_YEARS
    fcf, pv = fcf0, 0.0
    for t in range(1, n + 1):
        fcf *= 1 + g
        pv += fcf / (1 + discount) ** t
    terminal = fcf * (1 + gt) / (discount - gt)
    pv += terminal / (1 + discount) ** n

    net_debt = P.latest(fin, "net_debt")
    if pd.isna(net_debt):
        td, cash = P.latest(fin, "total_debt"), P.latest(fin, "cash_and_cash_equivalents")
        net_debt = (td if pd.notna(td) else 0.0) - (cash if pd.notna(cash) else 0.0)
    equity_value = pv - net_debt
    return equity_value / shares if equity_value > 0 else float("nan")
