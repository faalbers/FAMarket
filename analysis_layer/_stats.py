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
    values: pd.Series, groups: pd.Series, min_n: int
) -> pd.Series:
    """(value − group median) / |group median| × 100, per row.

    NaN where the value is missing, the group has fewer than `min_n` non-null
    values, or the median is zero. Rows with a NaN group key (e.g. a fund with no
    sector) are left NaN — they have no peers to compare against.
    """
    out = pd.Series(np.nan, index=values.index, dtype="float64")
    df = pd.DataFrame({"v": pd.to_numeric(values, errors="coerce"), "g": groups})
    for _, idx in df.dropna(subset=["v", "g"]).groupby("g").groups.items():
        vals = df.loc[idx, "v"]
        if len(vals) < min_n:
            continue
        med = float(vals.median())
        if med == 0 or pd.isna(med):
            continue
        out.loc[vals.index] = (vals - med) / abs(med) * 100
    return out


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
