"""
Peer Strength (fix list §21, Group H part 2, 2026-11-16).

One 0-100 strength per company, from the measures that fit its type, each ranked
against its peers. The review tested it against later stock risk: the weaker
score went to the riskier stock in ~70% of pairs (50% = chance).

  * bank    — ROA and ROTCE (3-year medians), pre-tax margin (3-year median),
              tangible equity / tangible assets, tangible book per share growth;
  * insurer — pre-tax margin, debt / capital (lower), (pre-tax + interest) /
              interest, ROE steadiness (lower), book value per share growth;
  * reit    — EBITDA margin, debt / EBITDA (lower), EBITDA / interest,
              operating cash flow per share growth;
  * broker  — ROTCE, pre-tax margin, ROE steadiness (lower), tangible book per
              share growth;
  * ordinary / utility / telecom — ROIC, cash conversion (`ocf_to_ni_3y`), margin
              steadiness (`op_margin_std`, lower), debt / EBITDA (lower), interest
              cover, Piotroski.

Scoring: each measure's percentile (flipped where lower is better) within the
peer group; strength = the mean of the percentiles present. Needs 2/3 of the
type's measures (ordinary: 4 of 6). Peers: the industry when it holds >= 8
reference companies of the type family, else the sector (ordinary family) or the
whole type. Reference = listed companies (`peer_universe`) of at least $2B;
every company of the type is placed in that distribution. Mortgage REITs (a gate,
`economic_return_3y`) and shells get none.

`measures()` runs per symbol on the one-row-per-year table; `compute()` ranks on
the whole frame.
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd

from config import settings
from analysis_layer import _stats
from analysis_layer import metrics as M

FIELDS = ("net_income", "total_assets", "tangible_book_value", "pretax_income",
          "total_revenue", "total_debt", "stockholders_equity", "common_stock_equity",
          "interest_expense", "normalized_ebitda", "ebitda", "operating_income",
          "operating_cash_flow", "diluted_average_shares", "reconciled_depreciation",
          "depreciation_and_amortization", "goodwill_and_other_intangible_assets")

# measure -> higher is better?  Per type family, in the review's order.
_ORDINARY = {"roic": True, "ocf_to_ni_3y": True, "op_margin_std": False,
             "strength_debt_ebitda": False, "strength_interest_cover": True, "piotroski_f": True}
_SETS: dict[str, dict[str, bool]] = {
    "bank": {"roa_3y": True, "rotce_3y": True, "pretax_margin_3y": True,
             "tangible_equity_ratio": True, "tbv_ps_growth": True},
    "insurer": {"pretax_margin_3y": True, "debt_to_capital": False,
                "pretax_interest_cover": True, "roe_std": False, "bvps_growth": True},
    "reit": {"ebitda_margin_3y": True, "strength_debt_ebitda": False,
             "ebitda_interest_cover": True, "ocf_ps_growth": True},
    "broker": {"rotce_3y": True, "pretax_margin_3y": True, "roe_std": False,
               "tbv_ps_growth": True},
    "ordinary": _ORDINARY, "utility": _ORDINARY, "telecom": _ORDINARY,
}
_FAMILY = {"ordinary": "ordinary", "utility": "ordinary", "telecom": "ordinary",
           "bank": "bank", "insurer": "insurer", "reit": "reit", "broker": "broker"}
_MIN_MEASURES = {"ordinary": 4}   # else ceil(2/3 of the type's measures)

# Interest cover when there is no interest to pay: the best value, stored as a
# finite number (an infinity is not valid JSON on the way to the UI).
NO_INTEREST_COVER = 999.0

# A split event this close to 1:1 is a spin-off adjustment, not a share split.
_MIN_SPLIT_LOG = float(np.log(1.1))

# Stored per-symbol measures (the ordinary ones not listed here already exist).
_OWN = ("roa_3y", "rotce_3y", "pretax_margin_3y", "tangible_equity_ratio", "tbv_ps_growth",
        "debt_to_capital", "pretax_interest_cover", "roe_std", "bvps_growth",
        "ebitda_margin_3y", "ebitda_interest_cover", "ocf_ps_growth")


def _median3(num: pd.Series, den: pd.Series) -> float:
    """Median of num / den (%) over the newest 3 fiscal years with both (den > 0); >= 2."""
    r = (num / den.where(den > 0)).dropna().iloc[-3:] * 100
    return float(r.median()) if len(r) >= 2 else float("nan")


def _ebitda(tab: pd.DataFrame) -> pd.Series:
    """Normalized EBITDA, else reported; operating income + depreciation where that
    is below operating income (as the health check, Yahoo's EBITDA quirk)."""
    e = tab["normalized_ebitda"].fillna(tab["ebitda"])
    op = tab["operating_income"]
    da = tab["reconciled_depreciation"].fillna(tab["depreciation_and_amortization"]).abs().fillna(0)
    low = op.notna() & (e.isna() | (e < op))
    return e.where(~low, op + da)


def per_share_growth(values: pd.Series, shares: pd.Series, splits: pd.Series | None) -> float:
    """Robust growth (§16) of value / diluted average shares, %/yr, over the years
    the trend uses (~6). When a split falls inside them, each year's share count is
    put on the newest year's basis (the split candidate closest to it, as for EPS),
    NaN when even the best is > 2.5x off (a change of basis, §14). Without a split
    the counts are used as filed: REITs and banks issue shares steadily (Agree
    Realty 12x in 16 years), which a closeness test would misread. NaN with < 4
    points.

    Events within 10% of 1:1 are not splits here: Yahoo records a spin-off's price
    adjustment as one (Realty Income's Orion spin-off, 2021, "1.032"), which would
    switch on the basis test for a heavy share issuer and wrongly give NaN."""
    s = pd.concat([values, shares], axis=1, keys=["v", "n"]).dropna()
    s = s[s["n"] > 0].iloc[-(settings.GROWTH_TREND_YEARS + 1):]
    if len(s) < 4:
        return float("nan")
    real = (splits[(splits > 0) & (np.abs(np.log(splits.where(splits > 0))) >= _MIN_SPLIT_LOG)]
            if splits is not None and not splits.empty
            else pd.Series(dtype=float, index=pd.DatetimeIndex([])))
    last_d, last_n = s.index[-1], float(s["n"].iloc[-1])
    window = real[(real.index > s.index[0]) & (real.index <= last_d)]
    ps = []
    for d, row in s.iterrows():
        n = float(row["n"])
        c = 1.0
        if not window.empty:
            c = min(M.basis_candidates(window, d), key=lambda f: abs(np.log(n * f / last_n)))
            if abs(np.log(n * c / last_n)) > np.log(M._EPS_BASIS_MAX_OFF):
                return float("nan")
        ps.append(float(row["v"]) / (n * c))
    return M.robust_trend(pd.Series(ps, index=s.index))[2]


def measures(tab: pd.DataFrame, ctype: str | None, splits: pd.Series | None = None) -> dict:
    """The per-symbol measures this type needs that are not already columns."""
    out = dict.fromkeys(_OWN, float("nan"))
    fam = _FAMILY.get(ctype or "")
    if fam is None or fam == "ordinary" or tab.empty:
        return out
    ni, rev, eq = tab["net_income"], tab["total_revenue"], tab["stockholders_equity"].fillna(
        tab["common_stock_equity"])
    tbv, shares = tab["tangible_book_value"], tab["diluted_average_shares"]
    interest = tab["interest_expense"].abs()
    last = tab.iloc[-1]
    if fam in ("bank", "insurer", "broker"):
        out["pretax_margin_3y"] = _median3(tab["pretax_income"], rev)
    if fam in ("bank", "broker"):
        out["rotce_3y"] = _median3(ni, tbv)
        out["tbv_ps_growth"] = per_share_growth(tbv, shares, splits)
    if fam == "bank":
        out["roa_3y"] = _median3(ni, tab["total_assets"])
        row = tab[tab["tangible_book_value"].notna() & tab["total_assets"].notna()]
        if len(row):
            r = row.iloc[-1]
            gi = r.get("goodwill_and_other_intangible_assets")
            gi = float(gi) if gi is not None and pd.notna(gi) else 0.0
            ta = float(r["total_assets"]) - gi
            out["tangible_equity_ratio"] = float(r["tangible_book_value"]) / ta * 100 if ta > 0 else float("nan")
    if fam in ("insurer", "broker"):
        roe = (ni / eq.where(eq > 0)).dropna().iloc[-settings.HEALTH_STEADINESS_YEARS:] * 100
        if len(roe) >= settings.HEALTH_STEADINESS_MIN_YEARS:
            out["roe_std"] = float(roe.std(ddof=1))
    if fam == "insurer":
        debt, e = last.get("total_debt"), eq.iloc[-1]
        debt = 0.0 if pd.isna(debt) else float(debt)
        if pd.notna(e) and debt + float(e) > 0:
            out["debt_to_capital"] = debt / (debt + float(e)) * 100
        pt, it = last.get("pretax_income"), interest.iloc[-1]
        if pd.notna(pt):
            if pd.isna(it) or it == 0:     # no interest: best, only when profitable
                out["pretax_interest_cover"] = NO_INTEREST_COVER if pt > 0 else float("nan")
            else:
                out["pretax_interest_cover"] = min((float(pt) + float(it)) / float(it), NO_INTEREST_COVER)
        out["bvps_growth"] = per_share_growth(eq, shares, splits)
    if fam == "reit":
        e = _ebitda(tab)
        out["ebitda_margin_3y"] = _median3(e, rev)
        rows = e.dropna()
        if len(rows):
            ev, it = float(rows.iloc[-1]), interest.loc[rows.index[-1]]
            out["ebitda_interest_cover"] = (0.0 if ev <= 0 else
                                            NO_INTEREST_COVER if pd.isna(it) or it == 0
                                            else min(ev / float(it), NO_INTEREST_COVER))
        out["ocf_ps_growth"] = per_share_growth(tab["operating_cash_flow"], shares, splits)
    return out


def _ordinary_inputs(df: pd.DataFrame) -> pd.DataFrame:
    """Debt / EBITDA with EBITDA <= 0 as the worst value, and interest cover as
    stored (a missing interest line can't be told from none, so it stays missing)."""
    de = pd.to_numeric(df["debt_to_ebitda"], errors="coerce")
    neg = pd.to_numeric(df.get("ebitda_negative"), errors="coerce") == 1
    return pd.DataFrame({"strength_debt_ebitda": de.where(~neg, math.inf),
                         "strength_interest_cover": pd.to_numeric(df["interest_coverage"], errors="coerce")},
                        index=df.index)


def compute(df: pd.DataFrame) -> pd.DataFrame:
    """Add `peer_strength` (0-100) and `peer_strength_measures` (how many of the
    type's measures it rests on)."""
    df["peer_strength"] = np.nan
    df["peer_strength_measures"] = np.nan
    if "company_type" not in df.columns:
        return df
    work = pd.concat([df, _ordinary_inputs(df)], axis=1) if "debt_to_ebitda" in df.columns else df
    cap = pd.to_numeric(work.get("market_cap"), errors="coerce")
    listed = pd.to_numeric(work.get("peer_universe"), errors="coerce") == 1
    ref_all = listed & (cap >= settings.PEER_STRENGTH_MIN_CAP)
    fam = work["company_type"].map(_FAMILY)
    for family in sorted(set(fam.dropna())):
        rows = work.index[fam == family]
        sub = work.loc[rows]
        ref = ref_all.loc[rows]
        tiers = [sub["industry"]] + ([sub["sector"]] if family == "ordinary" else [])
        pct = {}
        for meas, higher in _SETS[family].items():
            if meas not in sub.columns:
                continue
            v = pd.to_numeric(sub[meas], errors="coerce").replace(-math.inf, np.nan)
            pct[meas] = _stats.percentile_vs_ref_tiered(
                v, tiers, ref, ascending=higher, min_n=settings.PEER_STRENGTH_MIN_PEERS)
        p = pd.DataFrame(pct, index=rows)
        n_poss = len(_SETS[family])
        need = _MIN_MEASURES.get(family, math.ceil(n_poss * 2 / 3))
        used = p.notna().sum(axis=1)
        df.loc[rows, "peer_strength"] = p.mean(axis=1).where(used >= need).round(1)
        df.loc[rows, "peer_strength_measures"] = used.astype(float)
    return df
