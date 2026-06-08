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
"""

from __future__ import annotations

import pandas as pd

from config import settings
from analysis_layer import _stats


def compute(df: pd.DataFrame) -> pd.DataFrame:
    """Add _vs_sector / _vs_industry columns for every peer-comparable metric."""
    min_n = settings.MIN_PEERS_FOR_MEDIAN
    for metric in settings.PEER_COMPARABLE_METRICS:
        if metric not in df.columns:
            continue
        if "sector" in df.columns:
            df[f"{metric}_vs_sector"] = _stats.relative_to_group_median(
                df[metric], df["sector"], min_n
            )
        if "industry" in df.columns:
            df[f"{metric}_vs_industry"] = _stats.relative_to_group_median(
                df[metric], df["industry"], min_n
            )
    return df
