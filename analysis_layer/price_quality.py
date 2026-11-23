"""
Price-window cleanup before any price-based metric (fix list §3, §20).

Every price number in analysis.db — the valuation `price`, technical indicators,
`rs_raw` — comes from one per-symbol window of bars (`pipeline.run_analysis`).
`clean_window` applies the `price_flags` that the OHLCV fetch stored
(`data_layer/price_checks.py`) to that window, once, before any of them:

  * bad_adj      -> `adj_close` := `close` for the whole window. A price return
                    instead of a total return, but a real one; the valuation price
                    becomes the real last close instead of a negative number.
  * bad_print    -> drop that bar.
  * level_shift  -> no change, flagged only (decided 2026-11-12). A lasting 10x move
                    is an unrecorded reverse split (QH) as often as a real collapse
                    (ABOS $2.29 -> $0.21); prices alone can't tell them apart, and
                    cutting the history would hide a real crash from momentum and the
                    52-week numbers. Screens exclude it via `price_issues`.
  * stale        -> drop those bars: nothing traded. For such a stock the 252-bar
                    "52-week" window then spans more than a calendar year.
  * gap, double_event -> no change (recorded only).

The checks that touched the window are returned as `price_issues` (comma-joined,
NULL when clean) — a screenable analysis.db column.
"""

from __future__ import annotations

import pandas as pd

_DROP_BAR = ("bad_print", "stale")


def clean_window(bars: pd.DataFrame, flags: pd.DataFrame | None) -> tuple[pd.DataFrame, str | None]:
    """Apply one symbol's price flags to its analysis window.

    `bars` is the symbol's date-sorted window (datetime `date`); `flags` its
    `price_flags` rows with datetime `start_date` / `end_date`. Returns the cleaned
    bars (the input object itself when nothing applies) and the `price_issues` text.
    """
    if flags is None or flags.empty or bars.empty:
        return bars, None
    lo, hi = bars["date"].iloc[0], bars["date"].iloc[-1]
    f = flags[(flags["end_date"] >= lo) & (flags["start_date"] <= hi)]
    if f.empty:
        return bars, None

    out = bars
    drop = pd.Series(False, index=out.index)
    for row in f[f["check"].isin(_DROP_BAR)].itertuples(index=False):
        drop |= (out["date"] >= row.start_date) & (out["date"] <= row.end_date)
    if drop.any():
        out = out[~drop]
    if (f["check"] == "bad_adj").any():
        out = out.copy()
        out["adj_close"] = out["close"]
    return out, ", ".join(sorted(set(f["check"])))
