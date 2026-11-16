"""
Cross-symbol statistics (Analysis helper) — shared by peers.py and scoring.py.

These operate on a column of the assembled universe frame, not on one symbol:
  * relative_to_group_median — each value's % distance from its peer-group median
    (peers.py: _vs_sector / _vs_industry).
  * percentile_rank          — each value's 0-100 rank within a group (scoring.py).

Both ignore NaN (so non-applicable securities don't distort a group) and preserve
NaN in the output where a value is missing or its group is too small.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def relative_to_group_median(
    values: pd.Series, groups: pd.Series, min_n: int,
    ref: pd.Series | None = None,
) -> pd.Series:
    """(value − group median) / |group median| × 100, per row.

    `ref` (bool, same index) picks the rows the MEDIAN is taken over; every row
    with a value and a group is still measured against it. peers.py passes the
    listed-company peer universe (fix list §19): before, OTC lines, preferreds,
    units and closed-end funds carrying a sector tag set the median (Auto
    Manufacturers' P/E median 7.9 with them, 18.3 without). None = all rows.

    NaN where the value is missing, the group has fewer than `min_n` non-null
    reference values, or the median is zero. Rows with a NaN group key (e.g. a
    fund with no sector) are left NaN — they have no peers to compare against.

    A median that is merely NEAR zero (rather than exactly zero) overflows the
    ratio to +/-inf, which the zero guard alone doesn't catch — 21 such rows were
    already stored in `pe_vs_industry` before this was handled. Those are converted
    to NaN too: an infinite "percent above the median" is not a usable comparison,
    and it is not valid JSON either, so it would fail on the way to the UI.
    """
    v = pd.to_numeric(values, errors="coerce")
    in_ref = v.notna() & groups.notna()
    if ref is not None:
        in_ref &= ref.reindex(v.index).fillna(False).astype(bool)
    stats = v[in_ref].groupby(groups[in_ref]).agg(["median", "count"])
    stats = stats[(stats["count"] >= min_n) & (stats["median"] != 0) & stats["median"].notna()]
    med = groups.map(stats["median"]).astype("float64")
    out = (v - med) / med.abs() * 100
    return out.replace([np.inf, -np.inf], np.nan)


def percentile_rank(values: pd.Series, ascending: bool = True) -> pd.Series:
    """0-100 percentile rank within the non-null values; NaN preserved.

    ascending=True ranks higher values higher (use False for metrics where lower
    is better, e.g. P/E). A lone value ranks at 50 (mid), not 100.
    """
    s = pd.to_numeric(values, errors="coerce")
    return s.rank(pct=True, ascending=ascending, na_option="keep") * 100


def percentile_rank_tiered(
    values: pd.Series, tiers: list[pd.Series], ascending: bool = True, min_n: int = 5
) -> pd.Series:
    """Percentile rank within the narrowest peer tier that has >= min_n members.

    `tiers` is an ordered list of group Series from NARROWEST to BROADEST (e.g.
    [industry, sector]): each row is ranked within the narrowest tier whose group
    clears `min_n` non-null members, falling back through the wider tiers and
    finally to the whole universe (Topic 4.4: per-metric peer baseline). Rows with
    a missing group key at every tier keep the universe percentile. NaN preserved.
    """
    s = pd.to_numeric(values, errors="coerce")
    out = percentile_rank(s, ascending)  # universe baseline (also the final fallback)
    for groups in reversed(tiers):       # broadest first so the narrowest tier wins
        frame = pd.DataFrame({"v": s, "g": groups})
        for _, idx in frame.dropna(subset=["v", "g"]).groupby("g").groups.items():
            if len(idx) >= min_n:
                out.loc[idx] = percentile_rank(s.loc[idx], ascending)
    return out


def percentile_vs_ref(values: pd.Series, ref: pd.Series | None,
                      ascending: bool = True) -> pd.Series:
    """0-100 percentile of every value against the REFERENCE rows' values only.

    `ref` (bool, same index) is the fixed ranking universe (fix list §18): live,
    listed companies one line each, or one share class per fund. Rows outside it
    (an OTC stock, a second fund class) are still placed in that distribution.
    Same tie handling as `percentile_rank` (average rank / n), so with `ref` =
    every row the result is identical; a value beyond the reference range is
    clipped to 0-100. No reference values -> falls back to all rows. NaN kept.
    """
    s = pd.to_numeric(values, errors="coerce")
    if ref is None:
        return percentile_rank(s, ascending)
    r = s[ref.reindex(s.index).fillna(False).astype(bool) & s.notna()]
    if r.empty:
        return percentile_rank(s, ascending)
    sign = 1.0 if ascending else -1.0
    v = np.sort(r.to_numpy(dtype="float64") * sign)
    x = s.to_numpy(dtype="float64") * sign
    lo = np.searchsorted(v, x, side="left")
    hi = np.searchsorted(v, x, side="right")
    pct = (lo + (hi - lo + 1) / 2) / len(v) * 100
    out = pd.Series(np.clip(pct, 0.0, 100.0), index=s.index)
    return out.where(s.notna())


def percentile_vs_ref_tiered(
    values: pd.Series, tiers: list[pd.Series], ref: pd.Series | None,
    ascending: bool = True, min_n: int = 5,
) -> pd.Series:
    """`percentile_rank_tiered` against the reference rows: each row is ranked in
    the narrowest tier group holding >= `min_n` REFERENCE values (industry, then
    sector), else against the whole reference set."""
    if ref is None:
        return percentile_rank_tiered(values, tiers, ascending, min_n)
    s = pd.to_numeric(values, errors="coerce")
    refb = ref.reindex(s.index).fillna(False).astype(bool)
    out = percentile_vs_ref(s, refb, ascending)
    for groups in reversed(tiers):  # broadest first so the narrowest tier wins
        frame = pd.DataFrame({"v": s, "g": groups, "r": refb})
        frame = frame.dropna(subset=["v", "g"])
        n_ref = frame[frame["r"]].groupby("g").size()
        ok = set(n_ref.index[n_ref >= min_n])
        for g, idx in frame.groupby("g").groups.items():
            if g in ok:
                out.loc[idx] = percentile_vs_ref(s.loc[idx], refb.loc[idx], ascending)
    return out
