"""
FRED fetcher (Topic 8 — FRED DATA) — macro context, not per-symbol.

Writes to macro.db. Series configured in settings.FRED_SERIES (10y Treasury, Fed
Funds Rate, CPI, GDP growth). Used by the Analysis layer for DCF (risk-free rate;
^TNX via yfinance is available as a cross-check).

Not a BaseFetcher: it fetches a handful of long time-series, not a per-symbol
universe, so the per-symbol batching/rate-limit machinery doesn't apply. Stored
long (one row per series per date) and upserted on (series, date) for idempotency.
"""

from __future__ import annotations

import pandas as pd

from config import secrets, settings
from core.database import Database
from core.logging_config import get_logger

log = get_logger("fred")

TABLE = "macro"


def fetch_fred() -> int:
    """Fetch all configured FRED series into macro.db. Returns rows written."""
    from fredapi import Fred

    fred = Fred(api_key=secrets.require("FRED_API_KEY"))
    rows: list[dict] = []
    for key, series_id in settings.FRED_SERIES.items():
        try:
            series = fred.get_series(series_id)
        except Exception as exc:  # one bad series shouldn't sink the rest
            log.error("FRED series %s (%s) failed: %s", key, series_id, exc)
            continue
        for dt, val in series.items():
            if pd.isna(val):
                continue
            rows.append(
                {
                    "series": key,
                    "fred_id": series_id,
                    "date": pd.Timestamp(dt).strftime("%Y-%m-%d"),
                    "value": float(val),
                }
            )
        log.info("FRED %s (%s) — %d observations", key, series_id, len(series))

    if not rows:
        log.warning("FRED produced no data")
        return 0

    df = pd.DataFrame(rows)
    with Database(settings.MACRO_DB) as db:
        db.upsert(TABLE, df, key=["series", "date"])
    log.info("macro.db — %d observations written", len(df))
    return len(df)
