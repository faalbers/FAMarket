"""
Value-level cleaning rules for fetched data (data-system fix list, Group A).

Each rule turns a known Yahoo placeholder or impossible value into NULL (or into
the value it should have been), following the "NULL = not applicable" convention.
The same functions run in two places, so the two can never drift:

  * at import — the yfinance / EDGAR sanitize steps, on every new fetch;
  * once, over the stored rows — ``scripts/clean_stored_data.py`` (and
    ``scripts/fix_share_scale.py`` for ``repair_share_scale``).

Every function takes a DataFrame, returns ``(cleaned_frame, counts)`` where
``counts`` maps a rule name to the number of cells it changed. Columns a frame
doesn't have are skipped, so a rule is safe on any subset of a table. Import
callers ignore the counts (sanitize fixes are silent, per the logging policy);
the cleanup script reports them.

Rules and the evidence behind them: ``docs/fix_list_for_data_system_done.md``.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

Counts = dict[str, int]

# --- quotes ----------------------------------------------------------------- #
# Upper bounds past which a Yahoo ratio is a data error, not a real value.
BETA_ABS_MAX = 10.0          # |beta| above this is noise (ELOX showed -169,205)
PAYOUT_RATIO_MAX = 10.0      # a fraction: 10 = 1,000% of earnings
GROSS_MARGIN_MAX = 1.0       # a fraction: gross profit can't exceed revenue

# --- estimates -------------------------------------------------------------- #
# Value columns that hold a placeholder 0 when no analyst covers the horizon,
# keyed by the analyst-count column that tells whether the row is real.
_ESTIMATE_GROUPS: dict[str, tuple[str, ...]] = {
    "rev_num_analysts": ("rev_avg", "rev_low", "rev_high", "rev_year_ago", "rev_growth"),
    "eps_num_analysts": ("eps_avg", "eps_low", "eps_high", "eps_year_ago", "eps_growth"),
}


def _null_where(df: pd.DataFrame, col: str, mask: pd.Series) -> int:
    """Set ``df[col]`` to NULL where ``mask`` is true; return how many cells changed."""
    mask = mask.fillna(False).astype(bool)
    n = int(mask.sum())
    if n:
        df[col] = df[col].astype(object)
        df.loc[mask, col] = None
    return n


def _num(df: pd.DataFrame, col: str) -> pd.Series:
    return pd.to_numeric(df[col], errors="coerce")


def clean_quotes(df: pd.DataFrame) -> tuple[pd.DataFrame, Counts]:
    """Quote snapshot rules (fix list §5, §7, §25.5)."""
    counts: Counts = {}
    if "recommendationMean" in df.columns:
        # The scale is 1 (strong buy) .. 5 (sell); 0 is Yahoo's "no rating".
        counts["recommendationMean_zero"] = _null_where(
            df, "recommendationMean", _num(df, "recommendationMean") == 0)
    if "beta" in df.columns:
        counts["beta_out_of_range"] = _null_where(
            df, "beta", _num(df, "beta").abs() > BETA_ABS_MAX)
    if "payoutRatio" in df.columns:
        counts["payoutRatio_out_of_range"] = _null_where(
            df, "payoutRatio", _num(df, "payoutRatio") > PAYOUT_RATIO_MAX)
    if "grossMargins" in df.columns:
        counts["grossMargins_out_of_range"] = _null_where(
            df, "grossMargins", _num(df, "grossMargins") > GROSS_MARGIN_MAX)
    if "shortName" in df.columns:
        if "longName" not in df.columns:
            df["longName"] = None
        long_missing = df["longName"].isna() | (df["longName"].astype(str).str.strip() == "")
        short_present = df["shortName"].notna() & (df["shortName"].astype(str).str.strip() != "")
        fill = long_missing & short_present
        n = int(fill.sum())
        if n:
            df["longName"] = df["longName"].astype(object)
            df.loc[fill, "longName"] = df.loc[fill, "shortName"]
        counts["longName_from_shortName"] = n
    return df, counts


def clean_estimates(df: pd.DataFrame) -> tuple[pd.DataFrame, Counts]:
    """Analyst-estimate rules (fix list §5).

    A 0 in a revenue/EPS estimate column is a placeholder only when the horizon has
    no analysts (count 0 or missing). With analysts, 0 can be a real forecast — a
    pre-revenue biotech forecast at $0 revenue — so those rows are left alone.
    """
    counts: Counts = {}
    for count_col, value_cols in _ESTIMATE_GROUPS.items():
        if count_col in df.columns:
            no_analysts = _num(df, count_col).fillna(0) == 0
        else:
            no_analysts = pd.Series(True, index=df.index)
        n = 0
        for col in value_cols:
            if col in df.columns:
                n += _null_where(df, col, no_analysts & (_num(df, col) == 0))
        counts[f"{count_col.split('_')[0]}_zero_no_analysts"] = n
    return df, counts


# A Yahoo statement period with none of these is a leftover, not data (fix list §1).
CORE_FIELDS = ("total_revenue", "net_income", "total_assets")


def drop_empty_periods(df: pd.DataFrame) -> tuple[pd.DataFrame, Counts]:
    """Drop Yahoo periods with no revenue, net income AND total assets (fix list §1).

    Yahoo lists its oldest year (and some odd quarters) with only a few minor lines.
    Stored, such a row "owns" the period, so the EDGAR backfill never fills it — and
    if EDGAR did, the next Yahoo fetch would upsert the empty version back over it.
    Yahoo-only: an EDGAR row is never dropped by this rule.
    """
    present = [c for c in CORE_FIELDS if c in df.columns]
    if present:
        empty = df[present].isna().all(axis=1)
    else:
        empty = pd.Series(True, index=df.index)
    return df[~empty].copy(), {"empty_periods_dropped": int(empty.sum())}


def clean_financials(df: pd.DataFrame) -> tuple[pd.DataFrame, Counts]:
    """Statement rules (fix list §24).

    ``stockholders_equity`` = 0 is a placeholder: Yahoo returns it for partnerships
    (equity is "partners' capital") and some new listings, and a few EDGAR rows
    carry it too. Take ``common_stock_equity`` when it holds a non-zero figure
    (negative equity included), else store NULL.
    """
    counts: Counts = {"stockholders_equity_from_common": 0, "stockholders_equity_zero": 0}
    if "stockholders_equity" not in df.columns:
        return df, counts
    zero = (_num(df, "stockholders_equity") == 0).fillna(False)
    if not zero.any():
        return df, counts
    if "common_stock_equity" in df.columns:
        common = _num(df, "common_stock_equity")
        use_common = zero & common.notna() & (common != 0)
    else:
        common = pd.Series(float("nan"), index=df.index)
        use_common = pd.Series(False, index=df.index)
    to_null = zero & ~use_common
    df["stockholders_equity"] = df["stockholders_equity"].astype(object)
    df.loc[use_common, "stockholders_equity"] = common[use_common]
    df.loc[to_null, "stockholders_equity"] = None
    counts["stockholders_equity_from_common"] = int(use_common.sum())
    counts["stockholders_equity_zero"] = int(to_null.sum())
    return df, counts


# --- share-count scale (SEC filer tagging errors) ---------------------------- #
# Filers sometimes tag a share count at the wrong scale: Kohl's FY2021 diluted
# shares as 148 (148 million), Teva 2017 as 1,016. The error is always an exact
# power of 1,000, and only in EDGAR rows. Evidence: done file, revenue-per-share fix.
AVG_SHARE_COLS = (("diluted_average_shares", "diluted_eps"), ("basic_average_shares", "basic_eps"))
INSTANT_SHARE_COLS = ("ordinary_shares_number", "share_issued")
SHARE_SCALE_COLS = ("symbol", "period_end", "source", "net_income", "diluted_eps", "basic_eps",
                    *(c for c, _ in AVG_SHARE_COLS), *INSTANT_SHARE_COLS)
_ANCHOR_AGREE = 0.1        # log10: a count within ~26% of net income / EPS is an anchor
_SCALE_TOL = 0.35          # log10: an off-by-1000^k value sits within this of 3k
_INSTANT_TOL = 0.15        # log10: period-end count vs the same row's average count
_ANCHOR_MAX_GAP = pd.Timedelta(days=3 * 366)
_MIN_EPS = 0.05            # smaller EPS values are too rounded for the NI / EPS check
_SHARE_SCALE_ROUNDS = 10


def _log10(s: pd.Series) -> pd.Series:
    s = pd.to_numeric(s, errors="coerce")
    return np.log10(s.where(s > 0))


def _power_k(off: pd.Series, tol: float) -> pd.Series:
    """k where `off` (log10) sits within `tol` of 3k, k != 0; else 0."""
    k = np.round(off / 3)
    return k.where((k != 0) & ((off - 3 * k).abs() < tol), 0).fillna(0)


def _nearest(df: pd.DataFrame, lg: pd.Series, anchors: pd.Series) -> pd.Series:
    """log10 value of the nearest anchor row of the same symbol (by period_end,
    within `_ANCHOR_MAX_GAP`), aligned to df's index."""
    a = (df.loc[anchors, ["symbol", "_pe"]].assign(_alg=lg[anchors])
         .rename(columns={"_pe": "_ape"}).sort_values("_ape"))
    q = df[["symbol", "_pe"]].assign(_i=np.arange(len(df))).sort_values("_pe")
    if a.empty:
        return pd.Series(np.nan, index=df.index)
    m = pd.merge_asof(q, a, left_on="_pe", right_on="_ape", by="symbol",
                      direction="nearest", tolerance=_ANCHOR_MAX_GAP)
    return pd.Series(m.sort_values("_i")["_alg"].to_numpy(), index=df.index)


def repair_share_scale(df: pd.DataFrame) -> tuple[pd.DataFrame, Counts]:
    """Rescale EDGAR share counts tagged at the wrong power of 1,000.

    * Anchors: EDGAR rows whose average share count agrees with the row's own
      net income / EPS (same filing, so the same split basis). Yahoo rows are
      never anchors: Yahoo re-states old counts for later splits (ParkerVision's
      1-for-100), while EDGAR keeps each count as first filed.
    * Every other EDGAR average count off from the nearest anchor (same symbol,
      within 3 years) by close to an exact 1,000^k is divided by 1,000^k — unless
      the row's own NI / EPS cleanly says the value is right as it is or wrong
      the other way (Loar's pre-IPO units).
    * Period-end counts (`ordinary_shares_number`, `share_issued`): against the
      same row's average count when that one is an anchor or was repaired, else
      the nearest anchor among the period-end counts.
    * Only EDGAR rows change. EPS values tagged at the wrong scale (ICE 2013
      quarters at 1,850,000) are not repaired; they just can't be anchors.

    A repaired row that now agrees with its EPS becomes an anchor for its
    neighbours, so the pass repeats until nothing changes (5 rounds on the
    stored table, 2026-11-22; each later-round change moved closer to its own
    row's EPS). Works on any number of symbols at once; a second run changes
    nothing.
    """
    cols = [c for c, _ in AVG_SHARE_COLS if c in df.columns] +            [c for c in INSTANT_SHARE_COLS if c in df.columns]
    counts: Counts = {c: 0 for c in cols}
    if df.empty or not cols or "symbol" not in df.columns or "period_end" not in df.columns:
        return df, counts
    before = df[cols].apply(pd.to_numeric, errors="coerce")
    df["_pe"] = pd.to_datetime(df["period_end"], errors="coerce")
    edgar = (df["source"] == "edgar") if "source" in df.columns else pd.Series(True, index=df.index)
    edgar &= df["_pe"].notna()
    ni = _num(df, "net_income") if "net_income" in df.columns else pd.Series(np.nan, index=df.index)
    for _ in range(_SHARE_SCALE_ROUNDS):
        if not _share_scale_pass(df, edgar, ni):
            break
    df.drop(columns="_pe", inplace=True)
    for c in cols:
        after = pd.to_numeric(df[c], errors="coerce")
        counts[c] = int((~((before[c].isna() & after.isna()) | np.isclose(before[c], after))).sum())
    return df, counts


def _share_scale_pass(df: pd.DataFrame, edgar: pd.Series, ni: pd.Series) -> int:
    """One pass of `repair_share_scale`; returns the number of cells changed."""
    changed = 0

    ok_avg = pd.Series(False, index=df.index)
    for col, eps_col in AVG_SHARE_COLS:
        if col not in df.columns:
            continue
        lg = _log10(df[col])
        if eps_col in df.columns:
            eps = _num(df, eps_col)
            implied = (ni / eps).where((eps.abs() >= _MIN_EPS) & (ni.abs() > 0)
                                       & (np.sign(ni) == np.sign(eps)))
        else:
            implied = pd.Series(np.nan, index=df.index)
        vs_eps = lg - _log10(implied)
        anchors = edgar & lg.notna() & (vs_eps.abs() < _ANCHOR_AGREE)
        k = _power_k(lg - _nearest(df, lg, anchors), _SCALE_TOL)
        clean_eps = (vs_eps - 3 * np.round(vs_eps / 3)).abs() < _ANCHOR_AGREE
        worse = vs_eps.notna() & clean_eps & ((vs_eps - 3 * k).abs() > vs_eps.abs())
        fix = edgar & ~anchors & ~worse & (k != 0)
        if fix.any():
            df[col] = pd.to_numeric(df[col], errors="coerce")
            df.loc[fix, col] = df.loc[fix, col] / 10 ** (3 * k[fix])
        changed += int(fix.sum())
        if col == "diluted_average_shares":
            ok_avg = anchors | fix

    ref = _log10(df["diluted_average_shares"].where(ok_avg)) if "diluted_average_shares" in df.columns \
        else pd.Series(np.nan, index=df.index)
    for col in INSTANT_SHARE_COLS:
        if col not in df.columns:
            continue
        lg = _log10(df[col])
        anchors = edgar & lg.notna() & ((lg - ref).abs() < _INSTANT_TOL)
        k = _power_k(lg - ref, _INSTANT_TOL)
        k = k.where(k != 0, _power_k(lg - _nearest(df, lg, anchors), _SCALE_TOL))
        fix = edgar & ~anchors & (k != 0)
        if fix.any():
            df[col] = pd.to_numeric(df[col], errors="coerce")
            df.loc[fix, col] = df.loc[fix, col] / 10 ** (3 * k[fix])
        changed += int(fix.sum())
    return changed
