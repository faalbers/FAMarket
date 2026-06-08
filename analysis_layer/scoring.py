"""
Scoring & ranking (Topic 4.4) — composite scores on the assembled universe frame.

Runs after every per-symbol metric exists (peers already added), as the last
cross-symbol stage. Adds, in order:

  * rs_rank (0-99) — IBD-style universe percentile of the weighted trailing
    return that the pipeline collected per symbol into the temp `_rs_raw` column.
  * five category scores (0-100): value, quality, growth, momentum, income — each
    the weight-averaged percentile rank of its metrics (settings.CATEGORY_METRIC_WEIGHTS).
  * overall_score (0-100) — the category scores combined per OVERALL_SCORE_WEIGHTS.

Design (see [[analysis-layer-design-decisions]]):
  * Each metric becomes a 0-100 percentile rank. Higher percentile = better, so
    LOWER-IS-BETTER metrics (valuation multiples, leverage, payout) rank ascending=False.
  * Those same lower-is-better metrics are only meaningful when positive — a
    negative P/E (a loss) is "not applicable", not "cheap" — so non-positive values
    are masked to NaN before ranking.
  * Value & quality rank within the symbol's peer group, narrowest-first:
    INDUSTRY, falling back to SECTOR, then the whole universe when a tier is too
    small (settings.MIN_PEERS_FOR_PERCENTILE). Growth/momentum/income rank
    universe-wide.
  * Gating is data-driven: a category with no available metrics for a symbol
    (e.g. a fund has no valuation ratios) scores NaN, and overall_score
    renormalizes over whichever categories are present.

Scores are stored as a plain 0-100 index (unit "" in param_hints).
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from config import settings
from analysis_layer import _stats

# Metrics where a LOWER value is better; ranked descending so low -> high score.
# These are also "positive-only": a non-positive value is not-applicable (a
# negative P/E is a loss, not a bargain) and is masked to NaN before ranking.
_LOWER_IS_BETTER = frozenset({
    "pe", "forward_pe", "peg", "ps", "pb", "p_fcf", "ev_ebitda", "ev_revenue",
    "debt_to_equity", "debt_to_ebitda", "div_payout_ratio",
})


def compute(df: pd.DataFrame) -> pd.DataFrame:
    """Add rs_rank, the five category scores, and overall_score to the frame."""
    if df.empty:
        return df
    df["rs_rank"] = _rs_rank(df)
    df = df.drop(columns=[c for c in ("_rs_raw",) if c in df.columns])

    for category, weights in settings.CATEGORY_METRIC_WEIGHTS.items():
        peer_relative = category in settings.SCORE_PEER_RELATIVE_CATEGORIES
        df[f"{category}_score"] = _category_score(df, weights, peer_relative)

    df["overall_score"] = _overall_score(df)
    return df


def _rs_rank(df: pd.DataFrame) -> pd.Series:
    """0-99 universe percentile of the weighted trailing return (NaN preserved)."""
    if "_rs_raw" not in df.columns:
        return pd.Series(np.nan, index=df.index, dtype="float64")
    pct = _stats.percentile_rank(df["_rs_raw"], ascending=True)  # higher return = stronger
    return (pct * 0.99).round()  # 0-100 percentile -> IBD 0-99 integer scale


def _metric_percentiles(df: pd.DataFrame, metric: str, peer_relative: bool) -> pd.Series:
    """One metric's 0-100 percentile across the frame, direction- and gate-aware."""
    vals = pd.to_numeric(df[metric], errors="coerce")
    ascending = metric not in _LOWER_IS_BETTER
    if not ascending:  # lower-is-better -> only positive values are meaningful
        vals = vals.where(vals > 0)
    if peer_relative:
        tiers = [df[c] for c in ("industry", "sector") if c in df.columns]
        if tiers:  # rank within industry -> sector -> universe
            return _stats.percentile_rank_tiered(
                vals, tiers, ascending, settings.MIN_PEERS_FOR_PERCENTILE
            )
    return _stats.percentile_rank(vals, ascending)


def _category_score(df: pd.DataFrame, weights: dict[str, float], peer_relative: bool) -> pd.Series:
    """Weight-averaged percentile of the available metrics; NaN if none present."""
    total = pd.Series(0.0, index=df.index)
    wsum = pd.Series(0.0, index=df.index)
    for metric, w in weights.items():
        if metric not in df.columns:
            continue
        pr = _metric_percentiles(df, metric, peer_relative)
        present = pr.notna()
        total[present] += pr[present] * w
        wsum[present] += w
    return (total / wsum).where(wsum > 0)


def _overall_score(df: pd.DataFrame) -> pd.Series:
    """Category scores combined per OVERALL_SCORE_WEIGHTS, renormalized to present."""
    total = pd.Series(0.0, index=df.index)
    wsum = pd.Series(0.0, index=df.index)
    for category, w in settings.OVERALL_SCORE_WEIGHTS.items():
        col = f"{category}_score"
        if col not in df.columns:
            continue
        score = pd.to_numeric(df[col], errors="coerce")
        present = score.notna()
        total[present] += score[present] * w
        wsum[present] += w
    return (total / wsum).where(wsum > 0)
