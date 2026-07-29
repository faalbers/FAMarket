"""
Score-driven views: the category radar and both heat maps.

The heat maps colour each cell by `scoring_rules.metric_goodness`, which is
ranked against the FULL analysis universe and only then subset to the charted
symbols — scoping the ranking to the selection would make every set look
average. Colouring goes through `scoring_rules` so the heat map, the Scoring
Rules page and the stored `*_goodness` columns always agree.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Any

import pandas as pd

from analysis_layer import scoring_rules as SR
from config import param_hints, settings
from core.database import Database

# The five 0-100 category scores, in the order they ring the radar.
RADAR_CATEGORIES: list[tuple[str, str]] = [
    ("value_score", "Value"),
    ("quality_score", "Quality"),
    ("growth_score", "Growth"),
    ("momentum_score", "Momentum"),
    ("income_score", "Income"),
]

# Heat map defaults (shown when present): a spread across the categories,
# including a sweet-spot rule so the two-sided colouring is visible.
HEAT_DEFAULTS = [
    "overall_score",
    "pe",
    "pb",
    "roe",
    "net_margin",
    "revenue_cagr_3y",
    "debt_to_equity",
    "current_ratio",
    "div_yield_ttm",
    "rs_rank",
]

# Blue -> pale -> orange, matching ui/chart_theme.HEAT_RAMP. Never red/green:
# 0 is weak, 100 is strong, and the value is printed in every cell as well.
HEAT_RAMP = ["#3B78B0", "#7FA8CB", "#D7D7D7", "#E8A85C", "#D9782D"]


def _analysis_mtime() -> float:
    return settings.ANALYSIS_DB.stat().st_mtime if settings.ANALYSIS_DB.exists() else 0.0


@lru_cache(maxsize=2)
def _analysis_frame(_mtime: float) -> pd.DataFrame:
    """The whole analysis snapshot, cached on the db's mtime.

    Read in full on purpose: peer/universe goodness must rank each cell against
    every symbol, not just the charted handful.
    """
    if not settings.ANALYSIS_DB.exists():
        return pd.DataFrame()
    with Database(settings.ANALYSIS_DB) as db:
        if not db.table_exists("analysis"):
            return pd.DataFrame()
        return db.read("analysis")


def load_analysis() -> pd.DataFrame:
    return _analysis_frame(_analysis_mtime())


def metric_name(key: str) -> str:
    hint = param_hints.get_hint(key)
    return hint["name"] if hint else key.replace("_", " ").title()


def radar_view(symbols: list[str]) -> dict[str, Any]:
    """The five category scores per symbol, read straight from the snapshot."""
    frame = load_analysis()
    if frame.empty or "symbol" not in frame.columns:
        return {"axes": [], "rows": [], "message": "No analysis scores found — run an analysis."}

    indexed = frame.drop_duplicates("symbol").set_index("symbol")
    missing = [s for s in symbols if s not in indexed.index]

    rows: list[dict[str, Any]] = []
    for symbol in [s for s in symbols if s in indexed.index]:
        record = indexed.loc[symbol]
        values: list[float | None] = []
        for column, _label in RADAR_CATEGORIES:
            value = pd.to_numeric(record.get(column), errors="coerce")
            values.append(round(float(value), 1) if pd.notna(value) else None)
        # A symbol scoring in no category at all would be an empty polygon.
        if all(v is None for v in values):
            continue
        rows.append({"name": symbol, "values": values})

    axes = [
        {"key": column, "label": label, "hint_key": column}
        for column, label in RADAR_CATEGORIES
    ]
    return {
        "axes": axes,
        "rows": rows,
        "missing": missing,
        "message": None
        if rows
        else "None of these symbols have category scores (funds often score only some).",
    }


def heatmap_options(kind: str) -> list[dict[str, Any]]:
    """Selectable columns for a heat map: every rule metric present, plus the
    category scores (colourable through `rule_for`'s `*_score` fallback)."""
    frame = load_analysis()
    present = set(frame.columns)

    if kind == "scores":
        keys = [k for k in (list(SR.SCORE_COLUMNS) + ["rs_rank"]) if k in present]
        return [
            {"key": k, "label": metric_name(k), "category": "Score", "hint_key": k} for k in keys
        ]

    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for category, keys in SR.RULE_CATEGORIES.items():
        for key in keys:
            if key in present and key not in seen:
                seen.add(key)
                out.append(
                    {"key": key, "label": metric_name(key), "category": category, "hint_key": key}
                )
    for key in SR.SCORE_COLUMNS:
        if key in present and key not in seen:
            seen.add(key)
            out.append({"key": key, "label": metric_name(key), "category": "Score", "hint_key": key})
    return out


def heatmap_view(symbols: list[str], columns: list[str], kind: str = "metrics") -> dict[str, Any]:
    """Symbols × metrics grid with a 0-100 goodness per cell."""
    frame = load_analysis()
    if frame.empty or "symbol" not in frame.columns:
        return {"cells": [], "message": "No analysis scores found — run an analysis."}

    available = {opt["key"] for opt in heatmap_options(kind)}
    metrics_shown = [c for c in columns if c in available]
    if not metrics_shown:
        defaults = (
            [k for k in (list(SR.SCORE_COLUMNS) + ["rs_rank"]) if k in available]
            if kind == "scores"
            else [k for k in HEAT_DEFAULTS if k in available]
        )
        metrics_shown = defaults or sorted(available)[:8]

    indexed = frame.drop_duplicates("symbol").set_index("symbol")
    have = [s for s in symbols if s in indexed.index]
    missing = [s for s in symbols if s not in indexed.index]
    if not have:
        return {"cells": [], "message": "None of the selected symbols have an analysis row."}

    rules = SR.load_rules()
    # Rank over the whole universe first, THEN pick out the charted symbols.
    goodness = {
        metric: SR.metric_goodness(frame, metric, rules).set_axis(frame["symbol"].to_numpy())
        for metric in metrics_shown
    }

    cells: list[dict[str, Any]] = []
    for symbol in have:
        for metric in metrics_shown:
            raw = pd.to_numeric(indexed.loc[symbol].get(metric), errors="coerce")
            score = goodness[metric].get(symbol)
            cells.append(
                {
                    "symbol": symbol,
                    "metric": metric,
                    "value": round(float(raw), 2) if pd.notna(raw) else None,
                    "goodness": round(float(score), 1) if pd.notna(score) else None,
                }
            )

    return {
        "symbols": have,
        "missing": missing,
        "metrics": [
            {
                "key": metric,
                "label": metric_name(metric),
                "unit": (param_hints.get_hint(metric) or {}).get("unit", ""),
                "verdict": SR.verdict(0, SR.rule_for(metric, rules) or {}),
                "hint_key": metric,
            }
            for metric in metrics_shown
        ],
        "cells": cells,
        "ramp": HEAT_RAMP,
        "message": None,
    }
