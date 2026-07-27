"""
Fundamental metrics (Topic 4.1) — raw numeric values per symbol -> analysis.db.

Design: **compute-and-reconcile** (see [[analysis-layer-design-decisions]]). Every
ratio is computed from financials.db with ONE convention:
  * price  = the canonical adj_close of the last completed session (passed in),
             never yfinance's live price;
  * TTM    = sum of the last four quarters for flows (via _periods.ttm);
  * TTM EPS = TTM net income / current diluted shares (NOT a sum of quarterly EPS,
             which isn't additive across changing share counts).
Where quotes.db carries the same ratio we cross-check it and append a divergence
record to `reconcile` (the pipeline logs one summary line — no per-symbol noise).

Two correctness guards the data forced on us:
  * Currency: foreign filers (e.g. TSM, financialCurrency≠price currency) report
    fundamentals in another currency, so price÷fundamental valuation ratios are
    meaningless. When currencies disagree we fall back to yfinance's pre-computed
    valuation ratios and flag `valuation_basis="yfinance"`. Pure-fundamental ratios
    (margins, ROE, …) are currency-neutral and always computed.
  * Splits: EPS is reported split-UNADJUSTED, so an EPS *growth* series crossing a
    split is discontinuous. We split-adjust the EPS series with ohlcv `splits`
    before computing growth. Dollar series (revenue, FCF, equity) need no adjust.

Percent convention: every "%" metric is stored as a percent number (12.5, not
0.125) — see [[param-unit-percent-storage]].

Sections: valuation · profitability · growth · income · financial health.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from config import settings
from analysis_layer import _periods as P

# ---- field kinds for _periods.current() ---------------------------------- #
# Flows (income statement / cash flow) get a TTM sum; stocks (balance sheet) take
# the latest reported value.

# Metrics whose yfinance equivalent we reconcile against. value = (quotes column,
# scale to apply to the quotes value to match our units). yfinance stores margins
# /ROE/ROA as fractions and debtToEquity as a percent, so we rescale to compare.
_RECONCILE = {
    "pe": ("trailingPE", 1.0),
    "pb": ("priceToBook", 1.0),
    "ps": ("priceToSalesTrailing12Months", 1.0),
    "ev_ebitda": ("enterpriseToEbitda", 1.0),
    "ev_revenue": ("enterpriseToRevenue", 1.0),
    "roe": ("returnOnEquity", 100.0),       # fraction -> percent
    "roa": ("returnOnAssets", 100.0),
    "gross_margin": ("grossMargins", 100.0),
    "operating_margin": ("operatingMargins", 100.0),
    "net_margin": ("profitMargins", 100.0),
    "debt_to_equity": ("debtToEquity", 0.01),  # percent -> ratio (x)
    "current_ratio": ("currentRatio", 1.0),
    "quick_ratio": ("quickRatio", 1.0),
}


# --------------------------------------------------------------------------- #
# small numeric helpers
# --------------------------------------------------------------------------- #
def _div(a: float, b: float) -> float:
    """a / b, but NaN on zero / missing / non-finite denominator or numerator."""
    if a is None or b is None or pd.isna(a) or pd.isna(b) or b == 0:
        return float("nan")
    return a / b


def _pct(x: float) -> float:
    """Fraction -> stored percent number (0.125 -> 12.5)."""
    return x * 100 if pd.notna(x) else float("nan")


def _qget(quote: pd.Series | None, key: str) -> float:
    """A numeric field from the quotes row, or NaN when absent/blank."""
    if quote is None or key not in quote:
        return float("nan")
    v = pd.to_numeric(quote.get(key), errors="coerce")
    return float(v) if pd.notna(v) else float("nan")


# --------------------------------------------------------------------------- #
# ratio formulas — the single definition of each statement ratio
# --------------------------------------------------------------------------- #
# compute() calls these with TTM/latest inputs (the snapshot stored in analysis.db);
# the per-period fundamentals chart (ui) calls the SAME functions with each period's
# reported inputs. One formula, two callers — so a ratio is never defined twice.
# Only currency-neutral statement ratios live here (margins, returns, leverage);
# price-based ratios (P/E, EV/EBITDA, …) need a price per period and are not
# reconstructable from financials.db alone.
def net_margin(ni: float, rev: float) -> float:
    return _pct(_div(ni, rev))


def gross_margin(gp: float, rev: float) -> float:
    return _pct(_div(gp, rev))


def operating_margin(ebit: float, rev: float) -> float:
    return _pct(_div(ebit, rev))


def fcf_margin(fcf: float, rev: float) -> float:
    return _pct(_div(fcf, rev))


def roe(ni: float, equity: float) -> float:
    return _pct(_div(ni, equity))


def roa(ni: float, total_assets: float) -> float:
    return _pct(_div(ni, total_assets))


def debt_to_equity(total_debt: float, equity: float) -> float:
    return _div(total_debt, equity)


def current_ratio(cur_assets: float, cur_liab: float) -> float:
    return _div(cur_assets, cur_liab)


def debt_to_ebitda(total_debt: float, ebitda: float) -> float:
    return _div(total_debt, ebitda)


def interest_coverage(ebit: float, interest_expense: float) -> float:
    return _div(ebit, abs(interest_expense) if pd.notna(interest_expense) else float("nan"))


# Per-period chart registry (canonical, lives in the analysis layer so the metric
# definitions stay here, not in the UI). Each ratio maps to its formula function and
# the financials.db fields it consumes IN ORDER; the chart reads those fields per
# reported period and calls the function. Percent-valued ratios are flagged for the
# chart's axis unit (these functions already return percent numbers, e.g. 12.5).
RATIO_PERIOD_METRICS: dict[str, tuple] = {
    "gross_margin": (gross_margin, ("gross_profit", "total_revenue"), "%"),
    "operating_margin": (operating_margin, ("operating_income", "total_revenue"), "%"),
    "net_margin": (net_margin, ("net_income", "total_revenue"), "%"),
    "fcf_margin": (fcf_margin, ("free_cash_flow", "total_revenue"), "%"),
    "roe": (roe, ("net_income", "stockholders_equity"), "%"),
    "roa": (roa, ("net_income", "total_assets"), "%"),
    "debt_to_equity": (debt_to_equity, ("total_debt", "stockholders_equity"), "x"),
    "debt_to_ebitda": (debt_to_ebitda, ("total_debt", "ebitda"), "x"),
    "current_ratio": (current_ratio, ("current_assets", "current_liabilities"), "x"),
    "interest_coverage": (interest_coverage, ("operating_income", "interest_expense"), "x"),
}

# Raw statement line items the chart can plot directly (no formula) -> display label.
RAW_PERIOD_FIELDS: dict[str, str] = {
    "total_revenue": "Revenue",
    "gross_profit": "Gross profit",
    "operating_income": "Operating income",
    "net_income": "Net income",
    "ebitda": "EBITDA",
    "free_cash_flow": "Free cash flow",
    "diluted_eps": "Diluted EPS",
    "stockholders_equity": "Stockholders' equity",
    "total_assets": "Total assets",
    "total_debt": "Total debt",
}


# --------------------------------------------------------------------------- #
# free cash flow (shared with intrinsic_value)
# --------------------------------------------------------------------------- #
# yfinance only populates `free_cash_flow` for ~recent periods, while the EDGAR
# backfill carries the full operating_cash_flow + capital_expenditure history.
# FCF = OCF + capex (capex is stored negative) reproduces yfinance's FCF exactly
# and extends it back through the deep history.
def fcf_annual(fin: pd.DataFrame) -> pd.Series:
    """Deep-history annual FCF (OCF + capex), for growth + DCF; full EDGAR span."""
    derived = (P.annual(fin, "operating_cash_flow") + P.annual(fin, "capital_expenditure")).dropna()
    return derived if not derived.empty else P.annual(fin, "free_cash_flow")


def fcf_ttm(fin: pd.DataFrame) -> float:
    """TTM FCF from the reported (discrete-quarter, yfinance) column.

    Falls back to OCF + capex only if needed — we don't TTM-sum *derived* quarters
    because EDGAR's quarterly cash flow can be YTD-cumulative.
    """
    v = P.ttm(fin, "free_cash_flow")
    if pd.notna(v):
        return v
    ocf, capex = P.ttm(fin, "operating_cash_flow"), P.ttm(fin, "capital_expenditure")
    return ocf + capex if pd.notna(ocf) and pd.notna(capex) else float("nan")


# --------------------------------------------------------------------------- #
# growth helpers
# --------------------------------------------------------------------------- #
def _cagr(s: pd.Series, years: int) -> float:
    """Annualized growth from ~`years` ago to the latest annual point (fraction).

    Gated: NaN unless an annual point exists at/around `years` back. Annualized by
    the actual span so slightly-off fiscal dates don't bias it. NaN when either
    endpoint is <= 0 (CAGR is undefined across a sign change).
    """
    if len(s) < 2:
        return float("nan")
    last_date, last = s.index[-1], float(s.iloc[-1])
    target = last_date - pd.DateOffset(years=years)
    older = s[s.index <= target + pd.Timedelta(days=60)]
    if older.empty:
        return float("nan")
    base, base_date = float(older.iloc[-1]), older.index[-1]
    span = (last_date - base_date).days / 365.25
    if base <= 0 or last <= 0 or span < 0.5:
        return float("nan")
    return (last / base) ** (1 / span) - 1


def _trend_stats(s: pd.Series) -> tuple[float, float, float]:
    """(residual volatility %, R², CV %) of a linear fit over the last N years.

    Linear (not log) fit so series with negative values (FCF/EPS losses) still
    work. Residual vol % = std(residuals)/|mean| ; R² = fit goodness; CV =
    std(values)/|mean|. NaN when fewer than 3 points or a zero mean.
    """
    s = s.iloc[-settings.GROWTH_TREND_YEARS :]
    y = s.to_numpy(dtype=float)
    if len(y) < 3:
        return float("nan"), float("nan"), float("nan")
    x = np.arange(len(y))
    fit = np.polyval(np.polyfit(x, y, 1), x)
    resid = y - fit
    ss_res = float(np.sum(resid**2))
    ss_tot = float(np.sum((y - y.mean()) ** 2))
    r2 = 1 - ss_res / ss_tot if ss_tot > 0 else float("nan")
    denom = abs(float(y.mean()))
    if denom == 0:
        return float("nan"), r2, float("nan")
    return float(np.std(resid)) / denom * 100, r2, float(np.std(y)) / denom * 100


def _yoy_latest(sq: pd.Series) -> float:
    """Latest same-quarter-year-ago growth (fraction); removes seasonality."""
    if len(sq) < 5:
        return float("nan")
    last, prior = float(sq.iloc[-1]), float(sq.iloc[-5])
    if prior <= 0:
        return float("nan")
    return last / prior - 1


def _level_change(s: pd.Series, years: int) -> float:
    """Absolute change in a level series from ~`years` ago to the latest annual
    point (same point-selection rule as _cagr). NaN if no point ~`years` back.

    Used for margin TREND: the input series is already in percent (margin levels),
    so the result is a change in percentage points.
    """
    s = s.dropna()
    if len(s) < 2:
        return float("nan")
    last_date, last = s.index[-1], float(s.iloc[-1])
    target = last_date - pd.DateOffset(years=years)
    older = s[s.index <= target + pd.Timedelta(days=60)]
    if older.empty:
        return float("nan")
    return last - float(older.iloc[-1])


def _margin_series(fin, num_field: str, den_field: str) -> pd.Series:
    """Annual margin level series (percent) = num / den per reported year × 100.

    The two annual series align on period_end; years missing either field drop out.
    """
    num, den = P.annual(fin, num_field), P.annual(fin, den_field)
    if num.empty or den.empty:
        return pd.Series(dtype="float64")
    return (num / den).replace([np.inf, -np.inf], float("nan")).dropna() * 100


def split_adjust(s: pd.Series, splits: pd.Series | None) -> pd.Series:
    """Per-share series rescaled to current-share terms using post-period splits.

    Public (shared): the analysis snapshot uses it for EPS growth, and the
    fundamentals chart uses it so a plotted EPS-over-time series is split-consistent
    with the snapshot — one adjustment, defined once. `splits` is a datetime-indexed
    sparse series of split factors (from ohlcv.db).
    """
    if splits is None or s.empty:
        return s
    real = splits[(splits != 0) & (splits != 1)]
    if real.empty:
        return s
    out = s.copy()
    for d in s.index:
        factor = float(real[real.index > d].prod())
        if factor and factor > 0:
            out.loc[d] = s.loc[d] / factor
    return out


def _growth_block(name: str, annual: pd.Series, quarterly: pd.Series) -> dict:
    """CAGR(1/3/5y) + trend stats + latest YoY-quarterly for one growth series."""
    out: dict[str, float] = {}
    for w in settings.GROWTH_WINDOWS_YEARS:
        out[f"{name}_cagr_{w}y"] = _pct(_cagr(annual, w))
    vol, r2, cv = _trend_stats(annual)
    out[f"{name}_growth_vol"] = vol
    out[f"{name}_growth_r2"] = r2
    out[f"{name}_growth_cv"] = cv
    out[f"{name}_yoy_q"] = _pct(_yoy_latest(quarterly))
    return out


# --------------------------------------------------------------------------- #
# main entry
# --------------------------------------------------------------------------- #
def compute(
    symbol: str,
    fin: P.SymbolPeriods,
    quote: pd.Series | None,
    price: float,
    *,
    dividends: pd.Series | None = None,
    splits: pd.Series | None = None,
    as_of: pd.Timestamp | None = None,
    reconcile: list | None = None,
) -> dict:
    """All fundamental metrics for one symbol (raw numbers, percents as percents).

    `fin` is this symbol's prepared period frames (via _periods.prepare); `quote` is its
    quotes row; `price` the canonical adj_close. `dividends`/`splits` are sparse
    FULL-history event series (datetime-indexed payment amounts / split factors) —
    the pipeline reads them without the OHLCV date floor, so dividend streaks and
    EPS split-adjustment keep their whole span. `as_of` is the symbol's last bar
    date (anchors the dividend TTM window; None = no price history at all).
    Missing inputs yield NaN (= "not applicable"), so funds/ETFs with no financials
    fall through harmlessly. Divergences vs yfinance are appended to `reconcile`.
    """
    m: dict[str, float] = {}

    # -- shared inputs ------------------------------------------------------- #
    shares = _qget(quote, "sharesOutstanding")
    if pd.isna(shares):
        shares = P.latest(fin, "ordinary_shares_number")
    if pd.isna(shares):
        shares = P.latest(fin, "share_issued")
    mktcap = _div(price * shares, 1.0) if pd.notna(shares) else float("nan")
    m["market_cap"] = mktcap  # size; currency-neutral (price & shares share the listing's terms)

    rev = P.ttm(fin, "total_revenue")
    ni = P.ttm(fin, "net_income")
    ebitda = P.ttm(fin, "ebitda")
    ebit = P.ttm(fin, "operating_income")
    gp = P.ttm(fin, "gross_profit")
    fcf = fcf_ttm(fin)
    equity = P.latest(fin, "stockholders_equity")
    total_debt = P.latest(fin, "total_debt")
    cash = P.latest(fin, "cash_and_cash_equivalents")
    total_assets = P.latest(fin, "total_assets")
    total_liab = P.latest(fin, "total_liabilities_net_minority_interest")
    ev = (mktcap + total_debt - cash) if pd.notna(mktcap) else float("nan")

    fin_ccy = quote.get("financialCurrency") if quote is not None else None
    price_ccy = quote.get("currency") if quote is not None else None
    currency_ok = (
        pd.isna(fin_ccy) or fin_ccy in (None, "") or fin_ccy == price_ccy
    )

    # -- valuation ----------------------------------------------------------- #
    if currency_ok:
        m["pe"] = _div(mktcap, ni)
        m["pb"] = _div(mktcap, equity)
        m["ps"] = _div(mktcap, rev)
        m["p_fcf"] = _div(mktcap, fcf)
        m["ev_ebitda"] = _div(ev, ebitda)
        m["ev_revenue"] = _div(ev, rev)
        m["eps_ttm"] = _div(ni, shares)
        m["valuation_basis"] = "computed"
    else:  # foreign filer — price÷fundamental is invalid; use yfinance ratios
        m["pe"] = _qget(quote, "trailingPE")
        m["pb"] = _qget(quote, "priceToBook")
        m["ps"] = _qget(quote, "priceToSalesTrailing12Months")
        m["p_fcf"] = float("nan")
        m["ev_ebitda"] = _qget(quote, "enterpriseToEbitda")
        m["ev_revenue"] = _qget(quote, "enterpriseToRevenue")
        m["eps_ttm"] = _qget(quote, "trailingEps")
        m["valuation_basis"] = "yfinance"

    forward_eps = _qget(quote, "forwardEps")
    m["forward_pe"] = _div(price, forward_eps)  # forward = analyst data, from quotes

    # -- profitability (currency-neutral) ----------------------------------- #
    m["roe"] = roe(ni, equity)
    m["roa"] = roa(ni, total_assets)
    tax_rate = _div(P.ttm(fin, "tax_provision"), P.ttm(fin, "pretax_income"))
    if pd.notna(tax_rate):
        tax_rate = min(max(tax_rate, 0.0), 1.0)
    nopat = ebit * (1 - tax_rate) if pd.notna(ebit) and pd.notna(tax_rate) else float("nan")
    m["roic"] = _pct(_div(nopat, P.latest(fin, "invested_capital")))
    m["gross_margin"] = gross_margin(gp, rev)
    m["operating_margin"] = operating_margin(ebit, rev)
    m["net_margin"] = net_margin(ni, rev)
    m["fcf_margin"] = fcf_margin(fcf, rev)
    # Margin TREND (percentage points): widening margins signal pricing power and
    # often precede a re-rating. Annual-vs-annual so it's apples-to-apples.
    m["gross_margin_trend_3y"] = _level_change(_margin_series(fin, "gross_profit", "total_revenue"), 3)
    m["operating_margin_trend_3y"] = _level_change(_margin_series(fin, "operating_income", "total_revenue"), 3)

    # -- financial health --------------------------------------------------- #
    cur_assets = P.latest(fin, "current_assets")
    cur_liab = P.latest(fin, "current_liabilities")
    inventory = P.latest(fin, "inventory")
    m["debt_to_equity"] = debt_to_equity(total_debt, equity)
    m["current_ratio"] = current_ratio(cur_assets, cur_liab)
    # Acid test: liquid assets only (cash + ST investments + receivables). Prefer
    # the explicit liquid line; fall back to current-assets-minus-inventory.
    liquid = P.latest(fin, "cash_cash_equivalents_and_short_term_investments")
    recv = P.latest(fin, "receivables")
    if pd.notna(liquid):
        quick_num = liquid + (recv if pd.notna(recv) else 0.0)
    else:
        quick_num = (cur_assets - inventory) if pd.notna(inventory) else cur_assets
    m["quick_ratio"] = _div(quick_num, cur_liab)
    m["cash_ratio"] = _div(cash, cur_liab)
    m["interest_coverage"] = interest_coverage(ebit, P.ttm(fin, "interest_expense"))
    m["debt_to_ebitda"] = debt_to_ebitda(total_debt, ebitda)
    m["altman_z"] = _altman_z(fin, ebit, rev, mktcap, total_assets, total_liab) if currency_ok else float("nan")

    # -- growth ------------------------------------------------------------- #
    eps_a = split_adjust(P.annual(fin, "diluted_eps"), splits)
    eps_q = split_adjust(P.quarterly(fin, "diluted_eps"), splits)
    m.update(_growth_block("revenue", P.annual(fin, "total_revenue"), P.quarterly(fin, "total_revenue")))
    m.update(_growth_block("eps", eps_a, eps_q))
    m.update(_growth_block("fcf", fcf_annual(fin), P.quarterly(fin, "free_cash_flow")))
    m.update(_growth_block("book_value", P.annual(fin, "stockholders_equity"), P.quarterly(fin, "stockholders_equity")))

    # Growth ACCELERATION (percentage points): latest YoY quarter minus the smoothed
    # 3y CAGR. Positive = speeding up vs its own recent pace. Inputs already computed
    # above; NaN in either propagates to NaN.
    m["revenue_accel"] = m["revenue_yoy_q"] - m["revenue_cagr_3y"]
    m["eps_accel"] = m["eps_yoy_q"] - m["eps_cagr_3y"]
    # Share-count trend: 1y change in diluted share count (the EPS denominator).
    # Negative = net buybacks, which lift per-share growth; positive = dilution.
    m["share_count_chg_1y"] = _pct(_cagr(P.annual(fin, "diluted_average_shares"), 1))

    # PEG: trailing P/E over 3y EPS CAGR (%); guard non-positive growth.
    g3 = m.get("eps_cagr_3y")
    m["peg"] = _div(m["pe"], g3) if (pd.notna(g3) and g3 > 0) else float("nan")

    # -- income / dividends ------------------------------------------------- #
    m.update(_income_block(fin, dividends, price, ni, fcf, as_of))

    # -- reconciliation ----------------------------------------------------- #
    if reconcile is not None and currency_ok:
        for metric, (col, scale) in _RECONCILE.items():
            ours, ref = m.get(metric), _qget(quote, col) * scale
            if pd.isna(ours) or pd.isna(ref) or ref == 0:
                continue
            pct = (ours - ref) / abs(ref)
            if abs(pct) > settings.RECONCILE_TOLERANCE_PCT:
                reconcile.append(
                    {"symbol": symbol, "metric": metric, "computed": ours,
                     "reference": ref, "pct_diff": pct * 100}
                )

    return m


def _altman_z(fin, ebit, rev, mktcap, total_assets, total_liab) -> float:
    """Altman Z-Score (manufacturing form). NaN if total assets/liabilities absent."""
    if pd.isna(total_assets) or total_assets == 0 or pd.isna(total_liab) or total_liab == 0:
        return float("nan")
    wc = P.latest(fin, "working_capital")
    re = P.latest(fin, "retained_earnings")
    a = _div(wc, total_assets)
    b = _div(re, total_assets)
    c = _div(ebit, total_assets)
    d = _div(mktcap, total_liab)
    e = _div(rev, total_assets)
    if any(pd.isna(v) for v in (a, b, c, d, e)):
        return float("nan")
    return 1.2 * a + 1.4 * b + 3.3 * c + 0.6 * d + 1.0 * e


def _income_block(fin, paid: pd.Series | None, price, ni_ttm, fcf_ttm, as_of) -> dict:
    """Dividend metrics from the full-history dividend events (no extra API)."""
    out = {
        "div_yield_ttm": float("nan"), "div_rate_ttm": float("nan"),
        "div_cagr_1y": float("nan"), "div_cagr_3y": float("nan"), "div_cagr_5y": float("nan"),
        "div_payout_ratio": float("nan"),
        "div_consecutive_years": float("nan"), "div_consistency": float("nan"),
        "div_coverage": float("nan"),
        "div_growth_vol": float("nan"), "div_growth_r2": float("nan"),
        "div_growth_cv": float("nan"),
    }
    if as_of is None or pd.isna(as_of):  # no price history at all -> not applicable
        return out
    if paid is not None and not paid.empty:
        paid = pd.to_numeric(paid, errors="coerce")
        paid = paid[paid > 0]
    if paid is None or paid.empty:  # non-payer: a real 0% yield, the rest not applicable
        out["div_yield_ttm"] = 0.0
        out["div_rate_ttm"] = 0.0
        return out

    last = pd.Timestamp(as_of)
    ttm_div = float(paid[paid.index > last - pd.Timedelta(days=365)].sum())
    out["div_rate_ttm"] = ttm_div
    out["div_yield_ttm"] = _pct(_div(ttm_div, price))

    # Dividend-trend metrics use each complete year's MAX payment (the per-share
    # rate), not the calendar-year sum: a monthly/quarterly payer's yearly sum
    # swings with how many ex-dates land in a calendar year (11 vs 12 vs 13),
    # which would falsely break a long raising streak (e.g. Realty Income).
    by_year = paid.groupby(paid.index.year).max()
    by_year = by_year[by_year.index < last.year]
    if len(by_year) >= 2:
        for w in settings.GROWTH_WINDOWS_YEARS:
            out[f"div_cagr_{w}y"] = _pct(_cagr_from_yearly(by_year, w))
        steps = by_year.diff().dropna()
        out["div_consistency"] = _pct((steps >= 0).mean())
        out["div_consecutive_years"] = float(_consecutive_increases(by_year))
        vol, r2, cv = _trend_stats(by_year)
        out["div_growth_vol"] = vol
        out["div_growth_r2"] = r2
        out["div_growth_cv"] = cv

    # payout / coverage off the cash-flow statement (currency-neutral ratios)
    div_paid = abs(P.ttm(fin, "cash_dividends_paid"))
    out["div_payout_ratio"] = _pct(_div(div_paid, ni_ttm))
    out["div_coverage"] = _div(fcf_ttm, div_paid)
    return out


def _cagr_from_yearly(by_year: pd.Series, years: int) -> float:
    """CAGR over a calendar-year-indexed Series (used for dividends)."""
    s = by_year.copy()
    s.index = pd.to_datetime(s.index.astype(str) + "-12-31")
    return _cagr(s, years)


def _consecutive_increases(by_year: pd.Series) -> int:
    """Count of trailing consecutive years the annual value did not fall."""
    v = by_year.to_numpy(dtype=float)
    n = 0
    for i in range(len(v) - 1, 0, -1):
        if v[i] >= v[i - 1]:
            n += 1
        else:
            break
    return n
