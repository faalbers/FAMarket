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
from analysis_layer.screen_type import STANDARD

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


def asset_turnover(rev: float, total_assets: float) -> float:
    return _div(rev, total_assets)


def equity_multiplier(total_assets: float, equity: float) -> float:
    return _div(total_assets, equity)


def roe_roa_gap(ni: float, equity: float, total_assets: float) -> float:
    """ROE - ROA, in percentage points — how much of ROE is leverage, not margin/turnover."""
    return roe(ni, equity) - roa(ni, total_assets)


def debt_to_equity(total_debt: float, equity: float) -> float:
    return _div(total_debt, equity)


def current_ratio(cur_assets: float, cur_liab: float) -> float:
    return _div(cur_assets, cur_liab)


def debt_to_ebitda(total_debt: float, ebitda: float) -> float:
    return _div(total_debt, ebitda)


def interest_coverage(ebit: float, interest_expense: float) -> float:
    return _div(ebit, abs(interest_expense) if pd.notna(interest_expense) else float("nan"))


def _wacc(mktcap: float, total_debt: float, interest_expense: float, tax_rate: float,
          beta: float, risk_free: float | None) -> float:
    """CAPM WACC (stored as a percent). NaN unless every input is a real, priced,
    PLAUSIBLE number — deliberately NO default-beta fallback (unlike the DCF
    discount rate in intrinsic_value.py): a WACC used to grade ROIC needs the
    company's own priced risk, not a convenience assumption standing in for
    missing data. total_debt == 0 (reported debt-free) skips the cost-of-debt
    term rather than requiring an interest_expense that a debt-free company has
    no reason to report.

    Two plausibility guards catch real data-quality failures seen in production
    (checked 2026-08-09): some micro/nano-cap tickers carry a corrupted yfinance
    beta (seen: values in the hundreds to billions, vs. ~±5 for 97%+ of the
    universe), and total_debt can swing inconsistently quarter-to-quarter
    relative to TTM interest expense (a reporting glitch, not a real balance
    change) — either one alone can blow WACC up to a nonsense magnitude even
    though every individual field is technically "present, not NaN".
    """
    if pd.isna(mktcap) or mktcap <= 0 or pd.isna(total_debt) or total_debt < 0:
        return float("nan")
    if pd.isna(beta) or abs(beta) > settings.WACC_BETA_MAX_ABS:
        return float("nan")
    if risk_free is None or pd.isna(risk_free) or pd.isna(tax_rate):
        return float("nan")
    v = mktcap + total_debt
    if v <= 0:
        return float("nan")
    cost_of_equity = risk_free + beta * settings.DCF_EQUITY_RISK_PREMIUM
    if total_debt == 0:
        cost_of_debt = 0.0
    else:
        pretax_cod = _div(abs(interest_expense) if pd.notna(interest_expense) else float("nan"), total_debt)
        if pd.isna(pretax_cod) or pretax_cod > settings.WACC_MAX_COST_OF_DEBT:
            return float("nan")
        cost_of_debt = pretax_cod * (1 - tax_rate)
    return _pct((mktcap / v) * cost_of_equity + (total_debt / v) * cost_of_debt)


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
    "asset_turnover": (asset_turnover, ("total_revenue", "total_assets"), "x"),
    "equity_multiplier": (equity_multiplier, ("total_assets", "stockholders_equity"), "x"),
    "roe_roa_gap": (roe_roa_gap, ("net_income", "stockholders_equity", "total_assets"), "%"),
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


def _trend_stats(s: pd.Series) -> tuple[float, float, float, float]:
    """(residual volatility %, R², CV %, trend growth %/yr) over the last N years.

    vol/R²/CV come from a LINEAR fit (handles negative values like FCF/EPS
    losses; residual vol % = std(residuals)/|mean|, CV = std(values)/|mean|).
    Trend growth is a SEPARATE log-linear fit (ln(y) vs. year index,
    annualized via exp(slope)-1) — only defined when every value is > 0, the
    same sign-change gate _cagr() uses, since a compound rate across a loss
    year is undefined. NaN when fewer than 3 points, a zero mean (vol/CV), or
    any non-positive value (trend growth only).
    """
    s = s.iloc[-settings.GROWTH_TREND_YEARS :]
    y = s.to_numpy(dtype=float)
    if len(y) < 3:
        return float("nan"), float("nan"), float("nan"), float("nan")
    x = np.arange(len(y))

    fit = np.polyval(np.polyfit(x, y, 1), x)
    resid = y - fit
    ss_res = float(np.sum(resid**2))
    ss_tot = float(np.sum((y - y.mean()) ** 2))
    r2 = 1 - ss_res / ss_tot if ss_tot > 0 else float("nan")
    denom = abs(float(y.mean()))
    vol = float(np.std(resid)) / denom * 100 if denom else float("nan")
    cv = float(np.std(y)) / denom * 100 if denom else float("nan")

    growth_trend = float("nan")
    if np.all(y > 0):
        slope = float(np.polyfit(x, np.log(y), 1)[0])
        growth_trend = (np.exp(slope) - 1) * 100
    return vol, r2, cv, growth_trend


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


def _cash_conversion(fin: pd.DataFrame, years: int) -> float:
    """Average OCF/net-income (percent) over the last `years` annual periods
    where net income was positive. A loss year flips the ratio's sign to noise,
    not weakness — a company can generate perfectly healthy cash flow in a GAAP
    loss year (heavy D&A, one-off charge), so it's excluded from the average
    rather than dragging it in either direction. NaN below 2 valid years — a
    single data point isn't a trailing average.

    A year is ALSO excluded when its ratio exceeds settings.CASH_CONVERSION_YEAR_CAP
    (checked 2026-08-09 against production data: a near-breakeven year — net
    income a rounding error relative to normal-scale cash flow, e.g. Ericsson's
    $20M NI on $46B OCF in one reported year — produces a triple-digit-percent
    ratio that reflects a near-zero denominator, not a real cash-conversion signal).
    """
    ocf, ni = P.annual(fin, "operating_cash_flow"), P.annual(fin, "net_income")
    if ocf.empty or ni.empty:
        return float("nan")
    df = pd.concat([ocf.rename("ocf"), ni.rename("ni")], axis=1).dropna().iloc[-years:]
    df = df[df["ni"] > 0]
    ratio = df["ocf"] / df["ni"]
    ratio = ratio[(ratio > 0) & (ratio <= settings.CASH_CONVERSION_YEAR_CAP)]
    if len(ratio) < 2:
        return float("nan")
    return float(ratio.mean()) * 100


def _roic_annual_series(fin: pd.DataFrame) -> pd.Series:
    """Annual ROIC level series (percent), oldest -> newest — same NOPAT/invested-
    capital convention as the point-in-time `roic` above. Reused by both the 5y
    persistence median and the 3y trend below, same way `_margin_series` backs the
    margin trend metrics.

    A year with invested_capital <= 0 is EXCLUDED, not just magnitude-capped — a
    negative denominator flips the ratio's sign to noise, not a real value-creation
    reading (seen both for buyback-heavy large caps whose equity has gone negative,
    e.g. VRSN, and distressed micro-caps), same "drop the noise year" philosophy as
    _cash_conversion's loss-year exclusion. The point-in-time `roic`'s ROIC_MAX_ABS
    magnitude cap alone doesn't catch this: a ratio like -491% sits comfortably
    under the ±500% cap while still being meaningless, and unlike the point-in-time
    single value, a level-CHANGE (_level_change below) differences two points with
    no averaging to dilute one bad year — checked 2026-08-10 against production
    data (VRSN's roic_trend_3y hit -556pp from exactly this).
    """
    df = pd.concat(
        [
            P.annual(fin, "operating_income").rename("ebit"),
            P.annual(fin, "tax_provision").rename("tax_prov"),
            P.annual(fin, "pretax_income").rename("pretax"),
            P.annual(fin, "invested_capital").rename("invested"),
        ],
        axis=1,
    ).dropna()
    df = df[df["invested"] > 0]
    if df.empty:
        return pd.Series(dtype="float64")
    tax_rate = (df["tax_prov"] / df["pretax"]).clip(lower=0.0, upper=1.0)
    nopat = df["ebit"] * (1 - tax_rate)
    roic = (nopat / df["invested"]).replace([np.inf, -np.inf], float("nan"))
    return (roic[roic.abs() <= settings.ROIC_MAX_ABS] * 100).dropna()


def _roic_persistence(roic_series: pd.Series, wacc: float, years: int) -> float:
    """Median annual ROIC over the last `years` periods, minus WACC (today's
    snapshot — true historical WACC would need historical beta/market cap, which
    isn't stored; see [[economic-moat-persistence-metrics]]). NaN below 2 valid
    years, same minimum as cash-conversion.
    """
    window = roic_series.iloc[-years:]
    if len(window) < 2 or pd.isna(wacc):
        return float("nan")
    return float(window.median()) - wacc


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
    vol, r2, cv, growth_trend = _trend_stats(annual)
    out[f"{name}_growth_vol"] = vol
    out[f"{name}_growth_r2"] = r2
    out[f"{name}_growth_cv"] = cv
    out[f"{name}_growth_trend"] = growth_trend
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
    screen_type: str | None = None,
    reconcile: list | None = None,
    risk_free: float | None = None,
) -> dict:
    """All fundamental metrics for one symbol (raw numbers, percents as percents).

    `fin` is this symbol's prepared period frames (via _periods.prepare); `quote` is its
    quotes row; `price` the canonical adj_close. `dividends`/`splits` are sparse
    FULL-history event series (datetime-indexed payment amounts / split factors) —
    the pipeline reads them without the OHLCV date floor, so dividend streaks and
    EPS split-adjustment keep their whole span. `as_of` is the symbol's last bar
    date (anchors the dividend TTM window; None = no price history at all).
    `screen_type` (analysis_layer.screen_type.classify's label) gates the metrics
    that are conceptually STANDARD-only regardless of what data happens to be on
    file — e.g. quick_health_score, since a REIT/bank/BDC's cost-of-revenue line
    (if it even reports one) doesn't mean the same thing as a producing company's.
    Missing inputs yield NaN (= "not applicable"), so funds/ETFs with no financials
    fall through harmlessly. Divergences vs yfinance are appended to `reconcile`.
    `risk_free` (annual fraction, e.g. 0.043) feeds `wacc`'s CAPM cost-of-equity —
    same figure intrinsic_value.compute() uses for its DCF discount rate; NaN/None
    means `wacc`/`roic_vs_wacc` fall back to NaN rather than guessing.
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
    # DuPont decomposition pieces: net_margin x asset_turnover x equity_multiplier
    # = ROE. roe_roa_gap (signed percentage points) isolates how much of ROE is
    # leverage vs margin/turnover.
    m["asset_turnover"] = asset_turnover(rev, total_assets)
    m["equity_multiplier"] = equity_multiplier(total_assets, equity)
    m["roe_roa_gap"] = m["roe"] - m["roa"]
    tax_rate = _div(P.ttm(fin, "tax_provision"), P.ttm(fin, "pretax_income"))
    if pd.notna(tax_rate):
        tax_rate = min(max(tax_rate, 0.0), 1.0)
    nopat = ebit * (1 - tax_rate) if pd.notna(ebit) and pd.notna(tax_rate) else float("nan")
    # invested_capital can be a near-zero (occasionally negative) dollar figure for a
    # distressed/collapsing micro-cap, or genuinely negative for a buyback-heavy large
    # cap whose equity has gone negative (e.g. VRSN) — either way NOPAT/invested_capital
    # is meaningless, not just outsized, so a non-positive denominator is excluded
    # outright (same "drop the noise" philosophy as _roic_annual_series above) rather
    # than relying on ROIC_MAX_ABS alone, which a ratio like -491% still sits under —
    # checked 2026-08-10 against production data (VRSN's roic hit -207.8% from exactly
    # this, well inside the magnitude cap below).
    invested_capital = P.latest(fin, "invested_capital")
    roic_frac = (_div(nopat, invested_capital)
                 if pd.notna(invested_capital) and invested_capital > 0 else float("nan"))
    m["roic"] = (_pct(roic_frac)
                 if pd.notna(roic_frac) and abs(roic_frac) <= settings.ROIC_MAX_ABS else float("nan"))
    # WACC / ROIC-vs-WACC: standard-only by CONCEPT (like quick_health_score below) —
    # "capital" in the WACC sense doesn't mean the same thing for a bank/insurer
    # (deposits/float, not invested capital) or a REIT (depreciation-distorted
    # earnings), and funds have no operating capital structure at all. Also gated
    # on currency_ok since it mixes a USD-consistent market cap with financials-
    # currency debt/interest, same risk as the valuation ratios above.
    beta = _qget(quote, "beta")
    m["wacc"] = (_wacc(mktcap, total_debt, P.ttm(fin, "interest_expense"), tax_rate, beta, risk_free)
                 if currency_ok and screen_type == STANDARD else float("nan"))
    m["roic_vs_wacc"] = (m["roic"] - m["wacc"]
                          if pd.notna(m["roic"]) and pd.notna(m["wacc"]) else float("nan"))
    # Moat persistence/direction: has the value-creation spread held up over time,
    # and is it widening or narrowing? Both standard-only for the same reason as
    # WACC/roic_vs_wacc above (capital structure concept doesn't apply to banks/
    # REITs/funds).
    roic_series = _roic_annual_series(fin) if screen_type == STANDARD else pd.Series(dtype="float64")
    m["roic_vs_wacc_5y"] = _roic_persistence(roic_series, m["wacc"], 5)
    m["roic_trend_3y"] = _level_change(roic_series, 3) if screen_type == STANDARD else float("nan")
    m["gross_margin"] = gross_margin(gp, rev)
    m["operating_margin"] = operating_margin(ebit, rev)
    m["net_margin"] = net_margin(ni, rev)
    m["fcf_margin"] = fcf_margin(fcf, rev)
    # Cash-flow quality: standard-only by CONCEPT (same reasoning as WACC above) —
    # a REIT's net income is depreciation-crushed by design (its whole reason for
    # existing as a metric type here), so OCF/NI would read as a huge, meaningless
    # ratio rather than an earnings-quality signal; banks' operating cash flow is
    # dominated by deposit/loan flows, not comparable to an ordinary company's.
    m["ocf_to_ni_3y"] = _cash_conversion(fin, 3) if screen_type == STANDARD else float("nan")
    m["ocf_to_ni_5y"] = _cash_conversion(fin, 5) if screen_type == STANDARD else float("nan")
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
    m["altman_z"] = (_altman_z(fin, ebit, rev, mktcap, total_assets, total_liab,
                                m["net_margin"], m["operating_margin"])
                      if currency_ok else float("nan"))
    # No currency_ok gate here (unlike altman_z): every Beneish input is a
    # statement-internal line item (revenue, receivables, assets, debt, cash
    # flow) — none of the 8 ratios mix a USD price/market-cap figure with
    # foreign-currency financials, so a financialCurrency/price-currency
    # mismatch (common for ADRs) doesn't actually invalidate it.
    m["beneish_m_score"] = _beneish_m_score(fin)
    # Standard-only by CONCEPT, not just by missing data: a REIT can report a
    # cost-of-revenue/gross-profit line and still not mean the same thing by it
    # (property operating expense vs COGS) — this checklist is written for
    # ordinary producing companies, so gate on screen_type directly rather than
    # relying only on which fields happen to be populated.
    m["quick_health_score"] = (_quick_health_score(fin, m["cash_ratio"])
                                if screen_type == STANDARD else float("nan"))

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

    # PEG: trailing P/E over EPS trend growth (%, log-linear fit); guard non-positive growth.
    g_eps = m.get("eps_growth_trend")
    m["peg"] = _div(m["pe"], g_eps) if (pd.notna(g_eps) and g_eps > 0) else float("nan")

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


def _altman_z(fin, ebit, rev, mktcap, total_assets, total_liab,
              net_margin: float, operating_margin: float) -> float:
    """Altman Z-Score (manufacturing form). NaN if total assets/liabilities absent.

    Also NaN for a large, currently-profitable company whose retained_earnings/
    total_assets is extremely negative (market_cap >= ALTMAN_BUYBACK_MKTCAP_FLOOR,
    ratio <= ALTMAN_RE_TA_EXTREME, both margins positive) — that combination means
    the deficit is buyback-driven (decades of repurchases charged against retained
    earnings), not accumulated losses, so the 1.4x-weighted RE/TA term dominates
    with a distress reading that contradicts genuine profitability (checked
    2026-08-10: VRSN, $26.6B market cap, 50% net margin, RE/TA of -8.9 -> altman_z
    of -4.4). Deliberately NOT a plain magnitude cap (unlike the ROIC/WACC/cash-
    conversion guards) — most of the tail at this ratio is REAL distress signal from
    small/mid-caps with genuine accumulated losses, which stays kept; only the
    scale + profitability combination marks it as the model's assumption breaking,
    not the company's health.
    """
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
    if (
        pd.notna(mktcap) and mktcap >= settings.ALTMAN_BUYBACK_MKTCAP_FLOOR
        and b <= settings.ALTMAN_RE_TA_EXTREME
        and pd.notna(operating_margin) and operating_margin > 0
        and pd.notna(net_margin) and net_margin > 0
    ):
        return float("nan")
    return 1.2 * a + 1.4 * b + 3.3 * c + 0.6 * d + 1.0 * e


def _beneish_pair(fin, field: str) -> tuple[float, float]:
    """(latest annual value, prior annual value) for `field` — Beneish's 8 ratios
    are all a most-recent-fiscal-year-vs-the-one-before-it comparison. NaN pair
    when fewer than 2 annual periods are on file."""
    s = P.annual(fin, field)
    if len(s) < 2:
        return float("nan"), float("nan")
    return float(s.iloc[-1]), float(s.iloc[-2])


def _ratio_index(numer_ratio: float, denom_ratio: float, eps: float = 1e-4) -> float:
    """numer_ratio / denom_ratio for one of Beneish's 6 ratio-of-ratios terms
    (DSRI/GMI/AQI/DEPI/SGAI/LVGI). NaN when the base ratio is too close to zero
    to divide reliably — e.g. a prior-year balance sheet that's ~100%
    current-assets+PP&E makes AQI's denominator a floating-point-cancellation
    artifact (~1e-16), not a real economic value, and dividing by it would blow
    up to an absurd score for what's usually just a thin micro-cap balance sheet.
    """
    if pd.isna(denom_ratio) or abs(denom_ratio) < eps:
        return float("nan")
    return _div(numer_ratio, denom_ratio)


def _beneish_m_score(fin) -> float:
    """Beneish M-Score: likelihood of earnings manipulation from 8 weighted
    year-over-year ratios (Beneish 1999). An ABSOLUTE threshold, not
    industry-relative (same shape as altman_z): above -1.78 flags a likely
    manipulator. NaN unless both fiscal years report every required line —
    `_div`/`_ratio_index` propagate NaN/zero-denominator through every ratio
    below, so no separate missing-data guard is needed. "Securities"
    (long-term investments, AQI's least-universally-reported line) defaults to
    0 when absent, since that most often means genuinely none rather than
    unreported.
    """
    pair = _beneish_pair
    rev_t, rev_p = pair(fin, "total_revenue")
    recv_t, recv_p = pair(fin, "receivables")
    gp_t, gp_p = pair(fin, "gross_profit")
    ca_t, ca_p = pair(fin, "current_assets")
    ta_t, ta_p = pair(fin, "total_assets")
    ppe_t, ppe_p = pair(fin, "net_ppe")
    if pd.isna(ppe_t) or pd.isna(ppe_p):
        gppe_t, gppe_p = pair(fin, "gross_ppe")
        ppe_t = gppe_t if pd.isna(ppe_t) else ppe_t
        ppe_p = gppe_p if pd.isna(ppe_p) else ppe_p
    sec_t, sec_p = pair(fin, "investments_and_advances")
    sec_t = 0.0 if pd.isna(sec_t) else sec_t
    sec_p = 0.0 if pd.isna(sec_p) else sec_p
    da_t, da_p = pair(fin, "depreciation_and_amortization")
    sga_t, sga_p = pair(fin, "selling_general_and_administration")
    debt_t, debt_p = pair(fin, "total_debt")
    ni_t, _ = pair(fin, "net_income_from_continuing_operations")
    if pd.isna(ni_t):
        ni_t, _ = pair(fin, "net_income")
    ocf_t, _ = pair(fin, "operating_cash_flow")

    dsri = _ratio_index(_div(recv_t, rev_t), _div(recv_p, rev_p))
    gmi = _ratio_index(_div(gp_p, rev_p), _div(gp_t, rev_t))
    aqi = _ratio_index(1 - _div(ca_t + ppe_t + sec_t, ta_t), 1 - _div(ca_p + ppe_p + sec_p, ta_p))
    sgi = _div(rev_t, rev_p)
    depi = _ratio_index(_div(da_p, da_p + ppe_p), _div(da_t, da_t + ppe_t))
    sgai = _ratio_index(_div(sga_t, rev_t), _div(sga_p, rev_p))
    lvgi = _ratio_index(_div(debt_t, ta_t), _div(debt_p, ta_p))
    tata = _div(ni_t - ocf_t, ta_t)

    score = (-4.84 + 0.92 * dsri + 0.528 * gmi + 0.404 * aqi + 0.892 * sgi
             + 0.115 * depi - 0.172 * sgai + 4.679 * tata - 0.327 * lvgi)

    # A near-zero prior-year base (e.g. a shell/reverse-merger with ~$0 revenue
    # the year before) isn't a numerical artifact like the _ratio_index cases
    # above — it's a REAL number that still makes one of these 8 ratios swing
    # to an absurd multiple, because the model assumes two economically
    # comparable years. Bound the OUTPUT rather than each raw ratio: healthy
    # companies cluster around -2 to -3, flagged ones rarely clear low single
    # digits (Beneish 1999's own manipulator sample), so anything past +/-10
    # carries no more signal than "the YoY comparison doesn't apply here" —
    # NaN it instead of reporting a meaningless four-digit value.
    return score if abs(score) <= 10 else float("nan")


def _quick_health_score(fin, cash_ratio: float) -> float:
    """Quick health checklist (FAMarket_Epansion.md Topic 2): a fast, COUNT-based
    pass/fail gate (0-7) meant to run BEFORE deeper ratio/valuation work — not a
    weighted/anchored score like Beneish or Altman. Every check is a YoY
    direction-of-travel comparison (this year vs last), never a peer comparison:
      1. revenue growing
      2. cost of revenue NOT outgrowing revenue (margin not eroding)
      3. gross profit growing (tracking sales)
      4. assets > liabilities
      5. liabilities NOT outgrowing assets
      6. a comfortable cash cushion (cash ratio >= 0.2, the scoring-rule floor
         also used for `cash_ratio`'s own sweet-spot band)
      7. operating cash flow trending up
    A missing input counts as a FAIL for that one check (conservative — absence
    of proof isn't proof of health) — EXCEPT cost_of_revenue/gross_profit: banks
    and BDCs don't report a COGS/gross-margin line at all (there's no "cost of
    revenue" for a bank), so a company missing BOTH isn't failing checks 2/3, the
    checklist's traditional-operating-company shape just doesn't apply to it —
    same as checks 1-7 needing 2 annual periods on file in the first place.
    """
    pair = _beneish_pair
    rev_t, rev_p = pair(fin, "total_revenue")
    if pd.isna(rev_t) or pd.isna(rev_p):
        return float("nan")
    cogs_t, cogs_p = pair(fin, "cost_of_revenue")
    gp_t, gp_p = pair(fin, "gross_profit")
    if pd.isna(cogs_t) and pd.isna(gp_t):
        return float("nan")
    ta_t, ta_p = pair(fin, "total_assets")
    tl_t, tl_p = pair(fin, "total_liabilities_net_minority_interest")
    ocf_t, ocf_p = pair(fin, "operating_cash_flow")

    checks = (
        rev_t > rev_p,
        _div(cogs_t, cogs_p) <= _div(rev_t, rev_p),
        gp_t > gp_p,
        pd.notna(ta_t) and pd.notna(tl_t) and ta_t > tl_t,
        _div(tl_t, tl_p) <= _div(ta_t, ta_p),
        pd.notna(cash_ratio) and cash_ratio >= 0.2,
        ocf_t > ocf_p,
    )
    return float(sum(bool(c) for c in checks))


def _income_block(fin, paid: pd.Series | None, price, ni_ttm, fcf_ttm, as_of) -> dict:
    """Dividend metrics from the full-history dividend events (no extra API)."""
    out = {
        "div_yield_ttm": float("nan"), "div_rate_ttm": float("nan"),
        "div_cagr_1y": float("nan"), "div_cagr_3y": float("nan"), "div_cagr_5y": float("nan"),
        "div_payout_ratio": float("nan"),
        "div_consecutive_years": float("nan"), "div_consistency": float("nan"),
        "div_coverage": float("nan"),
        "div_growth_vol": float("nan"), "div_growth_r2": float("nan"),
        "div_growth_cv": float("nan"), "div_growth_trend": float("nan"),
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
        vol, r2, cv, growth_trend = _trend_stats(by_year)
        out["div_growth_vol"] = vol
        out["div_growth_r2"] = r2
        out["div_growth_cv"] = cv
        out["div_growth_trend"] = growth_trend

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
