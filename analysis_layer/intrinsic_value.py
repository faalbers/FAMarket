"""
Intrinsic value (Topic 4.1) — per-symbol fair-value estimates -> analysis.db.

Four independent estimates plus a type-gated blend and margin of safety:

  * intrinsic_value_graham — √(22.5 · EPS · book value per share). Graham's classic
    conservative floor; deliberately low for asset-light / high-ROE businesses.
  * intrinsic_value_lynch  — EPS · fair P/E, where fair P/E is the EPS trend growth
    rate (log-linear fit, steadier than endpoint CAGR — Lynch: a fairly priced
    grower trades at P/E ≈ growth%). Growth capped.
  * intrinsic_value_dcf    — simple FCF DCF: project TTM FCF at its capped trend
    growth rate (log-linear fit) over a horizon, Gordon terminal value, discount
    at a CAPM rate (risk-free from macro.db + beta · equity-risk-premium), less
    net debt, ÷ shares.
  * intrinsic_value_ddm    — dividend discount model: project the TTM per-share
    dividend at its capped trend growth over the same horizon, Gordon terminal
    value, discount at the SAME CAPM rate as the DCF (same company, same risk,
    whichever cash-flow stream you're discounting). Already per-share, so unlike
    the DCF there's no shares/net-debt conversion. The model banks/insurers/REITs
    actually get valued on — deposits/underwriting and GAAP depreciation make FCF-
    DCF and EPS-based Graham/Lynch unreliable for them, but the dividend itself
    isn't distorted the same way. Same reasoning applies to rate-regulated
    utilities/pipelines (EDGAR ASC-980 `regulatory` flag on symbols.db).
  * fair_value             — MEDIAN of whichever estimates are applicable to the
    symbol's type (`_applicable_models`, gated on `screen_type` + `regulated`) and
    computed positive. Median rather than mean: DCF has a long high-side tail
    (terminal-value/growth-assumption blowups) and Lynch collapses toward $0 for
    low/no-growth names — either one can drag a plain mean off; median is robust
    to a single outlier on either side.
  * margin_of_safety       — how far price sits below `fair_value`
    (positive = undervalued), in percent.

Reuses metrics' work: EPS and growth come from the already-computed metrics dict
`m` (so EPS is split-adjusted and consistent). When metrics fell back to yfinance
for valuation (`valuation_basis != "computed"`, i.e. a foreign filer whose
fundamentals are in another currency), intrinsic values would mix a USD price with
non-USD fundamentals, so we skip them entirely.
"""

from __future__ import annotations

import math
import statistics

import pandas as pd

from config import settings
from analysis_layer import _periods as P
from analysis_layer import screen_type as ST
from analysis_layer.metrics import fcf_ttm


def _applicable_models(screen_type: str | None, regulated: bool) -> frozenset[str]:
    """Which of the four models are conceptually sound for this symbol's type.

    DCF/Graham/Lynch lean on FCF/EPS, which GAAP depreciation and deposit/
    underwriting accounting distort for banks, insurers, REITs, and rate-
    regulated utilities/pipelines — see module docstring.
    """
    if screen_type == ST.REIT:
        return frozenset({"ddm"})
    if screen_type in (ST.BANK, ST.INSURANCE):
        return frozenset({"graham", "lynch", "ddm"})
    if screen_type == ST.STANDARD:
        if regulated:
            return frozenset({"graham", "lynch", "ddm"})
        return frozenset({"graham", "lynch", "dcf", "ddm"})
    return frozenset()  # etf / fund / preferred / minimal — none apply


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


_MODEL_COLUMN = {
    "graham": "intrinsic_value_graham",
    "lynch": "intrinsic_value_lynch",
    "dcf": "intrinsic_value_dcf",
    "ddm": "intrinsic_value_ddm",
}


def compute(
    symbol: str,
    fin: P.SymbolPeriods,
    quote: pd.Series | None,
    price: float,
    m: dict,
    risk_free: float,
    screen_type: str | None = None,
    regulated: bool = False,
) -> dict:
    """Intrinsic values for one symbol. `risk_free` is an annual fraction (0.043).

    `m` is this symbol's metrics dict (for split-adjusted EPS and growth). Missing
    inputs / non-stock securities yield NaN; foreign-currency filers are skipped.
    `screen_type`/`regulated` gate which of the four estimates feed `fair_value`
    and `margin_of_safety` (see `_applicable_models`); the four raw
    `intrinsic_value_*` columns are always computed regardless of type.
    """
    out = {
        "intrinsic_value_graham": float("nan"),
        "intrinsic_value_lynch": float("nan"),
        "intrinsic_value_dcf": float("nan"),
        "intrinsic_value_ddm": float("nan"),
        "fair_value": float("nan"),
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
    out["intrinsic_value_ddm"] = _ddm(m, quote, risk_free)

    applicable = _applicable_models(screen_type, regulated)
    estimates = [out[_MODEL_COLUMN[model]] for model in applicable
                 if pd.notna(out[_MODEL_COLUMN[model]]) and out[_MODEL_COLUMN[model]] > 0]
    if estimates:
        out["fair_value"] = statistics.median(estimates)
        if price and price > 0:
            fair = out["fair_value"]
            out["margin_of_safety"] = (fair - price) / fair * 100
    return out


def _graham(eps: float, bvps: float) -> float:
    if pd.isna(eps) or pd.isna(bvps) or eps <= 0 or bvps <= 0:
        return float("nan")
    return math.sqrt(settings.GRAHAM_MULTIPLIER * eps * bvps)


def _lynch(eps: float, m: dict) -> float:
    """EPS × fair P/E, fair P/E = EPS trend growth% (log-linear fit), capped."""
    growth = m.get("eps_growth_trend")
    if pd.isna(eps) or eps <= 0 or pd.isna(growth) or growth <= 0:
        return float("nan")
    return eps * min(growth, settings.LYNCH_GROWTH_CAP)


def _cost_of_equity(quote: pd.Series | None, risk_free: float) -> float:
    """CAPM cost of equity, floored to keep a minimum spread over terminal growth.

    Shared by the DCF and DDM — same company, same risk, same rate regardless of
    which cash-flow stream (FCF vs. dividends) it's discounting.
    """
    beta = float(quote["beta"]) if quote is not None and pd.notna(quote.get("beta")) else settings.DCF_DEFAULT_BETA
    discount = risk_free + beta * settings.DCF_EQUITY_RISK_PREMIUM
    gt = settings.DCF_TERMINAL_GROWTH
    if discount - gt < settings.DCF_MIN_DISCOUNT_SPREAD:
        discount = gt + settings.DCF_MIN_DISCOUNT_SPREAD
    return discount


def _dcf(fin: pd.DataFrame, m: dict, quote: pd.Series | None, shares: float, risk_free: float) -> float:
    fcf0 = fcf_ttm(fin)
    if pd.isna(fcf0) or fcf0 <= 0 or pd.isna(shares) or shares <= 0:
        return float("nan")

    discount = _cost_of_equity(quote, risk_free)
    gt = settings.DCF_TERMINAL_GROWTH

    # growth estimate from FCF trend growth (log-linear fit), floored at 0.
    g_pct = m.get("fcf_growth_trend")
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


def _ddm(m: dict, quote: pd.Series | None, risk_free: float) -> float:
    """Two-stage dividend discount model: project the TTM per-share dividend at
    its capped trend growth, Gordon terminal value, discount at CAPM cost of
    equity. Already per-share (a dividend rate, not a company aggregate) — no
    shares/net-debt conversion needed, unlike the DCF.
    """
    d0 = m.get("div_rate_ttm")
    if pd.isna(d0) or d0 <= 0:
        return float("nan")

    discount = _cost_of_equity(quote, risk_free)
    gt = settings.DCF_TERMINAL_GROWTH

    # growth estimate from dividend trend growth (log-linear fit), floored at 0.
    g_pct = m.get("div_growth_trend")
    g = max(min((g_pct / 100) if pd.notna(g_pct) else 0.0, settings.DCF_GROWTH_CAP), 0.0)

    n = settings.DCF_PROJECTION_YEARS
    div, pv = d0, 0.0
    for t in range(1, n + 1):
        div *= 1 + g
        pv += div / (1 + discount) ** t
    terminal = div * (1 + gt) / (discount - gt)
    pv += terminal / (1 + discount) ** n
    return pv if pv > 0 else float("nan")
