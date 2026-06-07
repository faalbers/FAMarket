"""
fetch_status table — makes fetch runs resumable (Topic 9.2).

One row per (symbol, fetcher_name) with a composite primary key. Tracks the last
successful fetch time and a running error counter. A successful fetch locks the
pair for settings.FETCH_LOCK_DAYS; the weekly Friday cadence falls outside that
window, so normal runs always refetch, but a crashed run resumed the same day
skips what already succeeded.

  fetcher_name is a plain string, e.g. "yfinance_quotes", "etrade_quotes" — each
  fetcher function holds an independent lock.

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
            PRIMARY KEY (symbol, fetcher_name)
        )
        """
    )


def is_locked(
    db: Database, symbol: str, fetcher_name: str, lock_days: int | None = None
) -> bool:
    """True if this pair fetched successfully within the lock window."""
    lock_days = settings.FETCH_LOCK_DAYS if lock_days is None else lock_days
    row = db.conn.execute(
        f"SELECT last_fetched FROM {TABLE} WHERE symbol=? AND fetcher_name=?",
        (symbol, fetcher_name),
    ).fetchone()
    if not row or not row[0]:
        return False
    last = datetime.fromisoformat(row[0])
    return datetime.now(timezone.utc) - last < timedelta(days=lock_days)


def due_symbols(
    db: Database, symbols: list[str], fetcher_name: str, lock_days: int | None = None
) -> list[str]:
    """Filter a symbol list down to those NOT currently locked for this fetcher."""
    return [s for s in symbols if not is_locked(db, s, fetcher_name, lock_days)]


def mark_success(db: Database, symbol: str, fetcher_name: str) -> None:
    """Record a successful fetch (sets last_fetched=now, resets error counter)."""
    db.conn.execute(
        f"""
        INSERT INTO {TABLE} (symbol, fetcher_name, last_fetched, fetch_errors)
        VALUES (?, ?, ?, 0)
        ON CONFLICT(symbol, fetcher_name)
        DO UPDATE SET last_fetched=excluded.last_fetched, fetch_errors=0
        """,
        (symbol, fetcher_name, _now_iso()),
    )
    db.conn.commit()


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
