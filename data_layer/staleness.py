"""
Data-staleness gates — stop fetching symbols whose stored data has gone stale.

A delisted/dead ticker often keeps returning its *existing* history on each fetch,
so it never counts as "no data" and never trips the no_data_count abandonment in
fetch_status. These checks read the newest stored date straight from the data DB
and skip a symbol whose latest value is older than the configured window.

The test is recomputed every run from the data itself, so it is self-perpetuating:
once a symbol is skipped its stored date never advances, so it stays skipped — until
a forced run (respect_lock=False) refetches it and fresh data resets the clock.

Thresholds (config/settings.py):
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


def _quarters_ago(quarters: int) -> date:
    return (pd.Timestamp.today().normalize() - pd.DateOffset(months=3 * quarters)).date()


def stale_by_max_date(
    db_path, table: str, column: str, max_age_days: int, candidates: list[str]
) -> set[str]:
    """Generic frontier-age gate: candidates whose newest `column` is too old.

    Used by BaseFetcher.stale_symbols for the single-stream case (e.g. OHLCV by
    `date`); `table`/`column` come from trusted fetcher class attributes. The
    financials rule has two streams (freq) and uses financials_stale instead.
    """
    cand = set(candidates)
    cutoff = date.today() - timedelta(days=max_age_days)
    with Database(db_path) as db:
        if not db.table_exists(table):
            return set()
        rows = db.query(f"SELECT symbol, MAX({column}) d FROM {table} GROUP BY symbol")
    return {
        s for s, d in rows.itertuples(index=False, name=None)
        if s in cand and _older_than(d, cutoff)
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


def financials_stale(candidates: list[str]) -> set[str]:
    """Candidate symbols whose stored financials streams have gone stale.

    Stale if the newest quarterly period_end OR the newest annual period_end that
    exists is older than its respective window (see module docstring).
    """
    cand = set(candidates)
    q_cut = _quarters_ago(settings.FINANCIALS_QUARTERLY_STALE_QUARTERS)
    a_cut = _quarters_ago(settings.FINANCIALS_YEARLY_STALE_QUARTERS)
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
        q_old = _older_than(by_freq.get("quarterly"), q_cut)
        a_old = _older_than(by_freq.get("annual"), a_cut)
        if q_old or a_old:
            stale.add(s)
    return stale
