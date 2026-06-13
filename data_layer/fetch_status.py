"""
fetch_status table — makes fetch runs resumable (Topic 9.2).

One row per (symbol, fetcher_name) with a composite primary key. Tracks the last
fetch time, a running error counter, and a consecutive no-data counter. A
completed fetch locks the pair for settings.FETCH_LOCK_DAYS; the weekly Friday
cadence falls outside that window, so normal runs always refetch, but a crashed
run resumed the same day skips what already succeeded.

A fetch ends one of three ways (see data_layer/fetchers/base.py):
  * data    -> mark_success: stamp last_fetched, reset BOTH counters.
  * no data -> mark_no_data: stamp last_fetched, reset errors, increment no_data.
  * failure -> mark_error:   leave last_fetched, increment errors (retried next run).

Abandonment: once no_data_count reaches settings.MAX_NO_DATA_FETCHES the pair is
permanently skipped on normal runs — a delisted ticker that keeps returning
nothing stops costing API calls forever. The counter resets the instant data
returns (so a relisted symbol recovers). Abandonment is INDEPENDENT of the 5-day
lock: it is gated by settings.FETCH_ABANDONMENT_ENABLED (turn that off to retry
abandoned pairs), not by respect_lock, which now governs only the time-lock window.

  fetcher_name is a plain string, e.g. "yfinance_quotes", "etrade_quotes" — each
  fetcher function holds an independent lock and counters.

Lives in symbols.db. All functions take an open Database so a whole run shares one
connection (important when iterating tens of thousands of symbols).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from config import settings
from core.database import Database

TABLE = "fetch_status"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def ensure_table(db: Database) -> None:
    """Create fetch_status with composite PK (symbol, fetcher_name) if missing."""
    db.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {TABLE} (
            symbol        TEXT NOT NULL,
            fetcher_name  TEXT NOT NULL,
            last_fetched  TEXT,
            fetch_errors  INTEGER NOT NULL DEFAULT 0,
            no_data_count INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (symbol, fetcher_name)
        )
        """
    )
    # Migrate older DBs created before the no-data abandonment counter existed.
    if "no_data_count" not in db.columns(TABLE):
        db.execute(f"ALTER TABLE {TABLE} ADD COLUMN no_data_count INTEGER NOT NULL DEFAULT 0")


def is_locked(
    db: Database,
    symbol: str,
    fetcher_name: str,
    lock_days: int | None = None,
    respect_lock: bool = True,
) -> bool:
    """True if this pair is abandoned OR — when respect_lock — still time-locked.

    The two gates are independent (see `_skip`): abandonment follows
    FETCH_ABANDONMENT_ENABLED; the time-lock follows respect_lock.
    """
    lock_days = settings.FETCH_LOCK_DAYS if lock_days is None else lock_days
    row = db.conn.execute(
        f"SELECT last_fetched, no_data_count FROM {TABLE} WHERE symbol=? AND fetcher_name=?",
        (symbol, fetcher_name),
    ).fetchone()
    if not row:
        return False
    return _skip(row[0], row[1], lock_days, respect_lock)


def _time_locked(last_fetched: str | None, lock_days: int) -> bool:
    """True if this pair was fetched within the lock window (the cadence gate)."""
    if not last_fetched:
        return False
    try:
        last = datetime.fromisoformat(last_fetched)
    except (TypeError, ValueError):
        return False
    return datetime.now(timezone.utc) - last < timedelta(days=lock_days)


def _abandoned(no_data_count: int | None) -> bool:
    """True if this pair hit the no-data cap (only when abandonment is enabled)."""
    return settings.FETCH_ABANDONMENT_ENABLED and (no_data_count or 0) >= settings.MAX_NO_DATA_FETCHES


def _skip(
    last_fetched: str | None,
    no_data_count: int | None,
    lock_days: int,
    respect_lock: bool = True,
) -> bool:
    """Whether a pair should be skipped — two INDEPENDENT gates, OR'd together:

      * abandoned   — no_data_count hit the cap (gated by FETCH_ABANDONMENT_ENABLED).
      * time-locked — fetched within the lock window (gated by respect_lock).

    The 5-day lock no longer governs abandonment: turning respect_lock off ignores
    only the cadence window; abandonment/staleness/due-date are controlled by the
    FETCH_ABANDONMENT_ENABLED master switch instead.
    """
    return _abandoned(no_data_count) or (respect_lock and _time_locked(last_fetched, lock_days))


def classify_skips(
    db: Database,
    symbols: list[str],
    fetcher_name: str,
    lock_days: int | None = None,
    respect_lock: bool = True,
) -> tuple[list[str], list[str], list[str]]:
    """Classify each symbol as (due, time-locked, abandoned) in a single query.

    The two skip gates are independent — abandonment (FETCH_ABANDONMENT_ENABLED) and
    the time-lock (respect_lock); abandonment wins when both apply. `due_symbols` is
    the (due) projection of this. One query loads the whole fetcher's status, then
    classification happens in Python — far cheaper than a per-symbol query when
    iterating tens of thousands of symbols.
    """
    lock_days = settings.FETCH_LOCK_DAYS if lock_days is None else lock_days
    rows = db.conn.execute(
        f"SELECT symbol, last_fetched, no_data_count FROM {TABLE} WHERE fetcher_name=?",
        (fetcher_name,),
    ).fetchall()
    status = {s: (lf, nd) for s, lf, nd in rows}
    due: list[str] = []
    locked: list[str] = []
    abandoned: list[str] = []
    for s in symbols:
        lf, nd = status.get(s, (None, 0))
        if _abandoned(nd):
            abandoned.append(s)
        elif respect_lock and _time_locked(lf, lock_days):
            locked.append(s)
        else:
            due.append(s)
    return due, locked, abandoned


def due_symbols(
    db: Database,
    symbols: list[str],
    fetcher_name: str,
    lock_days: int | None = None,
    respect_lock: bool = True,
) -> list[str]:
    """Symbols NOT currently abandoned or (when respect_lock) time-locked.

    The (due) projection of `classify_skips` — see it for the gate split.
    """
    return classify_skips(db, symbols, fetcher_name, lock_days, respect_lock)[0]


def mark_success(db: Database, symbol: str, fetcher_name: str) -> None:
    """Record a fetch that returned data (last_fetched=now, BOTH counters reset)."""
    db.conn.execute(
        f"""
        INSERT INTO {TABLE} (symbol, fetcher_name, last_fetched, fetch_errors, no_data_count)
        VALUES (?, ?, ?, 0, 0)
        ON CONFLICT(symbol, fetcher_name)
        DO UPDATE SET last_fetched=excluded.last_fetched, fetch_errors=0, no_data_count=0
        """,
        (symbol, fetcher_name, _now_iso()),
    )
    db.conn.commit()


def mark_no_data(db: Database, symbol: str, fetcher_name: str) -> int:
    """Record a fetch that completed but found no data; return the new no_data_count.

    Stamps last_fetched and resets the (transient-failure) error counter — the call
    succeeded — but increments no_data_count toward the abandonment cap.
    """
    db.conn.execute(
        f"""
        INSERT INTO {TABLE} (symbol, fetcher_name, last_fetched, fetch_errors, no_data_count)
        VALUES (?, ?, ?, 0, 1)
        ON CONFLICT(symbol, fetcher_name)
        DO UPDATE SET last_fetched=excluded.last_fetched, fetch_errors=0,
                      no_data_count = no_data_count + 1
        """,
        (symbol, fetcher_name, _now_iso()),
    )
    db.conn.commit()
    row = db.conn.execute(
        f"SELECT no_data_count FROM {TABLE} WHERE symbol=? AND fetcher_name=?",
        (symbol, fetcher_name),
    ).fetchone()
    return int(row[0]) if row else 1


def mark_error(db: Database, symbol: str, fetcher_name: str) -> None:
    """Increment the error counter for this pair (last_fetched unchanged)."""
    db.conn.execute(
        f"""
        INSERT INTO {TABLE} (symbol, fetcher_name, last_fetched, fetch_errors)
        VALUES (?, ?, NULL, 1)
        ON CONFLICT(symbol, fetcher_name)
        DO UPDATE SET fetch_errors = fetch_errors + 1
        """,
        (symbol, fetcher_name),
    )
    db.conn.commit()


def error_counts(db: Database, fetcher_name: str | None = None) -> dict:
    """Map (symbol, fetcher_name) -> error count for the end-of-run report."""
    if not db.table_exists(TABLE):
        return {}
    sql = f"SELECT symbol, fetcher_name, fetch_errors FROM {TABLE} WHERE fetch_errors > 0"
    params: tuple = ()
    if fetcher_name:
        sql += " AND fetcher_name=?"
        params = (fetcher_name,)
    return {(s, f): n for s, f, n in db.conn.execute(sql, params).fetchall()}
