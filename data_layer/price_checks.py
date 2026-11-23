"""
Price-history checks (fix list §3 and the §20 "gaps and stale runs" bullet).

Yahoo's daily history passes defects straight through: impossible adjusted closes,
one-day bad prints, reverse splits Yahoo never recorded, prices carried on days
nothing traded. yfinance's own repair (run only when `adj_close` is broken — see
`YFinanceOHLCV._try_repair`) fixes some; this module flags what is left, so the
analysis can skip bad days (`analysis_layer/price_quality.py`).

`find_price_flags` is a pure function on ONE symbol's bars. It runs on each OHLCV
fetch (`YFinanceOHLCV._write`) and in the one-time `scripts/fix_stored_prices.py`;
the result goes to the `price_flags` table in ohlcv.db, one row per flagged date or
date range:

    symbol, check, start_date, end_date, detail

Checks (thresholds in config/settings.py):
  * bad_adj      — adj_close <= 0 or above the close (a dividend adjustment can only
                   lower it). Ranges. What repair could not fix (PTPIF).
  * bad_print    — a one-day move of OHLCV_PRICE_BREAK_RATIO (10x) or more, up or
                   down, whose price comes back within 2x of the bar before it inside
                   5 bars. A range over the bad bars. BHPLF 2026-10-15 x0.0002 then
                   x4,075 (1 bar); FDCT Dec 2024 (several multi-day spikes).
  * level_shift  — a 10x move that stays: a reverse split Yahoo never recorded
                   (QH 2026-04-27), an unadjusted split, or an old and a new company
                   stitched together (CHRD 2020-11-20). Bars before it are on another
                   basis. A genuine 10x day would be flagged too — accepted, as such
                   days are vanishingly rare outside data breaks.
  * stale        — OHLCV_STALE_RUN_DAYS (10) or more bars in a row with an unchanged
                   close and zero volume: nothing traded, the price was carried.
                   Skipped for NAV-like series (zero volume on >= 95% of bars —
                   mutual funds), where a flat price is real.
  * gap          — more than 31 days between two bars. Recorded only.
  * double_event — a dividend and a split on the same day whose adjustment factor
                   jumps more than 2x (TDS 2005-05-16, 40x). Recorded only.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from config import settings

FLAG_COLUMNS = ["symbol", "check", "start_date", "end_date", "detail"]
TABLE = "price_flags"

_NAV_LIKE_ZERO_VOLUME_SHARE = 0.95  # mutual funds: volume 0 on (nearly) every bar
_REVERT_WITHIN = 2.0                # a bad print's price comes back within 2x of the bar before it
_REVERT_BARS = 5                    # ... within this many bars (multi-day bad prints: FDCT)
_GAP_DAYS = 31
_DOUBLE_EVENT_JUMP = 2.0


def find_price_flags(rows: pd.DataFrame) -> pd.DataFrame:
    """Flags for ONE symbol's OHLCV rows (columns as stored in `ohlcv`)."""
    if rows is None or rows.empty:
        return pd.DataFrame(columns=FLAG_COLUMNS)
    df = rows.sort_values("date").reset_index(drop=True)
    symbol = str(df["symbol"].iloc[0])
    dates = df["date"].astype(str).str[:10].to_numpy()
    close = pd.to_numeric(df["close"], errors="coerce").to_numpy(dtype=float)
    adj = pd.to_numeric(df["adj_close"], errors="coerce").to_numpy(dtype=float)
    volume = pd.to_numeric(df.get("volume"), errors="coerce").fillna(0).to_numpy(dtype=float)
    divs = pd.to_numeric(df.get("dividends"), errors="coerce").fillna(0).to_numpy(dtype=float)
    splits = pd.to_numeric(df.get("splits"), errors="coerce").fillna(0).to_numpy(dtype=float)

    flags: list[tuple[str, str, str, str]] = []  # (check, start, end, detail)

    # bad_adj: impossible adjusted close, as ranges
    bad = (adj <= 0) | (adj > close * 1.001)
    for a, b in _runs(bad):
        flags.append(("bad_adj", dates[a], dates[b], f"{b - a + 1} bars"))

    # bad_print / level_shift: one-day moves of 10x or more
    ratio = settings.OHLCV_PRICE_BREAK_RATIO
    i = 1
    while i < len(close):
        prev, cur = close[i - 1], close[i]
        if not (prev > 0 and cur > 0) or ratio > cur / prev > 1 / ratio:
            i += 1
            continue
        move = cur / prev
        back = next((j for j in range(i + 1, min(i + 1 + _REVERT_BARS, len(close)))
                     if close[j] > 0 and 1 / _REVERT_WITHIN <= close[j] / prev <= _REVERT_WITHIN), None)
        if back is not None:  # the price snapped back: bars i .. back-1 are the bad print
            flags.append(("bad_print", dates[i], dates[back - 1], f"x{move:.4g}, {back - i} bar(s)"))
            i = back + 1
            continue
        note = f"x{move:.4g}" + (f", split {splits[i]:g}" if splits[i] > 0 else "")
        flags.append(("level_shift", dates[i], dates[i], note))
        i += 1

    # stale: unchanged close on zero volume, OHLCV_STALE_RUN_DAYS or more in a row
    if len(volume) and (volume == 0).mean() < _NAV_LIKE_ZERO_VOLUME_SHARE:
        carried = np.zeros(len(close), dtype=bool)
        carried[1:] = (close[1:] == close[:-1]) & (volume[1:] == 0)
        for a, b in _runs(carried):
            if b - a + 1 >= settings.OHLCV_STALE_RUN_DAYS:
                flags.append(("stale", dates[a], dates[b], f"{b - a + 1} bars"))

    # gap: more than a month between two bars
    d = pd.to_datetime(pd.Series(dates))
    gaps = d.diff().dt.days.to_numpy()
    for i in np.flatnonzero(gaps > _GAP_DAYS):
        flags.append(("gap", dates[i - 1], dates[i], f"{int(gaps[i])} days"))

    # double_event: dividend + split on one day with an outsized adjustment jump
    for i in np.flatnonzero((divs > 0) & (splits > 0)):
        if i == 0 or not (close[i] > 0 and close[i - 1] > 0 and adj[i] > 0 and adj[i - 1] > 0):
            continue
        jump = (adj[i] / close[i]) / (adj[i - 1] / close[i - 1])
        if jump > _DOUBLE_EVENT_JUMP:
            flags.append(("double_event", dates[i], dates[i],
                          f"factor x{jump:.3g} (dividend {divs[i]:g}, split {splits[i]:g})"))

    out = pd.DataFrame(flags, columns=["check", "start_date", "end_date", "detail"])
    out.insert(0, "symbol", symbol)
    return out[FLAG_COLUMNS]


def flags_for(rows: pd.DataFrame) -> pd.DataFrame:
    """`find_price_flags` over a multi-symbol frame."""
    if rows is None or rows.empty:
        return pd.DataFrame(columns=FLAG_COLUMNS)
    parts = [find_price_flags(g) for _, g in rows.groupby("symbol", sort=False)]
    parts = [p for p in parts if not p.empty]
    return pd.concat(parts, ignore_index=True) if parts else pd.DataFrame(columns=FLAG_COLUMNS)


def _runs(mask: np.ndarray) -> list[tuple[int, int]]:
    """(first, last) index of every run of True values."""
    if not mask.any():
        return []
    m = np.concatenate(([False], mask.astype(bool), [False]))
    edges = np.flatnonzero(m[1:] != m[:-1])
    return [(int(a), int(b) - 1) for a, b in zip(edges[::2], edges[1::2])]
