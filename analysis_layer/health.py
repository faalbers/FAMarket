"""
Company health checks (fix list §21, Group H part 1, 2026-11-15).

Three per-company warnings, judged on annual statements:

  * health_check (ok / warn / fail) + health_reason — a danger gate with lines set
    per company type. Ordinary companies get debt / EBITDA and interest-cover lines
    from how steady their INDUSTRY's operating margin is (the S&P business-risk
    idea: a steady business can carry more debt). Utilities, telecoms, REITs,
    insurers, banks, brokers and mortgage REITs have their own tests. A missing
    figure means "can't judge" that test, never fail.
  * margin_peak (0/1) + margin_vs_normal — an operating margin far above its own
    normal, which tends to fade (cyclical peak).
  * div_outgrowing_earnings (0/1) — the regular dividend growing much faster than
    net income, with a high payout or shrinking earnings.

Flow: `assess()` runs per symbol inside the pipeline loop and returns the finished
columns plus, for ordinary companies, the inputs still waiting for their
industry's line; `finalize()` runs on the whole frame, sorts industries into
steady / normal / swinging thirds by the median `op_margin_std` of their listed
companies, and judges those.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
from scipy.stats import theilslopes

from config import settings
from analysis_layer import _periods as P
from analysis_layer import metrics as M

ORDINARY, UTILITY, TELECOM = "ordinary", "utility", "telecom"
BANK, INSURER, REIT, MREIT, BROKER, SHELL = (
    "bank", "insurer", "reit", "mortgage_reit", "broker", "shell")
_LINED = (ORDINARY, UTILITY, TELECOM)          # judged on debt / EBITDA + interest cover
_BROKER_INDUSTRIES = ("Capital Markets", "Financial Conglomerates", "Mortgage Finance")
_UTILITY_INDUSTRIES = ("Utilities - Regulated Electric", "Utilities - Regulated Gas",
                       "Utilities - Regulated Water", "Utilities - Diversified")

_FIELDS = ("total_revenue", "operating_income", "pretax_income", "net_income",
           "normalized_ebitda", "ebitda", "total_debt", "interest_expense",
           "stockholders_equity", "common_stock_equity", "total_assets",
           "tangible_book_value", "goodwill_and_other_intangible_assets",
           "ordinary_shares_number", "cash_dividends_paid",
           "reconciled_depreciation", "depreciation_and_amortization")

OK, WARN, FAIL = "ok", "warn", "fail"


def company_type(security_type: str | None, industry: str | None) -> str | None:
    """The health-check type from the Yahoo industry (after overrides); None for
    anything that is not a stock / ADR or has no industry."""
    if security_type not in ("stock", "adr") or not isinstance(industry, str) or not industry:
        return None
    if industry in _UTILITY_INDUSTRIES:
        return UTILITY
    if industry == "Telecom Services":
        return TELECOM
    if industry.startswith("Banks - "):
        return BANK
    if industry.startswith("Insurance - "):
        return INSURER
    if industry == "REIT - Mortgage":
        return MREIT
    if industry.startswith("REIT - "):
        return REIT
    if industry in _BROKER_INDUSTRIES:
        return BROKER
    if industry == "Shell Companies":
        return SHELL
    return ORDINARY


@dataclass
class Pending:
    """An ordinary company / utility / telecom waiting for its debt line."""
    ctype: str
    op_income: float      # latest fiscal year
    debt_ebitda: float    # NaN = can't judge; inf = EBITDA <= 0 with debt
    cover: float          # operating income / interest; inf = no interest; NaN = can't judge


def _verdict(fails: list[str], warns: list[str]) -> tuple[str, str | None]:
    if fails:
        return FAIL, "; ".join(fails + warns)
    if warns:
        return WARN, "; ".join(warns)
    return OK, None


def _ratio(a: float, b: float) -> float:
    return a / b if pd.notna(a) and pd.notna(b) and b != 0 else float("nan")


def _latest(tab: pd.DataFrame, key: str) -> pd.Series | None:
    """The newest fiscal year that has `key` (the "latest clean year")."""
    rows = tab[tab[key].notna()] if key in tab.columns else tab.iloc[0:0]
    return rows.iloc[-1] if len(rows) else None


def _ebitda(row: pd.Series) -> float:
    """Normalized EBITDA, else reported (as §20). Yahoo's EBITDA starts from EBIT =
    pre-tax income + interest, so a non-operating loss can put it BELOW operating
    income (210 listed companies; Carvana FY2025 -$0.11B vs +$1.88B operating
    income). Then operating income + depreciation is used instead."""
    e = row.get("normalized_ebitda")
    e = float(e) if pd.notna(e) else float(row.get("ebitda", float("nan")))
    op = row.get("operating_income")
    if pd.notna(op) and (pd.isna(e) or e < float(op)):
        da = row.get("reconciled_depreciation")
        da = da if pd.notna(da) else row.get("depreciation_and_amortization")
        e = float(op) + (abs(float(da)) if pd.notna(da) else 0.0)
    return e


def _equity(row: pd.Series) -> float:
    e = row.get("stockholders_equity")
    return float(e) if pd.notna(e) else float(row.get("common_stock_equity", float("nan")))


def _interest(row: pd.Series) -> float:
    i = row.get("interest_expense")
    return abs(float(i)) if pd.notna(i) else float("nan")


def _loss_years(tab: pd.DataFrame) -> tuple[int, int]:
    """(loss years, years) among the last three fiscal years with net income."""
    ni = tab["net_income"].dropna().iloc[-3:]
    return int((ni < 0).sum()), len(ni)


def _lined_inputs(tab: pd.DataFrame, ctype: str) -> Pending | None:
    row = _latest(tab, "operating_income")
    if row is None:
        return None
    ebitda, debt, interest = _ebitda(row), float(row.get("total_debt", np.nan)), _interest(row)
    if pd.isna(debt):
        de = float("nan")
    elif debt <= 0:
        de = 0.0
    elif pd.notna(ebitda) and ebitda > 0:
        de = debt / ebitda
    elif pd.notna(ebitda):
        de = float("inf")          # EBITDA <= 0 with debt: the worst debt ratio
    else:
        de = float("nan")
    op = float(row["operating_income"])
    cover = (float("inf") if interest == 0 else op / interest) if pd.notna(interest) else float("nan")
    return Pending(ctype, op, de, cover)


def judge(p: Pending, line_name: str) -> tuple[str, str | None]:
    """Verdict for an ordinary company / utility / telecom under its lines."""
    max_de, min_cover = settings.HEALTH_LINES[line_name]
    fails, warns = [], []
    if p.op_income < 0:
        fails.append("operating loss")
    # Below the line = warn; fail only when operating profit barely covers the
    # interest (decided 2026-11-15: the line alone failed 31% of companies >= $2B,
    # Amgen and Synopsys among them).
    if pd.notna(p.cover) and p.cover < settings.HEALTH_COVER_FAIL and p.op_income >= 0:
        fails.append(f"interest cover {p.cover:.1f}x < {settings.HEALTH_COVER_FAIL:g}x")
    elif pd.notna(p.cover) and p.cover < min_cover and p.op_income >= 0:
        warns.append(f"interest cover {p.cover:.1f}x < {min_cover:g}x")
    if pd.notna(p.debt_ebitda) and p.debt_ebitda > max_de:
        warns.append("EBITDA <= 0 with debt" if np.isinf(p.debt_ebitda)
                     else f"debt/EBITDA {p.debt_ebitda:.1f}x > {max_de:g}x")
    v, r = _verdict(fails, warns)
    if r and p.ctype == ORDINARY:
        r = f"{r} ({line_name} industry)"
    return v, r


def _reit(tab: pd.DataFrame) -> tuple[str | None, str | None]:
    row = _latest(tab, "normalized_ebitda" if tab["normalized_ebitda"].notna().any() else "ebitda")
    if row is None:
        return None, None
    ebitda, debt, interest = _ebitda(row), float(row.get("total_debt", np.nan)), _interest(row)
    fails, warns = [], []
    if ebitda <= 0:
        fails.append("EBITDA <= 0")
    else:
        cover = float("inf") if interest == 0 else _ratio(ebitda, interest)
        if pd.notna(cover) and cover < settings.HEALTH_REIT_COVER_FAIL:
            fails.append(f"EBITDA/interest {cover:.1f}x < {settings.HEALTH_REIT_COVER_FAIL:g}x")
        elif pd.notna(cover) and cover < settings.HEALTH_REIT_COVER_WARN:
            warns.append(f"EBITDA/interest {cover:.1f}x < {settings.HEALTH_REIT_COVER_WARN:g}x")
        de = _ratio(debt, ebitda)
        if pd.notna(de) and de > settings.HEALTH_REIT_DEBT_EBITDA_WARN:
            warns.append(f"debt/EBITDA {de:.1f}x > {settings.HEALTH_REIT_DEBT_EBITDA_WARN:g}x")
    return _verdict(fails, warns)


def _insurer(tab: pd.DataFrame) -> tuple[str | None, str | None]:
    row = _latest(tab, "pretax_income")
    if row is None:
        return None, None
    fails, warns = [], []
    equity, debt, interest = _equity(row), float(row.get("total_debt", np.nan)), _interest(row)
    if pd.notna(equity) and equity <= 0:
        fails.append("equity <= 0")
    losses, years = _loss_years(tab)
    if years >= 2 and losses >= 2:
        fails.append(f"net loss in {losses} of the last {years} years")
    elif years and tab["net_income"].dropna().iloc[-1] < 0:
        warns.append("latest year a net loss")
    if pd.notna(interest) and interest > 0:
        cover = (float(row["pretax_income"]) + interest) / interest
        if cover < settings.HEALTH_INSURER_COVER_FAIL:
            fails.append(f"interest cover {cover:.1f}x < {settings.HEALTH_INSURER_COVER_FAIL:g}x")
        elif cover < settings.HEALTH_INSURER_COVER_WARN:
            warns.append(f"interest cover {cover:.1f}x < {settings.HEALTH_INSURER_COVER_WARN:g}x")
    if pd.notna(debt) and pd.notna(equity) and debt + equity > 0:
        dc = debt / (debt + equity) * 100
        if dc > settings.HEALTH_INSURER_DEBT_CAPITAL_WARN:
            warns.append(f"debt {dc:.0f}% of capital > {settings.HEALTH_INSURER_DEBT_CAPITAL_WARN:g}%")
    return _verdict(fails, warns)


def _bank(tab: pd.DataFrame) -> tuple[str | None, str | None]:
    """Warn only: loan quality and capital ratios are not in the data."""
    row = _latest(tab, "net_income")
    if row is None:
        return None, None
    warns = []
    ni, assets = float(row["net_income"]), float(row.get("total_assets", np.nan))
    if ni < 0:
        warns.append("net loss")
    roa = _ratio(ni, assets) * 100
    if pd.notna(roa) and ni >= 0 and roa < settings.HEALTH_BANK_ROA_WARN:
        warns.append(f"ROA {roa:.2f}% < {settings.HEALTH_BANK_ROA_WARN:g}%")
    gi = row.get("goodwill_and_other_intangible_assets")
    tce = _ratio(float(row.get("tangible_book_value", np.nan)),
                 assets - (float(gi) if pd.notna(gi) else 0.0)) * 100
    if pd.notna(tce) and tce < settings.HEALTH_BANK_TCE_WARN:
        warns.append(f"tangible equity {tce:.1f}% < {settings.HEALTH_BANK_TCE_WARN:g}%")
    return _verdict([], warns)


def _broker(tab: pd.DataFrame) -> tuple[str | None, str | None]:
    row = _latest(tab, "net_income")
    if row is None:
        return None, None
    fails, warns = [], []
    equity = _equity(row)
    if pd.notna(equity) and equity <= 0:
        fails.append("equity <= 0")
    losses, years = _loss_years(tab)
    if years >= 2 and losses >= 2:
        fails.append(f"net loss in {losses} of the last {years} years")
    elif float(row["net_income"]) < 0:
        warns.append("latest year a net loss")
    return _verdict(fails, warns)


def _mreit(tab: pd.DataFrame, dividends: pd.Series | None, splits: pd.Series | None,
           shares_now: float) -> tuple[str | None, str | None, float]:
    """Economic return over up to 3 years = (change in book value per share +
    dividends per share paid in between) / starting book value per share. Book
    value per share = common equity / year-end shares, each year's shares put on
    today's split basis (the candidate closest to today's count, as for EPS)."""
    bv = tab[["common_stock_equity", "ordinary_shares_number"]].dropna()
    bv = bv[(bv["common_stock_equity"] > 0) & (bv["ordinary_shares_number"] > 0)]
    if len(bv) < 2 or pd.isna(shares_now) or shares_now <= 0:
        return None, None, float("nan")
    bv = bv.iloc[-4:]                                   # up to 3 years of change
    real = (splits[(splits != 0) & (splits != 1)] if splits is not None and not splits.empty
            else pd.Series(dtype=float, index=pd.DatetimeIndex([])))

    def bvps(d: pd.Timestamp) -> float:
        sh = float(bv.at[d, "ordinary_shares_number"])
        c = min(M.basis_candidates(real, d), key=lambda f: abs(np.log(sh * f / shares_now)))
        return float(bv.at[d, "common_stock_equity"]) / (sh * c)

    d0, d1 = bv.index[0], bv.index[-1]
    paid = 0.0
    if dividends is not None and not dividends.empty:
        paid = float(dividends[(dividends.index > d0) & (dividends.index <= d1)].sum())
    b0, b1 = bvps(d0), bvps(d1)
    econ = (b1 - b0 + paid) / b0 * 100
    fails, warns = [], []
    if econ < settings.HEALTH_MREIT_ECON_FAIL:
        fails.append(f"economic return {econ:.0f}% < {settings.HEALTH_MREIT_ECON_FAIL:g}%")
    elif econ < 0:
        warns.append(f"economic return {econ:.0f}%")
    recent = tab.loc[tab.index > d0]
    ni, divs = recent["net_income"].sum(min_count=1), recent["cash_dividends_paid"].abs().sum(min_count=1)
    if pd.notna(ni) and pd.notna(divs) and divs > ni:
        warns.append("dividends paid above net income")
    v, r = _verdict(fails, warns)
    return v, r, econ


def _margins(tab: pd.DataFrame, field: str) -> tuple[np.ndarray, np.ndarray]:
    """(fractional years, margin %) of the years with `field` and revenue > 0.
    numpy, not pandas: the pandas version was most of `assess`'s 3 ms a symbol."""
    v = tab[field].to_numpy()
    rev = tab["total_revenue"].to_numpy()
    ok = ~np.isnan(v) & (rev > 0)
    if not ok.any():
        return np.empty(0), np.empty(0)
    return M.fractional_years(tab.index[ok]), v[ok] / rev[ok] * 100


def _peak(tab: pd.DataFrame, op: tuple[np.ndarray, np.ndarray]) -> tuple[float, float]:
    """(margin_peak 0/1, margin - normal in points). Operating margin (`op`, from
    `_margins`); the pre-tax margin when operating income has too few earlier
    years (some miners)."""
    nan = float("nan")
    t, m = op
    if len(m) - 1 < settings.PEAK_MIN_EARLIER_YEARS:
        t, m = _margins(tab, "pretax_income")
    if len(m) - 1 < settings.PEAK_MIN_EARLIER_YEARS:
        return nan, nan
    keep = settings.HEALTH_STEADINESS_YEARS + 1
    t, m = t[-keep:], m[-keep:]
    now, earlier = float(m[-1]), m[:-1]
    normal = float(np.median(earlier))
    if normal <= 0:   # a loss-maker turning profitable is a turnaround, not a peak
        return 0.0, now - normal
    slope, intercept, _, _ = theilslopes(earlier, t[:-1])
    trend_now = intercept + slope * t[-1]
    gap = settings.PEAK_MARGIN_GAP_PP
    peak = (now - normal > gap and now > settings.PEAK_MARGIN_RATIO * normal
            and now - trend_now > gap)
    return float(peak), now - normal


def _steadiness(op: tuple[np.ndarray, np.ndarray]) -> float:
    """Std dev of the yearly operating margin over the last 5-10 years (points)."""
    m = op[1][-settings.HEALTH_STEADINESS_YEARS:]
    if len(m) < settings.HEALTH_STEADINESS_MIN_YEARS:
        return float("nan")
    return float(np.std(m, ddof=1))


def _div_outgrowing(tab: pd.DataFrame, m: dict) -> float:
    """1 when a RISING regular dividend grows > DIV_OUTGROW_GAP_PP a year faster
    than net income AND the payout is >= DIV_OUTGROW_PAYOUT % or the latest year is
    a loss. Narrowed from the review's "or shrinking earnings" (2026-11-15): with a
    low payout (Thermo Fisher 9%) falling earnings are no danger to the dividend;
    that branch was 262 of 427 flags. NaN for non-payers or without both rates."""
    div_g, rate = m.get("div_growth_trend"), m.get("div_rate")
    if rate is None or pd.isna(rate) or rate <= 0 or div_g is None or pd.isna(div_g):
        return float("nan")
    ni = tab["net_income"].dropna()
    if ni.empty:
        return float("nan")
    if div_g <= 0:
        return 0.0
    loss = ni.iloc[-1] <= 0
    ni_g = M.robust_trend(ni)[2]
    if pd.isna(ni_g):
        return float(div_g > settings.DIV_OUTGROW_GAP_PP) if loss else float("nan")
    payout = m.get("div_payout_ratio")
    high_payout = payout is not None and pd.notna(payout) and payout >= settings.DIV_OUTGROW_PAYOUT
    return float(div_g > ni_g + settings.DIV_OUTGROW_GAP_PP and (high_payout or loss))


def assess(fin, ctype: str | None, m: dict, dividends: pd.Series | None = None,
           splits: pd.Series | None = None) -> tuple[dict, Pending | None]:
    """Per-symbol columns, plus the inputs an ordinary company / utility / telecom
    still needs its industry's line for (see `finalize`)."""
    out: dict = {"company_type": ctype, "health_check": None, "health_reason": None,
                 "op_margin_std": float("nan"), "margin_peak": float("nan"),
                 "margin_vs_normal": float("nan"), "div_outgrowing_earnings": float("nan"),
                 "economic_return_3y": float("nan")}
    if ctype is None:
        return out, None
    tab = P.annual_table(fin, _FIELDS)
    if tab.empty:
        return out, None
    op = _margins(tab, "operating_income")
    out["op_margin_std"] = _steadiness(op)
    out["div_outgrowing_earnings"] = _div_outgrowing(tab, m)
    pending = None
    if ctype in _LINED:
        out["margin_peak"], out["margin_vs_normal"] = _peak(tab, op)
        pending = _lined_inputs(tab, ctype)
    elif ctype == REIT:
        out["health_check"], out["health_reason"] = _reit(tab)
    elif ctype == INSURER:
        out["health_check"], out["health_reason"] = _insurer(tab)
    elif ctype == BANK:
        out["health_check"], out["health_reason"] = _bank(tab)
    elif ctype == BROKER:
        out["health_check"], out["health_reason"] = _broker(tab)
    elif ctype == MREIT:
        v, r, econ = _mreit(tab, dividends, splits, m.get("_shares", float("nan")))
        out["health_check"], out["health_reason"], out["economic_return_3y"] = v, r, econ
    return out, pending


def industry_classes(df: pd.DataFrame) -> dict[str, str]:
    """industry -> steady / normal / swinging: thirds of the industries' median
    `op_margin_std` over their listed ordinary companies (>= MIN_PEERS each)."""
    if "op_margin_std" not in df.columns or "company_type" not in df.columns:
        return {}
    base = df[(df["company_type"] == ORDINARY) & df["op_margin_std"].notna()]
    if "peer_universe" in df.columns:
        base = base[pd.to_numeric(base["peer_universe"], errors="coerce") == 1]
    g = base.groupby("industry")["op_margin_std"].agg(["median", "size"])
    g = g[g["size"] >= settings.HEALTH_STEADINESS_MIN_PEERS]["median"]
    if g.empty:
        return {}
    lo, hi = g.quantile(1 / 3), g.quantile(2 / 3)
    return {ind: ("steady" if v <= lo else "swinging" if v > hi else "normal")
            for ind, v in g.items()}


def finalize(df: pd.DataFrame, pending: dict[str, Pending]) -> pd.DataFrame:
    """Judge the waiting ordinary companies / utilities / telecoms (by symbol)."""
    if not pending:
        return df
    classes = industry_classes(df)
    ind = df.set_index("symbol")["industry"] if "industry" in df.columns else pd.Series(dtype=object)
    verdicts = {}
    for sym, p in pending.items():
        line = p.ctype if p.ctype != ORDINARY else classes.get(ind.get(sym), "normal")
        verdicts[sym] = judge(p, line)
    idx = df["symbol"].map(lambda s: s in verdicts)
    df.loc[idx, "health_check"] = df.loc[idx, "symbol"].map(lambda s: verdicts[s][0])
    df.loc[idx, "health_reason"] = df.loc[idx, "symbol"].map(lambda s: verdicts[s][1])
    return df
