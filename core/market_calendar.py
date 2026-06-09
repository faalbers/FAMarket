"""US equity market calendar helpers (shared infrastructure).

Backed by `pandas-market-calendars` (already a dependency) on the NYSE/Nasdaq
regular-session calendar (`XNYS`). Honors weekends, full US market holidays, AND
early-close half-days (the day after Thanksgiving, Christmas Eve, July 3rd, …) —
the schedule's `market_close` reflects the 1pm ET early close on those days.

Primary use: `last_completed_session`, the intraday gate for OHLCV writes — a
daily bar dated after the last fully-settled session is still in progress (today
mid-session) or not yet finalized, so it must not be stored as a closing price.
"""

from __future__ import annotations

from functools import lru_cache

import pandas as pd
import pandas_market_calendars as mcal

_CALENDAR = "XNYS"  # NYSE/Nasdaq regular US equity session

# Yahoo finalizes the official daily close a few minutes after the bell; treat a
# session's bar as trustworthy only once this long has passed since market_close,
# so a fetch racing the finalization never captures a still-moving "close".
_SETTLE = pd.Timedelta(minutes=15)


@lru_cache(maxsize=1)
def _calendar():
    return mcal.get_calendar(_CALENDAR)


def last_completed_session(
    now: pd.Timestamp | None = None, settle: pd.Timedelta = _SETTLE
) -> pd.Timestamp | None:
    """Date of the most recent regular session whose close (+`settle`) is past `now`.

    Returned as a tz-naive, midnight-normalized Timestamp (the session date), or
    None when no session in the lookback window has finished settling yet. Used to
    gate OHLCV writes: a daily bar dated *after* this is still in progress (today
    mid-session) or not yet finalized, so it must not be stored as a closing price.
    Half-days are handled automatically — the schedule's `market_close` already
    reflects an early 1pm ET close.
    """
    now = pd.Timestamp.now(tz="UTC") if now is None else pd.Timestamp(now)
    now = now.tz_localize("UTC") if now.tzinfo is None else now.tz_convert("UTC")

    cal = _calendar()
    # 10 days back always spans at least one session, even across a long holiday weekend.
    sched = cal.schedule(start_date=(now - pd.Timedelta(days=10)).date(), end_date=now.date())
    if sched.empty:
        return None
    completed = sched.loc[(sched["market_close"] + settle) <= now]
    if completed.empty:
        return None
    return pd.Timestamp(completed.index[-1]).normalize()
