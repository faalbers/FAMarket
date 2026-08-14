"""
Peer comparison (Topic 4.3) — sector/industry relative valuation, on the frame.

For each peer-comparable metric (settings.PEER_COMPARABLE_METRICS) we add two
columns to the assembled analysis frame:

  * <metric>_vs_sector   — % the symbol sits above/below its SECTOR median
  * <metric>_vs_industry — % above/below its INDUSTRY median

The median itself is not stored (Topic 4.3) — only the relative % difference, so a
positive value means "higher than the typical peer" (more expensive for a P/E,
more profitable for a margin; the filter/scoring layer assigns direction). Groups
with fewer than settings.MIN_PEERS_FOR_MEDIAN members give NaN — too few to trust
a median. Sector is broad, industry narrow, both sourced from quotes.db.

Metrics whose scoring rule is `positive_only` are masked to NaN where non-positive
BEFORE the median is taken — see `_peer_input`.
"""

from __future__ import annotations

import pandas as pd

from config import settings
from analysis_layer import _stats
from analysis_layer import scoring_rules as SR


def _peer_input(df: pd.DataFrame, metric: str) -> pd.Series:
    """The metric's values, with non-positive entries masked out when the metric's
    scoring rule declares `positive_only`.

    A negative multiple means "not applicable", not "cheap": a loss-making company
    has a negative P/E, and negative book value gives a negative P/B. Compared raw
    against a positive peer median those land far BELOW it, i.e. reported as a deep
    discount — precisely inverting what they mean. Measured 2026-08-13 before this
    mask existed: 2,266 loss-making names carried a `pe_vs_industry` of -180% at the
    median (worst -68,000,000%), and any screen reading that column as "cheaper than
    peers" without a separate profitability gate silently collected them.

    They also drag the peer median itself, so the distortion is not confined to the
    offending rows.

    The flag is read from `scoring_rules.DEFAULT_RULES` rather than a second list
    here, so the two layers cannot disagree about what a negative value means. The
    committed defaults are used, not `load_rules()`: whether a negative multiple is
    meaningful is a property of the metric, not a user preference, and peer columns
    should not shift because someone edited a scoring rule in the UI.
    """
    values = pd.to_numeric(df[metric], errors="coerce")
    if (SR.DEFAULT_RULES.get(metric) or {}).get("positive_only"):
        return values.where(values > 0)
    return values


def compute(df: pd.DataFrame) -> pd.DataFrame:
    """Add _vs_sector / _vs_industry columns for every peer-comparable metric."""
    min_n = settings.MIN_PEERS_FOR_MEDIAN
    for metric in settings.PEER_COMPARABLE_METRICS:
        if metric not in df.columns:
            continue
        values = _peer_input(df, metric)
        if "sector" in df.columns:
            df[f"{metric}_vs_sector"] = _stats.relative_to_group_median(
                values, df["sector"], min_n
            )
        if "industry" in df.columns:
            df[f"{metric}_vs_industry"] = _stats.relative_to_group_median(
                values, df["industry"], min_n
            )
    return df
