"""US equity market calendar helpers (shared infrastructure).

Backed by `pandas-market-calendars` (already a dependency) on the NYSE/Nasdaq
regular-session calendar (`XNYS`). Honors weekends, full US market holidays, AND
early-close half-days (the day after Thanksgiving, Christmas Eve, July 3rd, …) —
the schedule's `market_close` reflects the 1pm ET early close on those days.

Primary use: the fetch orchestrator's market-closed gate, which blocks data
fetching while the regular session is open so prices are never captured intraday
(symbol discovery is exempt). "Open" here means the *regular* 9:30am–4:00pm ET
session — pre/post-market is treated as closed.
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


def is_market_open(now: pd.Timestamp | None = None) -> bool:
    """True if the US equity regular session is open at `now` (defaults to UTC now).

    Returns False on weekends, full holidays, before/after the regular session, and
    after an early close. `now` may be tz-aware or naive (naive is treated as UTC).
    """
    now = pd.Timestamp.now(tz="UTC") if now is None else pd.Timestamp(now)
    now = now.tz_localize("UTC") if now.tzinfo is None else now.tz_convert("UTC")

    cal = _calendar()
    # A small window around `now` so the timestamp is always inside the schedule's
    # date range (open_at_time needs that); covers tz date-boundary edges too.
    start = (now - pd.Timedelta(days=2)).date()
    end = (now + pd.Timedelta(days=1)).date()
    sched = cal.schedule(start_date=start, end_date=end)
    if sched.empty:  # no sessions in the window at all
        return False
    try:
        return bool(cal.open_at_time(sched, now))
    except ValueError:
        # Raised when `now` falls on a non-session moment within the window
        # (weekend/holiday gap) — i.e. the market is closed.
        return False


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
