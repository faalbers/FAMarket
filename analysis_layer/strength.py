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
# Share count jump across a listing that marks the pre-listing years as another basis.
_LISTING_SHARE_JUMP = 1.5
# A > 2.5x share-count step is a change of basis unless the value moved at least
# this much the same way (a merger paid in stock).
_BREAK_VALUE_MOVE = 2.0

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


def per_share_growth(values: pd.Series, shares: pd.Series, splits: pd.Series | None,
                     listed_from: pd.Timestamp | None = None) -> float:
    """Robust growth (§16) of value / diluted average shares, %/yr, over the years
    the trend uses (~6); NaN with < 4 points or a change of basis."""
    ps = per_share_series(values, shares, splits, listed_from=listed_from)
    return M.robust_trend(ps)[2] if ps is not None else float("nan")


def per_share_series(values: pd.Series, shares: pd.Series, splits: pd.Series | None,
                     points: int | None = None,
                     listed_from: pd.Timestamp | None = None) -> pd.Series | None:
    """value / diluted average shares over the newest `points` fiscal years
    (default: the trend's ~6), from the first fiscal year ending a year after
    `listed_from` (the first price bar). Before a listing the filed share count
    leaves out the preferred stock that converts at it (Palantir: 577M shares for
    2019, 1.9B for 2021), and a bankruptcy relisting cancels the old shares — both
    read as flat or falling per-share values. The pre-listing years are dropped
    only when the count jumps > 1.5x across the listing: a spin-off files its old
    years on the shares it distributes (GE Vernova), which are fine.
    **Split basis:** each year's share count is put on the next newer year's basis
    — the split candidate (`M.basis_candidates`, as for EPS) closest to it. Splits
    dated after the newest year count too: SEC rows filed before them are on the old
    basis while Yahoo's are restated (CrowdStrike 4:1 Jul-2026, Booking 25:1
    Apr-2026; before 2026-11-22 those were ignored, Booking -28%/yr).

    **Basis breaks** (fix list, revenue-per-share seams part 3, 2026-11-22): walking
    back from the newest year, a year whose count is > 2.5x off the next newer kept
    year while the value moved < 2x is on another basis — an ADR's SEC years count
    ordinary shares, Yahoo's count ADSs (Alibaba 8 per ADS, JOYY 20); a Yahoo count
    4x too high (FirstEnergy); one class vs all (Carvana). That year and every older
    one are dropped, as EPS drops rows off its basis. A single odd year between two
    that agree is dropped alone (Travelers 2024, x100). Each year is tested against
    its NEWER NEIGHBOUR, not the newest year: REITs and banks issue shares steadily
    (Agree Realty 12x in 16 years) and no single year jumps 2.5x. Shares and value
    jumping together (a merger paid in stock) is real and kept. NaN with < 4 points.

    Events within 10% of 1:1 are not splits here: Yahoo records a spin-off's price
    adjustment as one (Realty Income's Orion spin-off, 2021, "1.032")."""
    s = pd.concat([values, shares], axis=1, keys=["v", "n"]).dropna()
    s = s[s["n"] > 0]
    if listed_from is not None and pd.notna(listed_from):
        listed = s.index >= pd.Timestamp(listed_from) + pd.Timedelta(days=365)
        if listed.any() and (~listed).any():
            jump = float(s["n"][listed].iloc[0]) / float(s["n"][~listed].iloc[-1])
            if abs(np.log(jump)) > np.log(_LISTING_SHARE_JUMP):
                s = s[listed]
    s = s.iloc[-(points or settings.GROWTH_TREND_YEARS + 1):]
    if len(s) < 4:
        return None
    real = (splits[(splits > 0) & (np.abs(np.log(splits.where(splits > 0))) >= _MIN_SPLIT_LOG)]
            if splits is not None and not splits.empty
            else pd.Series(dtype=float, index=pd.DatetimeIndex([])))
    real = real[real.index > s.index[0]].sort_index()
    dates, vals, counts = list(s.index), s["v"].to_numpy(float), s["n"].to_numpy(float)

    def on_basis(i: int, ref_d: pd.Timestamp, ref_n: float) -> float:
        """Year i's count on the basis of a newer year (ref_d, already adjusted to
        ref_n): the relative split factor closest to it."""
        rel = {a / b for a in M.basis_candidates(real, dates[i])
               for b in M.basis_candidates(real, ref_d)}
        return counts[i] * min(rel, key=lambda f: abs(np.log(counts[i] * f / ref_n)))

    def other_basis(i: int, n_adj: float, ref_v: float, ref_n: float) -> bool:
        if abs(np.log(n_adj / ref_n)) <= np.log(M._EPS_BASIS_MAX_OFF):
            return False
        v = vals[i]
        moved_together = (v > 0 and ref_v > 0 and abs(np.log(ref_v / v)) >= np.log(_BREAK_VALUE_MOVE)
                          and np.sign(np.log(ref_v / v)) == np.sign(np.log(ref_n / n_adj)))
        return not moved_together

    last = len(dates) - 1
    kept = {last: counts[last]}
    ref = last
    i = last - 1
    while i >= 0:
        n_adj = on_basis(i, dates[ref], kept[ref])
        if not other_basis(i, n_adj, vals[ref], kept[ref]):
            kept[i], ref = n_adj, i
            i -= 1
            continue
        # A lone odd year: the year before it is back near the newer neighbour's count
        # (closeness only — a value that moved with the shares doesn't excuse it).
        if i > 0:
            n_prev = on_basis(i - 1, dates[ref], kept[ref])
            if abs(np.log(n_prev / kept[ref])) <= np.log(M._EPS_BASIS_MAX_OFF):
                i -= 1  # skip year i; year i-1 is tested (and kept) next round
                continue
        break  # a change of basis: this year and every older one are dropped
    if len(kept) < 4:
        return None
    order = sorted(kept)
    return pd.Series([vals[k] / kept[k] for k in order], index=[dates[k] for k in order])


def measures(tab: pd.DataFrame, ctype: str | None, splits: pd.Series | None = None,
             listed_from: pd.Timestamp | None = None) -> dict:
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
        out["tbv_ps_growth"] = per_share_growth(tbv, shares, splits, listed_from)
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
        out["bvps_growth"] = per_share_growth(eq, shares, splits, listed_from)
    if fam == "reit":
        e = _ebitda(tab)
        out["ebitda_margin_3y"] = _median3(e, rev)
        rows = e.dropna()
        if len(rows):
            ev, it = float(rows.iloc[-1]), interest.loc[rows.index[-1]]
            out["ebitda_interest_cover"] = (0.0 if ev <= 0 else
                                            NO_INTEREST_COVER if pd.isna(it) or it == 0
                                            else min(ev / float(it), NO_INTEREST_COVER))
        out["ocf_ps_growth"] = per_share_growth(tab["operating_cash_flow"], shares, splits, listed_from)
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
