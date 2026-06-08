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
