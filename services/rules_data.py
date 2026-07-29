"""
Scoring-rule editing support: which metrics are editable, and the histogram
preview that shows what a candidate rule would do to the real universe.

Colouring goes through `scoring_rules.goodness`, the same function the heat map
and the stored `*_goodness` columns use — a rule is never scored twice by two
different implementations.
"""

from __future__ import annotations

from typing import Any, cast

import numpy as np
import pandas as pd

from analysis_layer import scoring_rules as SR
from config import param_hints
from services.scores_data import HEAT_RAMP, load_analysis, metric_name

PREVIEW_BINS = 60
# Tukey's fence: the middle 50% of values extended 1.5× each side.
IQR_FENCE_K = 1.5


def heat_color(goodness: float | None) -> str:
    """Interpolate HEAT_RAMP; None (no data) is a neutral grey."""
    if goodness is None or not np.isfinite(goodness):
        return "#3a3f4b"
    position = max(0.0, min(100.0, goodness)) / 100.0 * (len(HEAT_RAMP) - 1)
    low = int(np.floor(position))
    high = min(low + 1, len(HEAT_RAMP) - 1)
    frac = position - low

    def channel(hex_color: str, offset: int) -> int:
        return int(hex_color[1 + offset * 2 : 3 + offset * 2], 16)

    mixed = [
        round(channel(HEAT_RAMP[low], i) * (1 - frac) + channel(HEAT_RAMP[high], i) * frac)
        for i in range(3)
    ]
    return "#%02x%02x%02x" % tuple(mixed)


def editable_metrics() -> list[dict[str, Any]]:
    """Rule metrics that are actually present as columns in analysis.db.

    Category scores are excluded on purpose: they are RESULTS derived from rule
    goodness, not rules themselves.
    """
    frame = load_analysis()
    present = set(frame.columns)
    out: list[dict[str, Any]] = []
    for category, keys in SR.RULE_CATEGORIES.items():
        for key in keys:
            if key in present:
                hint = param_hints.get_hint(key) or {}
                out.append(
                    {
                        "key": key,
                        "label": metric_name(key),
                        "category": category,
                        "unit": hint.get("unit", ""),
                    }
                )
    return out


def overview() -> list[dict[str, Any]]:
    """Every editable metric with its current rule, for the summary table."""
    rules = SR.load_rules()
    out: list[dict[str, Any]] = []
    for entry in editable_metrics():
        rule = SR.rule_for(entry["key"], rules) or {}
        default = SR.DEFAULT_RULES.get(entry["key"], {})
        out.append(
            {
                **entry,
                "rule": rule,
                "shape": rule.get("shape"),
                "anchor": rule.get("anchor"),
                "band": _band(rule),
                "customised": rule != default,
            }
        )
    return out


def _band(rule: dict) -> str:
    if rule.get("shape") == "sweet_spot":
        return f"{rule.get('lo')} – {rule.get('hi')}"
    if rule.get("anchor") == "absolute" and rule.get("value") is not None:
        return str(rule.get("value"))
    return ""


def preview(metric: str, rule: dict) -> dict[str, Any]:
    """Histogram of the metric's universe values, each bar coloured by goodness.

    The x-axis is zoomed to the Tukey fence so a fat tail can't flatten the bell
    (P/E shows ~0-66, not 0-1376), while the COLOURING still uses every value —
    this only changes the visible window.
    """
    frame = load_analysis()
    if frame.empty or metric not in frame.columns:
        return {"bins": [], "message": "No data for this metric in analysis.db."}

    values = pd.Series(pd.to_numeric(frame[metric], errors="coerce"))
    if rule.get("positive_only"):
        values = values.where(values > 0)
    clean = cast(pd.Series, values.dropna())
    if clean.empty:
        return {"bins": [], "message": "No data for this metric in analysis.db."}

    tiers = (
        [frame["industry"], frame["sector"]]
        if rule.get("anchor") == "peer" and {"industry", "sector"} <= set(frame.columns)
        else None
    )
    good = SR.goodness(frame[metric], rule, tiers)

    q1, q3 = float(clean.quantile(0.25)), float(clean.quantile(0.75))
    iqr = q3 - q1
    if iqr > 0:
        low = max(q1 - IQR_FENCE_K * iqr, float(clean.min()))
        high = min(q3 + IQR_FENCE_K * iqr, float(clean.max()))
    else:
        low, high = float(clean.min()), float(clean.max())
    if low == high:
        high = low + 1.0

    edges = np.linspace(low, high, PREVIEW_BINS + 1)
    cut = pd.cut(clean, bins=edges, include_lowest=True)
    counts = cut.value_counts(sort=False)
    means = good.reindex(clean.index).groupby(cut, observed=False).mean()

    bins: list[dict[str, Any]] = []
    for interval, count in zip(counts.index.categories, counts.to_numpy()):
        mean = means.get(interval)
        score = float(mean) if pd.notna(mean) else None
        bins.append(
            {
                "center": round((interval.left + interval.right) / 2, 4),
                "count": int(count),
                "goodness": round(score, 1) if score is not None else None,
                "color": heat_color(score),
            }
        )

    return {
        "bins": bins,
        "metric": metric,
        "label": metric_name(metric),
        "unit": (param_hints.get_hint(metric) or {}).get("unit", ""),
        "sweet_spot": [rule.get("lo"), rule.get("hi")]
        if rule.get("shape") == "sweet_spot" and rule.get("lo") is not None
        else None,
        "line": rule.get("value") if rule.get("anchor") == "absolute" else None,
        "message": None,
    }


def suggest(metric: str) -> dict[str, Any]:
    """A rule proposed from the metric's own distribution."""
    frame = load_analysis()
    if frame.empty or metric not in frame.columns:
        return {}
    return SR.suggest_rule(pd.Series(pd.to_numeric(frame[metric], errors="coerce")), metric)
