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
from core.database import Database
from core.logging_config import get_logger
from analysis_layer import _stats, scoring_rules

log = get_logger("analysis")

# Column suffixes that are NOT scorable base metrics: results (category/overall
# scores), peer-relative variants, and the goodness columns themselves.
_NON_SCORABLE_SUFFIXES = ("_score", "_vs_sector", "_vs_industry", "_goodness")


def compute(df: pd.DataFrame) -> pd.DataFrame:
    """Add rs_rank, per-metric goodness, the five category scores, and overall_score."""
    if df.empty:
        return df
    # On a subset-merged frame the kept rows arrive with their prior rs_rank; keep
    # it where re-ranking yields NaN (rows from before rs_raw was persisted). Full
    # runs build the frame without an rs_rank column, so this never fires there.
    prior_rs = df["rs_rank"] if "rs_rank" in df.columns else None
    df["rs_rank"] = _rs_rank(df)
    if prior_rs is not None:
        df["rs_rank"] = df["rs_rank"].fillna(pd.to_numeric(prior_rs, errors="coerce"))

    return _apply_rule_scores(df)


def _apply_rule_scores(df: pd.DataFrame, rules: dict[str, dict] | None = None) -> pd.DataFrame:
    """The rule-dependent scoring, in dependency order: per-metric goodness FIRST,
    then category scores derived from it, then overall.

    Shared by compute() (during analysis) and refresh_scores() (a fast rule-edit
    update). rs_rank and the raw metrics are NOT touched here. metric direction/
    anchor lives in the rules, loaded once.
    """
    rules = rules if rules is not None else scoring_rules.load_rules()
    df = _goodness_columns(df, rules)                       # 1. per-parameter Score
    for category, weights in settings.CATEGORY_METRIC_WEIGHTS.items():
        df[f"{category}_score"] = _category_score(df, weights, rules)  # 2. categories
    df["overall_score"] = _overall_score(df)               # 3. overall
    return df


def _scorable_columns(df: pd.DataFrame, rules: dict[str, dict]) -> list[str]:
    """Base/window columns that have a scoring rule — each gets a `_goodness` twin.

    Excludes results & variants (the `_NON_SCORABLE_SUFFIXES`) and `rs_rank` (its
    goodness equals itself — it's already a 0-99 rank).
    """
    return [c for c in df.columns
            if c != "rs_rank" and not c.endswith(_NON_SCORABLE_SUFFIXES)
            and scoring_rules.rule_for(c, rules) is not None]


def _goodness_columns(df: pd.DataFrame, rules: dict[str, dict]) -> pd.DataFrame:
    """Append `<metric>_goodness` (0-100) for every scorable column.

    This is the per-parameter "Score" — filterable in the UI AND the input the
    category scores average, so goodness is computed ONCE here (via the canonical
    scoring_rules.metric_goodness) and reused, never recomputed per category.

    All twins are built against the original frame (goodness never depends on
    another goodness column) and joined in ONE concat — assigning ~76 columns
    individually fragments the DataFrame.
    """
    cols = _scorable_columns(df, rules)
    if not cols:
        return df
    twins = {f"{c}_goodness": scoring_rules.metric_goodness(df, c, rules) for c in cols}
    return pd.concat([df, pd.DataFrame(twins, index=df.index)], axis=1)


def refresh_scores() -> dict:
    """Recompute ONLY the rule-dependent scores on the stored analysis.db, in place.

    For when a scoring rule changed but nothing else did: re-derives every
    `<metric>_goodness`, the category scores, and overall_score from the current
    rules and rewrites the table. No fetch, no per-symbol metric recompute, no
    index rebuild — rs_rank and raw metrics are left as stored. analysis_meta is
    untouched (prices/vintage didn't change). Returns a small summary.
    """
    with Database(settings.ANALYSIS_DB) as db:
        df = db.read("analysis")
    if df.empty:
        return {"symbols": 0}
    # Drop existing goodness columns first so a metric whose rule was removed (or a
    # renamed column) doesn't leave a stale `_goodness` behind.
    df = df.drop(columns=[c for c in df.columns if c.endswith("_goodness")], errors="ignore")
    df = _apply_rule_scores(df)
    with Database(settings.ANALYSIS_DB) as db:
        db.replace("analysis", df)
    log.info("Scores refreshed — %d symbols, %d columns", len(df), df.shape[1])
    return {"symbols": len(df), "columns": df.shape[1]}


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
    """Weight-averaged per-metric GOODNESS of the available metrics; NaN if none.

    Reuses the `<metric>_goodness` columns already computed by `_goodness_columns`
    (one goodness per metric, never recomputed). The only weighted metric without a
    stored twin is `rs_rank`, whose goodness == itself — computed live via the same
    canonical path. Missing metrics drop out and weights renormalize, so a fund with
    no fundamentals gates itself to NaN naturally.
    """
    total = pd.Series(0.0, index=df.index)
    wsum = pd.Series(0.0, index=df.index)
    for metric, w in weights.items():
        if metric not in df.columns:
            continue
        gcol = f"{metric}_goodness"
        g = (pd.to_numeric(df[gcol], errors="coerce") if gcol in df.columns
             else scoring_rules.metric_goodness(df, metric, rules))
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
