"""
Valuation range ("football field") view — bear/base/bull fair value per symbol.

Plots each symbol's bear→bull fair-value span as a percentage above/below
today's price, so a $5 stock and a $500 stock share one axis and the zero line
reads as "fairly priced". That normalisation is `(value − price) / price`, NOT
the stored `margin_of_safety` (which divides by fair value, a different number);
it is computed here rather than stored because it is purely presentational —
nothing filters or sorts on it, which is the one case the project's
compute-in-analysis rule leaves to the UI layer.

Two behaviours this view exists to show honestly, both load-bearing:

  * **A NULL bear/bull means "no range available"**, never "no downside". Those
    symbols still get a row (`has_range=False`) so a selected stock never
    silently vanishes — a zero-width bar would read as certainty where there is
    in fact no estimate.
  * **The base `fair_value` may legitimately sit OUTSIDE the bear→bull span**
    (35% of ranges, measured 2026-08-13). Graham is in the base blend but
    excluded from the scenarios — it has no forward growth input to flex — so it
    pulls base outside whenever the trailing-book view disagrees with the growth
    models. That divergence is information; the marker is emitted at its true
    position and must not be clamped into the bar.

Extreme trend growth produces extreme upside (measured: p99 ≈ +3,700%, max
≈ +318,000%), which on a shared axis would flatten every ordinary bar to a
hairline. Values are therefore clamped to a display window and tagged with
`overflow_*`, keeping the true figure for the caller to show on demand. Clamping
happens HERE, not in the client, per the charts contract: the server computes
every series, the client only renders.
"""

from __future__ import annotations

from typing import Any

import pandas as pd

from config import param_hints, settings
from core.database import Database

# Analysis columns this view needs. `name` is for the tooltip, the two flag
# columns annotate a bar without costing a second request.
_COLUMNS = [
    "symbol", "name", "price",
    "fair_value_bear", "fair_value", "fair_value_bull",
    "valuation_guardrail_flag", "bear_flag_count",
]

# Fair value cannot be negative, so -100% ("worth nothing") is a real floor
# rather than an arbitrary one — only the upside needs a configurable cap.
AXIS_MIN_PCT = -100.0


def load_rows(symbols: list[str]) -> pd.DataFrame:
    """The analysis rows for `symbols`. Same targeted read as
    `filter_fail.load_rows` — deliberately not `scores_data.load_analysis()`,
    which caches all 283 columns × ~38k rows because its heat maps need
    universe-wide ranking. This view needs a handful of columns for a handful
    of symbols.
    """
    if not symbols or not settings.ANALYSIS_DB.exists():
        return pd.DataFrame()
    with Database(settings.ANALYSIS_DB) as db:
        if not db.table_exists("analysis"):
            return pd.DataFrame()
        placeholders = ",".join("?" * len(symbols))
        return db.read("analysis", where=f"symbol IN ({placeholders})", params=symbols)


def _num(value: Any) -> float:
    return float(pd.to_numeric(value, errors="coerce"))


def _upside(value: float, price: float) -> float:
    """Percent above/below the current price. Positive = worth more than it costs."""
    return (value - price) / price * 100


def _clamped(pct: float, axis_max: float) -> tuple[float | None, bool, bool]:
    """(display value, overflowed high, overflowed low) for one point."""
    if pd.isna(pct):
        return None, False, False
    if pct > axis_max:
        return axis_max, True, False
    if pct < AXIS_MIN_PCT:
        return AXIS_MIN_PCT, False, True
    return round(pct, 1), False, False


def football_field_view(symbols: list[str]) -> dict[str, Any]:
    """Bear/base/bull fair value per symbol, as percent vs. current price.

    Rows are sorted most-undervalued first (by base upside). Symbols with no
    price or no computed `fair_value` are dropped — there is nothing to plot —
    but a symbol WITH a base value and no scenario range is kept and flagged.
    """
    axis_max = float(settings.VALUATION_CHART_AXIS_MAX_PCT)
    frame = load_rows(symbols)
    if frame.empty or "symbol" not in frame.columns:
        return _empty(axis_max, "No analysis data found for these symbols — run an analysis.")

    present = [c for c in _COLUMNS if c in frame.columns]
    indexed = frame[present].drop_duplicates("symbol").set_index("symbol")
    missing = [s for s in symbols if s not in indexed.index]

    rows: list[dict[str, Any]] = []
    skipped_no_value = 0
    for symbol in [s for s in symbols if s in indexed.index]:
        record = indexed.loc[symbol]
        price = _num(record.get("price"))
        base = _num(record.get("fair_value"))
        if pd.isna(price) or price <= 0 or pd.isna(base):
            skipped_no_value += 1
            continue

        bear_raw = _num(record.get("fair_value_bear"))
        bull_raw = _num(record.get("fair_value_bull"))
        has_range = bool(pd.notna(bear_raw) and pd.notna(bull_raw))

        point: dict[str, Any] = {
            "symbol": symbol,
            "name": (str(record.get("name")) if pd.notna(record.get("name")) else symbol),
            "price": round(price, 2),
            "has_range": has_range,
            "guardrail": bool(pd.notna(record.get("valuation_guardrail_flag"))
                              and record.get("valuation_guardrail_flag")),
            "bear_flags": (int(record["bear_flag_count"])
                           if "bear_flag_count" in record.index
                           and pd.notna(record.get("bear_flag_count")) else 0),
        }
        for key, raw in (("bear", bear_raw), ("base", base), ("bull", bull_raw)):
            pct = _upside(raw, price) if pd.notna(raw) else float("nan")
            display, over_high, over_low = _clamped(pct, axis_max)
            point[key] = display
            point[f"{key}_value"] = round(float(raw), 2) if pd.notna(raw) else None
            point[f"{key}_true_pct"] = round(pct, 1) if pd.notna(pct) else None
            point[f"{key}_overflow_high"] = over_high
            point[f"{key}_overflow_low"] = over_low
        rows.append(point)

    # Most undervalued first. A missing base can't happen here (rows without one
    # were skipped), so the sort key is always a real number.
    rows.sort(key=lambda r: r["base_true_pct"], reverse=True)

    return {
        "rows": rows,
        "missing": missing,
        "axis": {"min": AXIS_MIN_PCT, "max": axis_max},
        "labels": {key: _label(column) for key, column in
                   (("bear", "fair_value_bear"), ("base", "fair_value"),
                    ("bull", "fair_value_bull"), ("price", "price"))},
        "n_no_range": sum(1 for r in rows if not r["has_range"]),
        "message": None if rows else _no_rows_message(skipped_no_value),
    }


def _label(column: str) -> str:
    hint = param_hints.get_hint(column)
    return hint["name"] if hint else column.replace("_", " ").title()


def _no_rows_message(skipped: int) -> str:
    if skipped:
        return (f"None of these {skipped} symbols have a computed fair value — "
                "funds, ETFs and preferreds are not valued, and a stock needs the "
                "inputs its applicable models require.")
    return "No analysis data found for these symbols — run an analysis."


def _empty(axis_max: float, message: str) -> dict[str, Any]:
    return {"rows": [], "missing": [], "axis": {"min": AXIS_MIN_PCT, "max": axis_max},
            "labels": {}, "n_no_range": 0, "message": message}
