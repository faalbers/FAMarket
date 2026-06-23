"""
Per-parameter scoring RULES — the "is this value strong or weak?" model.

Background (see the plan + ROADMAP 6.2 heatmap): the existing category scores in
`scoring.py` turn each metric into a percentile rank with a hard-coded two-state
direction (`_LOWER_IS_BETTER`). That can't express **sweet-spot** metrics (payout
ratio, current ratio) where the *middle* is best, nor an **absolute anchor** (PEG
fair at 1.0). This module is the richer, fully data-driven replacement that the
**rule-colored heatmap** consumes now and `scoring.py` can adopt later (no rework).

A rule is pure data:
  - `shape`  : "higher_better" | "lower_better" | "sweet_spot"
  - `anchor` : "peer"      — rank within the symbol's industry→sector→universe
               "universe"  — rank across the whole universe (no peer tiers)
               "absolute"  — judged against a fixed line, not the crowd:
                               * with `value`  → a pivot (50 = the line)
                               * with `lo`/`hi`→ a sweet-spot band (sweet_spot shape)
                               * with neither  → used as-is (already a 0-100 rank, e.g. rs_rank)
  - `positive_only` : mask non-positive values to NaN before judging (a negative P/E
                       is "not applicable", not "cheap").
  - `overrides` : {screen_type: {partial fields}} merged onto the base for that type
                  (e.g. REIT payout 80-95 vs the base 30-60). Sparse — most have none.

`goodness(values, rule, tiers)` turns a column into a 0-100 score (100 = strong,
0 = weak). The DEFAULTS below are the rules walked through with the user; the
machine-local `scoring_rules.json` (settings.SCORING_RULES_PATH) overrides/extends
them, exactly like the `.filt` filter sets. Delete that file to reset.

This module is pure (no Streamlit / no chart imports) so both the UI and a future
`scoring.py` can import it.
"""

from __future__ import annotations

import json
import logging

import numpy as np
import pandas as pd

from config import settings
from analysis_layer import _stats

log = logging.getLogger(__name__)

SHAPES = ("higher_better", "lower_better", "sweet_spot")
ANCHORS = ("peer", "universe", "absolute")
_MIN_PEERS = settings.MIN_PEERS_FOR_PERCENTILE


# --------------------------------------------------------------------------- #
# Committed default rules (the user-walked-through set). Keyed by analysis.db
# column. scoring_rules.json overrides/extends these at load.
# --------------------------------------------------------------------------- #
DEFAULT_RULES: dict[str, dict] = {
    # -- Valuation: multiples lower-better/peer; PEG absolute@1; safety absolute@0 --
    "pe":           {"shape": "lower_better", "anchor": "peer", "positive_only": True},
    "forward_pe":   {"shape": "lower_better", "anchor": "peer", "positive_only": True},
    "peg":          {"shape": "lower_better", "anchor": "absolute", "value": 1.0, "positive_only": True},
    "pb":           {"shape": "lower_better", "anchor": "peer", "positive_only": True},
    "ps":           {"shape": "lower_better", "anchor": "peer"},
    "p_fcf":        {"shape": "lower_better", "anchor": "peer", "positive_only": True},
    "ev_ebitda":    {"shape": "lower_better", "anchor": "peer", "positive_only": True},
    "ev_revenue":   {"shape": "lower_better", "anchor": "peer"},
    "margin_of_safety": {"shape": "higher_better", "anchor": "absolute", "value": 0.0},
    # -- Profitability: all higher-better, peer --
    "roe":              {"shape": "higher_better", "anchor": "peer"},
    "roa":              {"shape": "higher_better", "anchor": "peer"},
    "roic":             {"shape": "higher_better", "anchor": "peer"},
    "gross_margin":     {"shape": "higher_better", "anchor": "peer"},
    "operating_margin": {"shape": "higher_better", "anchor": "peer"},
    "net_margin":       {"shape": "higher_better", "anchor": "peer"},
    "fcf_margin":       {"shape": "higher_better", "anchor": "peer"},
    # margin TREND (3y pp change): widening = improving pricing power, universe-ranked
    "gross_margin_trend_3y":     {"shape": "higher_better", "anchor": "universe"},
    "operating_margin_trend_3y": {"shape": "higher_better", "anchor": "universe"},
    # -- Balance sheet: leverage lower/peer; liquidity sweet-spots; Altman abs@3 --
    "debt_to_equity":    {"shape": "lower_better", "anchor": "peer", "positive_only": True},
    "debt_to_ebitda":    {"shape": "lower_better", "anchor": "peer", "positive_only": True},
    "interest_coverage": {"shape": "higher_better", "anchor": "peer"},
    "current_ratio":     {"shape": "sweet_spot", "anchor": "absolute", "lo": 1.5, "hi": 3.0},
    "quick_ratio":       {"shape": "sweet_spot", "anchor": "absolute", "lo": 1.0, "hi": 2.0},
    "cash_ratio":        {"shape": "sweet_spot", "anchor": "absolute", "lo": 0.2, "hi": 0.75},
    "altman_z":          {"shape": "higher_better", "anchor": "absolute", "value": 3.0},
    # -- Growth: rates higher-better/universe; steadiness flips --
    "revenue_cagr_1y": {"shape": "higher_better", "anchor": "universe"},
    "revenue_cagr_3y": {"shape": "higher_better", "anchor": "universe"},
    "revenue_cagr_5y": {"shape": "higher_better", "anchor": "universe"},
    "eps_cagr_1y":     {"shape": "higher_better", "anchor": "universe"},
    "eps_cagr_3y":     {"shape": "higher_better", "anchor": "universe"},
    "eps_cagr_5y":     {"shape": "higher_better", "anchor": "universe"},
    "fcf_cagr_3y":     {"shape": "higher_better", "anchor": "universe"},
    "book_value_cagr_3y": {"shape": "higher_better", "anchor": "universe"},
    "revenue_yoy_q":   {"shape": "higher_better", "anchor": "universe"},
    "eps_yoy_q":       {"shape": "higher_better", "anchor": "universe"},
    # acceleration pivots on 0 (speeding up vs own pace); buybacks (lower share count) better
    "revenue_accel":   {"shape": "higher_better", "anchor": "absolute", "value": 0.0},
    "eps_accel":       {"shape": "higher_better", "anchor": "absolute", "value": 0.0},
    "share_count_chg_1y": {"shape": "lower_better", "anchor": "universe"},
    # -- Estimates: forward growth higher/universe; fwd PEG abs@1; revisions pivot@0 --
    "forward_eps_growth":   {"shape": "higher_better", "anchor": "universe"},
    "forward_rev_growth":   {"shape": "higher_better", "anchor": "universe"},
    "forward_peg":          {"shape": "lower_better", "anchor": "absolute", "value": 1.0, "positive_only": True},
    "eps_revision_1m":      {"shape": "higher_better", "anchor": "absolute", "value": 0.0},
    "eps_revision_3m":      {"shape": "higher_better", "anchor": "absolute", "value": 0.0},
    "eps_revision_breadth": {"shape": "higher_better", "anchor": "absolute", "value": 0.0},
    # -- Income: ABSOLUTE bands (goal is absolute "good income") --
    "div_yield_ttm":   {"shape": "sweet_spot", "anchor": "absolute", "lo": 2.0, "hi": 6.0,
                        "overrides": {"reit": {"lo": 4.0, "hi": 10.0}}},
    "div_growth_5y":         {"shape": "higher_better", "anchor": "universe"},
    "div_consecutive_years": {"shape": "higher_better", "anchor": "universe"},
    "div_consistency":       {"shape": "higher_better", "anchor": "universe"},
    "div_payout_ratio": {"shape": "sweet_spot", "anchor": "absolute", "lo": 30.0, "hi": 60.0,
                         "overrides": {"reit": {"lo": 80.0, "hi": 95.0}}},
    "div_coverage":     {"shape": "higher_better", "anchor": "absolute", "value": 1.0},
    # -- Momentum / technical --
    "rs_rank":          {"shape": "higher_better", "anchor": "absolute"},   # already 0-99
    "price_vs_ma_50":   {"shape": "higher_better", "anchor": "absolute", "value": 0.0},
    "price_vs_ma_150":  {"shape": "higher_better", "anchor": "absolute", "value": 0.0},
    "price_vs_ma_200":  {"shape": "higher_better", "anchor": "absolute", "value": 0.0},
    "pct_from_52w_high": {"shape": "higher_better", "anchor": "absolute", "value": 0.0},
    "pct_from_52w_low":  {"shape": "higher_better", "anchor": "universe"},
    "rsi_14":           {"shape": "sweet_spot", "anchor": "absolute", "lo": 40.0, "hi": 70.0},
    "bb_pct":           {"shape": "sweet_spot", "anchor": "absolute", "lo": 0.3, "hi": 0.7},
    "macd_hist":        {"shape": "higher_better", "anchor": "absolute", "value": 0.0},
}

# Category/overall scores are NOT first-class rules — they are RESULTS (0-100): today from
# scoring.py's own percentile logic, later DERIVED from these parameter rules (the parked
# scoring.py rewire). So they do NOT appear on the rules page. They stay COLORABLE in the
# heatmap as-is (higher = better, value used directly) via rule_for's `*_score` fallback.
SCORE_COLUMNS = ["overall_score", "value_score", "quality_score", "growth_score",
                 "momentum_score", "income_score"]

# Display order on the rules page (category -> metric keys), so the page mirrors the
# walkthrough. Only metrics with a rule appear; the heatmap can still color any column
# via the pattern fallback in `rule_for`.
RULE_CATEGORIES: dict[str, list[str]] = {
    "Valuation": ["pe", "forward_pe", "peg", "pb", "ps", "p_fcf", "ev_ebitda",
                  "ev_revenue", "margin_of_safety"],
    "Profitability": ["roe", "roa", "roic", "gross_margin", "operating_margin",
                      "net_margin", "fcf_margin", "gross_margin_trend_3y",
                      "operating_margin_trend_3y"],
    "Balance Sheet": ["debt_to_equity", "debt_to_ebitda", "interest_coverage",
                      "current_ratio", "quick_ratio", "cash_ratio", "altman_z"],
    "Growth": ["revenue_cagr_1y", "revenue_cagr_3y", "revenue_cagr_5y", "eps_cagr_1y",
               "eps_cagr_3y", "eps_cagr_5y", "fcf_cagr_3y", "book_value_cagr_3y",
               "revenue_yoy_q", "eps_yoy_q", "revenue_accel", "eps_accel",
               "share_count_chg_1y"],
    "Estimates": ["forward_eps_growth", "forward_rev_growth", "forward_peg",
                  "eps_revision_1m", "eps_revision_3m", "eps_revision_breadth"],
    "Income": ["div_yield_ttm", "div_growth_5y", "div_consecutive_years",
               "div_consistency", "div_payout_ratio", "div_coverage"],
    "Momentum / Technical": ["rs_rank", "price_vs_ma_50", "price_vs_ma_150",
                             "price_vs_ma_200", "pct_from_52w_high", "pct_from_52w_low",
                             "rsi_14", "bb_pct", "macd_hist"],
}


# --------------------------------------------------------------------------- #
# Load / save (scoring_rules.json over the defaults — the .filt pattern)
# --------------------------------------------------------------------------- #
def load_rules() -> dict[str, dict]:
    """The full rule set: committed DEFAULT_RULES with scoring_rules.json laid on top.

    The file is a flat {metric: rule-dict}; each present metric REPLACES the default
    entry wholesale (a rule is small and self-contained, so a deep-merge would only
    invite half-set rules). A missing/bad file leaves the defaults in force.
    """
    rules = {k: dict(v) for k, v in DEFAULT_RULES.items()}
    path = settings.SCORING_RULES_PATH
    if not path.exists():
        return rules
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        log.warning("Ignoring unreadable %s: %s", path.name, exc)
        return rules
    if isinstance(data, dict):
        for metric, rule in data.items():
            if isinstance(rule, dict) and rule.get("shape") in SHAPES:
                rules[metric] = rule
    return rules


def save_rules(rules: dict[str, dict]) -> None:
    """Persist only the metrics whose rule DIFFERS from its committed default.

    Keeps the file to the user's deviations (like settings.local.json), so a later
    change to a default still reaches untouched metrics.
    """
    changed = {m: r for m, r in rules.items()
               if m not in DEFAULT_RULES or r != DEFAULT_RULES[m]}
    path = settings.SCORING_RULES_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(changed, indent=2, sort_keys=True) + "\n", encoding="utf-8")


# --------------------------------------------------------------------------- #
# Rule resolution
# --------------------------------------------------------------------------- #
def rule_for(metric: str, rules: dict[str, dict] | None = None) -> dict | None:
    """The base rule for a metric: explicit entry, else a pattern fallback, else None.

    Pattern fallback covers growth-window columns the explicit table doesn't list
    (`*_cagr_*`, `*_yoy_q` higher-better; `*_growth_vol`/`*_growth_cv` lower-better;
    `*_growth_r2` higher-better) and any `*_score` (0-100). None = not a scorable metric.
    """
    rules = rules if rules is not None else load_rules()
    if metric in rules:
        return rules[metric]
    if metric.endswith("_score"):
        return {"shape": "higher_better", "anchor": "absolute"}
    if metric.endswith(("_growth_vol", "_growth_cv")):
        return {"shape": "lower_better", "anchor": "universe"}
    if metric.endswith("_growth_r2") or "_cagr_" in metric or metric.endswith("_yoy_q"):
        return {"shape": "higher_better", "anchor": "universe"}
    return None


def resolve(metric: str, screen_type: str | None,
            rules: dict[str, dict] | None = None) -> dict | None:
    """`rule_for(metric)` with its per-`screen_type` override merged on top (if any)."""
    base = rule_for(metric, rules)
    if base is None:
        return None
    ov = (base.get("overrides") or {}).get(screen_type) if screen_type else None
    if not ov:
        return base
    merged = {k: v for k, v in base.items() if k != "overrides"}
    merged.update(ov)
    return merged


# --------------------------------------------------------------------------- #
# Goodness — a column → 0-100 (100 strong, 0 weak), NaN preserved
# --------------------------------------------------------------------------- #
def goodness(values: pd.Series, rule: dict,
             tiers: list[pd.Series] | None = None) -> pd.Series:
    """Apply a rule to a column, returning 0-100 goodness (NaN where not applicable).

    For peer/universe anchors this is a percentile across the passed frame, so pass the
    WHOLE universe (not a handful of rows) — ranks are only meaningful against the crowd.
    Absolute anchors are self-contained (a pivot or band), with falloff scaled to the
    column's own 5th/95th-percentile spread.
    """
    s = pd.to_numeric(values, errors="coerce")
    if rule.get("positive_only"):
        s = s.where(s > 0)
    shape = rule.get("shape", "higher_better")
    if shape == "sweet_spot":
        return _sweet_spot(s, rule)
    ascending = shape == "higher_better"           # high value → high goodness
    anchor = rule.get("anchor", "peer")
    if anchor == "peer" and tiers:
        return _stats.percentile_rank_tiered(s, tiers, ascending, _MIN_PEERS)
    if anchor in ("peer", "universe"):
        return _stats.percentile_rank(s, ascending)
    # absolute
    if rule.get("value") is not None:
        return _pivot(s, float(rule["value"]), ascending)
    return s.clip(lower=0, upper=100)              # already a 0-100 rank (rs_rank, scores)


def metric_goodness(df: pd.DataFrame, metric: str,
                    rules: dict[str, dict] | None = None) -> pd.Series:
    """One metric's 0-100 goodness over the WHOLE frame, with per-`screen_type`
    overrides spliced in — the SINGLE code path shared by the scoring rewire and
    the heatmap (don't reimplement strong/weak coloring elsewhere).

    Pass the whole universe: peer/universe anchors rank across the passed rows.
    Returns all-NaN when the metric is absent or has no rule (not scorable).
    """
    base = rule_for(metric, rules)
    if base is None or metric not in df.columns:
        return pd.Series(np.nan, index=df.index, dtype="float64")
    tiers = ([df["industry"], df["sector"]]
             if base.get("anchor") == "peer" and {"industry", "sector"} <= set(df.columns)
             else None)
    g = goodness(df[metric], base, tiers)
    overrides = base.get("overrides") or {}
    if overrides and "screen_type" in df.columns:
        for st_val in overrides:                       # sparse; all ABSOLUTE today
            mask = df["screen_type"] == st_val
            if mask.any():                             # absolute → tiers irrelevant
                merged = resolve(metric, st_val, rules)
                g.loc[mask] = goodness(df.loc[mask, metric], merged, None)
    return g


def _spread(s: pd.Series) -> tuple[float, float]:
    """The falloff reach for absolute rules — the robust **1.5× IQR fence**, clamped to the
    data. Using the IQR fence (not the 5th/95th pctile) keeps fat tails from stretching the
    fade to "weak" far out: e.g. quick ratio's 95th pctile is ~14 (max ~29000), so a pctile
    reach left everything up to ~10 looking strong. The fence (~5.8 here) makes above-band
    values fade to blue sensibly — and it matches the preview's IQR x-axis zoom."""
    q1, q3 = s.quantile(0.25), s.quantile(0.75)
    iqr = q3 - q1
    if pd.isna(iqr) or iqr <= 0:
        return float(s.min()), float(s.max())
    lo = max(q1 - 1.5 * iqr, s.min())
    hi = min(q3 + 1.5 * iqr, s.max())
    return float(lo), float(hi)


def _sweet_spot(s: pd.Series, rule: dict) -> pd.Series:
    """100 inside [lo, hi]; linear falloff to 0 toward the 5th/95th pctile on each side."""
    lo = float(rule.get("lo"))
    hi = float(rule.get("hi"))
    p05, p95 = _spread(s)
    lo_span = max(lo - p05, 1e-9)
    hi_span = max(p95 - hi, 1e-9)
    g = pd.Series(100.0, index=s.index)
    below = s < lo
    above = s > hi
    g[below] = (100.0 * (1.0 - (lo - s[below]) / lo_span)).clip(lower=0.0)
    g[above] = (100.0 * (1.0 - (s[above] - hi) / hi_span)).clip(lower=0.0)
    return g.where(s.notna())


def _pivot(s: pd.Series, v: float, ascending: bool) -> pd.Series:
    """50 at the anchor line `v`; toward 100 in the good direction, 0 in the bad one,
    scaled by the column's spread on each side of the line."""
    p05, p95 = _spread(s)
    up = max(p95 - v, 1e-9)   # reach above the line
    dn = max(v - p05, 1e-9)   # reach below the line
    g = pd.Series(50.0, index=s.index)
    hi = s > v
    loo = s < v
    if ascending:             # above the line is good
        g[hi] = 50.0 + 50.0 * (s[hi] - v) / up
        g[loo] = 50.0 - 50.0 * (v - s[loo]) / dn
    else:                     # below the line is good
        g[hi] = 50.0 - 50.0 * (s[hi] - v) / up
        g[loo] = 50.0 + 50.0 * (v - s[loo]) / dn
    return g.clip(0.0, 100.0).where(s.notna())


def verdict(value: float, rule: dict) -> str:
    """A short human read of where a raw value sits for its rule — for tooltips.

    e.g. sweet_spot → "below ideal 30–60" / "in ideal band" / "above ideal 30–60";
    directional → "lower is better" / "higher is better"; absolute pivot names the line.
    """
    if value is None or (isinstance(value, float) and value != value):
        return "no data"
    shape = rule.get("shape", "higher_better")
    if shape == "sweet_spot":
        lo, hi = rule.get("lo"), rule.get("hi")
        if value < lo:
            return f"below ideal {lo:g}–{hi:g}"
        if value > hi:
            return f"above ideal {lo:g}–{hi:g}"
        return f"in ideal band {lo:g}–{hi:g}"
    direction = "higher is better" if shape == "higher_better" else "lower is better"
    if rule.get("anchor") == "absolute" and rule.get("value") is not None:
        return f"{direction} (line at {rule['value']:g})"
    return direction


# --------------------------------------------------------------------------- #
# Data-driven suggestion (a starting point on the rules page)
# --------------------------------------------------------------------------- #
def suggest_rule(values: pd.Series, metric: str) -> dict:
    """A suggested rule from the column's distribution: keep the default shape/anchor,
    and for a sweet_spot propose the band = the interquartile range (25th–75th pctile)."""
    base = rule_for(metric) or {"shape": "higher_better", "anchor": "universe"}
    out = {k: v for k, v in base.items() if k != "overrides"}
    if out.get("shape") == "sweet_spot":
        s = pd.to_numeric(values, errors="coerce").dropna()
        if not s.empty:
            out["lo"] = round(float(s.quantile(0.25)), 2)
            out["hi"] = round(float(s.quantile(0.75)), 2)
    return out
