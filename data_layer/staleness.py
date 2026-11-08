"""
Data-staleness gates — stop fetching symbols whose stored data has gone stale.

A delisted/dead ticker often keeps returning its *existing* history on each fetch,
so it never counts as "no data" and never trips the no_data_count abandonment in
fetch_status. These checks read the newest stored date straight from the data DB
and skip a symbol whose latest value is older than the configured window.

The age is measured from the symbol's LAST ACTUAL FETCH (fetch_status.last_fetched
for that fetcher), NOT from today (changed 2026-11-08). "Stale" means: when we last
asked the source, what it gave back was already too old — evidence the symbol has
stopped producing data. Measuring from today instead confused "dead" with "not
fetched lately": after a 5-week break every symbol's newest bar was > 4 weeks old,
38,019 of 38,524 OHLCV symbols were skipped as stale, and — since a skipped symbol's
stored date never advances — they would have stayed skipped forever.

A symbol with no stamped last_fetched (never fetched, or only errors) is never
stale — it gets fetched, and that fetch becomes the evidence. A truly dead symbol
costs at most one extra fetch before it is caught. The gate stays self-perpetuating
for genuinely stale symbols (their last fetch is what condemned them) until
FETCH_ABANDONMENT_ENABLED is turned off (this is a viability gate, not the 5-day
lock) and a refetch returns fresh data.

Thresholds (config/settings.py), each counted back from last_fetched:
  * OHLCV                 — newest `date` older than OHLCV_STALE_WEEKS weeks.
  * financials quarterly  — newest quarterly `period_end` older than
                            FINANCIALS_QUARTERLY_STALE_QUARTERS quarters.
  * financials annual     — newest annual `period_end` older than
                            FINANCIALS_YEARLY_STALE_QUARTERS quarters.

The financials rule abandons a symbol when EITHER stored stream that exists has
gone stale — so an annual-only filer with a current 10-K is kept, while a name that
has stopped filing quarterlies is dropped. A symbol with no stored financials at all
is never "stale" (nothing to age out); the no-data counter governs that case.

This module also hosts the financials DUE-DATE gate (financials_not_due) — the
opposite-direction check that defers fetching while the next statement cannot
exist yet (reporting cycle + FINANCIALS_REPORT_LAG_DAYS filing window).
"""

from __future__ import annotations

from datetime import date, timedelta

import pandas as pd

from config import settings
from core.database import Database


def _older_than(stored, cutoff: date) -> bool:
    """True if a stored date value parses and falls strictly before `cutoff`."""
    if not stored:
        return False
    try:
        return pd.to_datetime(stored).date() < cutoff
    except (ValueError, TypeError):
        return False


def _quarters_before(ref: date, quarters: int) -> date:
    return (pd.Timestamp(ref) - pd.DateOffset(months=3 * quarters)).date()


def stale_by_max_date(
    db_path, table: str, column: str, max_age_days: int, candidates: list[str],
    last_fetched: dict[str, date],
) -> set[str]:
    """Generic frontier-age gate: candidates whose newest `column` was already too
    old at their last fetch (`last_fetched`, from fetch_status.last_fetched_dates).

    Used by BaseFetcher.stale_symbols for the single-stream case (e.g. OHLCV by
    `date`); `table`/`column` come from trusted fetcher class attributes. The
    financials rule has two streams (freq) and uses financials_stale instead.
    """
    cand = set(candidates)
    age = timedelta(days=max_age_days)
    with Database(db_path) as db:
        if not db.table_exists(table):
            return set()
        rows = db.query(f"SELECT symbol, MAX({column}) d FROM {table} GROUP BY symbol")
    return {
        s for s, d in rows.itertuples(index=False, name=None)
        if s in cand and s in last_fetched and _older_than(d, last_fetched[s] - age)
    }


def financials_not_due(candidates: list[str]) -> set[str]:
    """Candidates whose NEXT financial statement cannot exist yet (due-date gate).

    The inverse of financials_stale: a statement cycle is ~91 days (quarterly) /
    365 days (annual), plus FINANCIALS_REPORT_LAG_DAYS for the SEC filing window.
    A symbol is deferred while EVERY stream it has on file is inside that window
    — fetching it would be guaranteed to find nothing new. It comes due again by
    itself once the window passes (deferral, not abandonment). Symbols with no
    stored financials are always due.
    """
    cand = set(candidates)
    lag = settings.FINANCIALS_REPORT_LAG_DAYS
    q_cut = date.today() - timedelta(days=91 + lag)   # newest q before this -> due
    a_cut = date.today() - timedelta(days=365 + lag)  # newest annual before this -> due
    with Database(settings.FINANCIALS_DB) as db:
        if not db.table_exists("financials"):
            return set()
        rows = db.query(
            "SELECT symbol, freq, MAX(period_end) d FROM financials GROUP BY symbol, freq"
        )
    latest: dict[str, dict[str, object]] = {}
    for s, freq, d in rows.itertuples(index=False, name=None):
        if s in cand and d:
            latest.setdefault(s, {})[freq] = d
    not_due = set()
    for s, by_freq in latest.items():
        q, a = by_freq.get("quarterly"), by_freq.get("annual")
        q_due = q is not None and _older_than(q, q_cut)
        a_due = a is not None and _older_than(a, a_cut)
        if not q_due and not a_due:
            not_due.add(s)
    return not_due


def financials_stale(candidates: list[str], last_fetched: dict[str, date]) -> set[str]:
    """Candidate symbols whose stored financials streams have gone stale.

    Stale if, at the symbol's last fetch (`last_fetched`, for the calling
    fetcher), the newest quarterly period_end OR the newest annual period_end that
    exists was already older than its respective window (see module docstring).
    """
    cand = set(candidates)
    q_n = settings.FINANCIALS_QUARTERLY_STALE_QUARTERS
    a_n = settings.FINANCIALS_YEARLY_STALE_QUARTERS
    with Database(settings.FINANCIALS_DB) as db:
        if not db.table_exists("financials"):
            return set()
        rows = db.query(
            "SELECT symbol, freq, MAX(period_end) d FROM financials GROUP BY symbol, freq"
        )
    latest: dict[str, dict[str, object]] = {}
    for s, freq, d in rows.itertuples(index=False, name=None):
        if s in cand and d:
            latest.setdefault(s, {})[freq] = d
    stale = set()
    for s, by_freq in latest.items():
        ref = last_fetched.get(s)
        if ref is None:
            continue
        q_old = _older_than(by_freq.get("quarterly"), _quarters_before(ref, q_n))
        a_old = _older_than(by_freq.get("annual"), _quarters_before(ref, a_n))
        if q_old or a_old:
            stale.add(s)
    return stale
