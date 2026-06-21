"""
Scoring & ranking (Topic 4.4) — composite scores on the assembled universe frame.

Runs after every per-symbol metric exists (peers already added), as the last
cross-symbol stage. Adds, in order:

  * rs_rank (0-99) — IBD-style percentile of the weighted trailing return that
    the pipeline collected per symbol into the `rs_raw` column, ranked WITHIN
    each security_type (funds vs stocks don't distort each other).
    rs_raw is persisted in analysis.db (not dropped) so a subset run can re-rank
    the merged frame: rows that weren't recomputed feed their stored rs_raw back in.
  * five category scores (0-100): value, quality, growth, momentum, income — each
    the weight-averaged rule GOODNESS of its metrics (settings.CATEGORY_METRIC_WEIGHTS).
  * overall_score (0-100) — the category scores combined per OVERALL_SCORE_WEIGHTS.

Design (see [[analysis-layer-design-decisions]], [[scoring-rules-system]]):
  * Each metric becomes 0-100 GOODNESS via its scoring rule
    (analysis_layer/scoring_rules.py): shape (higher/lower/sweet_spot) + anchor
    (peer / universe / absolute) + per-screen_type overrides. So metric DIRECTION
    and the peer-vs-universe choice now live per-metric in the rules, not in a
    hardcoded _LOWER_IS_BETTER set or a per-category flag here. This is the same
    goodness the heatmap shows — both go through scoring_rules.metric_goodness.
  * Gating is data-driven: a category with no available metrics for a symbol
    (e.g. a fund has no valuation ratios) scores NaN, and overall_score
    renormalizes over whichever categories are present.

Scores are RESULTS, not rules — they get no rule of their own (the rewire DERIVES
them from the parameter rules). Stored as a plain 0-100 index (unit "" in param_hints).
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from config import settings
from analysis_layer import _stats, scoring_rules


def compute(df: pd.DataFrame) -> pd.DataFrame:
    """Add rs_rank, the five category scores, and overall_score to the frame."""
    if df.empty:
        return df
    # On a subset-merged frame the kept rows arrive with their prior rs_rank; keep
    # it where re-ranking yields NaN (rows from before rs_raw was persisted). Full
    # runs build the frame without an rs_rank column, so this never fires there.
    prior_rs = df["rs_rank"] if "rs_rank" in df.columns else None
    df["rs_rank"] = _rs_rank(df)
    if prior_rs is not None:
        df["rs_rank"] = df["rs_rank"].fillna(pd.to_numeric(prior_rs, errors="coerce"))

    rules = scoring_rules.load_rules()  # load once; metric direction/anchor lives here now
    for category, weights in settings.CATEGORY_METRIC_WEIGHTS.items():
        df[f"{category}_score"] = _category_score(df, weights, rules)

    df["overall_score"] = _overall_score(df)
    return df


def _rs_rank(df: pd.DataFrame) -> pd.Series:
    """0-99 percentile of the weighted trailing return, RANKED WITHIN security_type.

    The universe is ~65% mutual funds; ranking everything together let funds
    dominate and compressed real stocks (medians: stock 45, mutual_fund 50, adr
    32). Ranking each symbol against its own security_type fixes that. Thin/odd
    types (< RS_RANK_MIN_PER_TYPE members) fall back to the universe percentile.
    NaN preserved; downstream still consumes the same 0-99 `rs_rank` column.
    """
    if "rs_raw" not in df.columns:
        return pd.Series(np.nan, index=df.index, dtype="float64")
    raw = pd.to_numeric(df["rs_raw"], errors="coerce")  # higher return = stronger
    if "security_type" in df.columns:
        pct = _stats.percentile_rank_tiered(
            raw, [df["security_type"]], ascending=True,
            min_n=settings.RS_RANK_MIN_PER_TYPE,
        )
    else:  # safety net: no type column -> old universe-wide behaviour
        pct = _stats.percentile_rank(raw, ascending=True)
    return (pct * 0.99).round()  # 0-100 percentile -> IBD 0-99 integer scale


def _category_score(df: pd.DataFrame, weights: dict[str, float],
                    rules: dict[str, dict]) -> pd.Series:
    """Weight-averaged rule GOODNESS of the available metrics; NaN if none present.

    Each metric's 0-100 goodness comes from its scoring rule (shape + anchor +
    per-screen_type overrides) via the shared scoring_rules.metric_goodness — the
    same code path the heatmap uses. Missing metrics drop out and weights
    renormalize, so a fund with no fundamentals gates itself to NaN naturally.
    """
    total = pd.Series(0.0, index=df.index)
    wsum = pd.Series(0.0, index=df.index)
    for metric, w in weights.items():
        if metric not in df.columns:
            continue
        g = scoring_rules.metric_goodness(df, metric, rules)
        present = g.notna()
        total[present] += g[present] * w
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
