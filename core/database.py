"""
Opinionated SQLite wrapper.

Design decision (roadmap "Key Decisions"): no generic read/write. Every write
goes through an explicit method that names the intent — `append`, `replace`,
`replace_by(key=...)` or `upsert(key=...)`. This was a deliberate improvement
over the previous build, where a generic write made it ambiguous what would
happen to existing rows.

The four verbs differ only in what they do to rows already stored:
  * `append`      — keep everything, add rows.
  * `replace`     — drop the whole table, rewrite it.
  * `replace_by`  — drop just the groups present in the frame, rewrite those.
  * `upsert`      — keep everything, overwrite rows matching the key.

Other decisions baked in here:
  * One DataFrame in, one table out — pandas is the interchange format. Cross-
    database merging is done in pandas by the caller, not in SQL.
  * Every API parameter is its own column (no JSON blobs). The schema grows
    dynamically: unknown columns are added via ALTER TABLE ADD COLUMN before a
    write, so fetchers can introduce new fields without a migration step.
  * NULL is the natural "not applicable" value for a column on a given symbol.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Iterable

import pandas as pd

# Map pandas dtypes to SQLite column affinities for dynamic column creation.
_SQLITE_AFFINITY = {
    "int64": "INTEGER",
    "Int64": "INTEGER",
    "float64": "REAL",
    "bool": "INTEGER",
    "boolean": "INTEGER",
    "datetime64[ns]": "TEXT",
    "object": "TEXT",
    "string": "TEXT",
}


def _affinity(dtype: object) -> str:
    return _SQLITE_AFFINITY.get(str(dtype), "TEXT")


# Conservative cap on bound parameters in one statement. SQLite's own limit is
# 999 on older builds and 32766 since 3.32; staying under the low one keeps
# `IN (...)` chunking safe regardless of which SQLite the interpreter bundles.
_MAX_SQL_PARAMS = 900


class Database:
    """A thin handle to one SQLite database file.

    Use as a context manager so the connection is always closed::

        with Database(settings.SYMBOLS_DB) as db:
            db.upsert("symbols", df, key="symbol")
    """

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn: sqlite3.Connection | None = None

    # -- connection lifecycle ------------------------------------------------ #
    @property
    def conn(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = sqlite3.connect(self.path)
            self._conn.execute("PRAGMA journal_mode=WAL;")
            self._conn.execute("PRAGMA foreign_keys=ON;")
        return self._conn

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    def __enter__(self) -> "Database":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # -- introspection ------------------------------------------------------- #
    def table_exists(self, table: str) -> bool:
        row = self.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
            (table,),
        ).fetchone()
        return row is not None

    def columns(self, table: str) -> list[str]:
        if not self.table_exists(table):
            return []
        return [r[1] for r in self.conn.execute(f"PRAGMA table_info({table})")]

    def is_empty(self, table: str) -> bool:
        """True when the table is missing or has no rows.

        Used to auto-detect initial-load vs weekly-update mode (Topic 2.4):
        an empty database means a straight insert, otherwise upsert.
        """
        if not self.table_exists(table):
            return True
        return self.conn.execute(f"SELECT 1 FROM {table} LIMIT 1").fetchone() is None

    # -- schema growth ------------------------------------------------------- #
    def _ensure_columns(self, table: str, df: pd.DataFrame) -> None:
        """Add any DataFrame columns missing from the table (dynamic schema)."""
        existing = set(self.columns(table))
        for col in df.columns:
            if col not in existing:
                self.conn.execute(
                    f'ALTER TABLE "{table}" ADD COLUMN "{col}" {_affinity(df[col].dtype)}'
                )

    def _ensure_key_index(self, table: str, key: str) -> None:
        """Index `key` for `replace_by`'s DELETE, unless one already leads with it.

        Without this, a table created by `replace_by` on a fresh database has no
        index at all, and every `DELETE ... WHERE key IN (...)` full-scans a table
        that grows to tens of millions of rows. The leading-column check keeps us
        from adding a redundant second index when a composite one already serves
        the lookup — ohlcv's UNIQUE (symbol, date) covers `symbol` queries, so an
        established database gains nothing and pays no extra disk.
        """
        for idx in self.conn.execute(f'PRAGMA index_list("{table}")').fetchall():
            info = self.conn.execute(f'PRAGMA index_info("{idx[1]}")').fetchall()
            if info and info[0][2] == key:      # seqno 0 == leading column
                return
        self.conn.execute(
            f'CREATE INDEX IF NOT EXISTS "ix_{table}_{key}" ON "{table}" ("{key}")'
        )

    # -- reads --------------------------------------------------------------- #
    def read(
        self, table: str, where: str | None = None, params: Iterable | None = None
    ) -> pd.DataFrame:
        """Read a table (optionally filtered) into a DataFrame.

        Reads are unambiguous, so a single generic reader is fine — the
        opinionated split only matters for writes.
        """
        if not self.table_exists(table):
            return pd.DataFrame()
        sql = f'SELECT * FROM "{table}"'
        if where:
            sql += f" WHERE {where}"
        return pd.read_sql_query(sql, self.conn, params=list(params) if params else None)

    def query(self, sql: str, params: Iterable | None = None, chunksize: int | None = None):
        """Run raw SQL into a DataFrame; with `chunksize`, an iterator of frames.

        Chunked reads keep the peak footprint of very large results near one
        chunk: read_sql materializes every row as Python objects before the
        DataFrame forms, which transiently dwarfs the final frame.
        """
        return pd.read_sql_query(
            sql, self.conn, params=list(params) if params else None, chunksize=chunksize
        )

    # -- writes (the three explicit verbs) ----------------------------------- #
    def append(self, table: str, df: pd.DataFrame) -> int:
        """Insert rows, keeping everything already in the table.

        Use for time-series style data (e.g. new OHLCV dates, new financial
        periods). Caller is responsible for not appending duplicates.
        """
        if df.empty:
            return 0
        if self.table_exists(table):
            self._ensure_columns(table, df)
        df.to_sql(table, self.conn, if_exists="append", index=False)
        self.conn.commit()
        return len(df)

    def replace(self, table: str, df: pd.DataFrame) -> int:
        """Drop the whole table and rewrite it from the DataFrame.

        Use for clean-slate rebuilds — most notably the full analysis.db
        recalculation that runs after every fetch (Topic 4.2).
        """
        df.to_sql(table, self.conn, if_exists="replace", index=False)
        self.conn.commit()
        return len(df)

    def replace_by(self, table: str, df: pd.DataFrame, key: str) -> int:
        """Delete every stored row whose `key` appears in `df`, then insert `df`.

        The "own this group outright" verb, between `append` (keep everything) and
        `replace` (drop the whole table). Use it when a fetch returns the COMPLETE
        current state of a group and any stored row outside that state is wrong
        rather than merely old — OHLCV price history is the case it exists for:
        Yahoo retro-adjusts the whole series for splits/dividends, so a row the
        fetch no longer covers is stale at a different adjustment basis, and
        upserting would silently leave it behind (see the yfinance_ohlcv note).

        `key` is a single column (e.g. "symbol"); every distinct value present in
        `df` has its existing rows removed first. Delete + insert run in ONE
        transaction, so an interrupted run can never leave a group deleted with
        nothing written back.
        """
        if df.empty:
            return 0
        if key not in df.columns:
            raise ValueError(f"replace_by: key column {key!r} not in DataFrame")
        if not self.table_exists(table):
            n = self.append(table, df)
            self._ensure_key_index(table, key)
            return n

        self._ensure_columns(table, df)
        self._ensure_key_index(table, key)
        groups = df[key].dropna().unique().tolist()
        try:
            # sqlite3 opens the transaction implicitly on the first DML statement
            # and holds it until commit, so both statements land together.
            for i in range(0, len(groups), _MAX_SQL_PARAMS):
                chunk = groups[i : i + _MAX_SQL_PARAMS]
                ph = ",".join("?" * len(chunk))
                self.conn.execute(f'DELETE FROM "{table}" WHERE "{key}" IN ({ph})', chunk)
            df.to_sql(table, self.conn, if_exists="append", index=False)
        except Exception:
            self.conn.rollback()
            raise
        self.conn.commit()
        return len(df)

    def upsert(self, table: str, df: pd.DataFrame, key: str | list[str]) -> int:
        """Insert new rows and update existing ones, matched on `key`.

        `key` is the column (or columns) that uniquely identify a row, e.g.
        `"symbol"` for symbols.db or `["symbol", "date"]` for time series.

        Only the columns present in `df` are updated on a matched row; any column
        not in `df` keeps its existing value. This primitive does NOT do
        read-modify-write — callers needing richer flag preservation (e.g. the
        symbol-state reassessment) build that on top in the data layer.
        """
        if df.empty:
            return 0
        keys = [key] if isinstance(key, str) else list(key)

        if not self.table_exists(table):
            # First write defines the table; the key becomes the primary key.
            df.to_sql(table, self.conn, if_exists="append", index=False)
            pk = ", ".join(f'"{k}"' for k in keys)
            # SQLite can't add a PK after creation; create a uniqueness index
            # instead so future ON CONFLICT upserts work.
            self.conn.execute(
                f'CREATE UNIQUE INDEX IF NOT EXISTS "ux_{table}_key" ON "{table}" ({pk})'
            )
            self.conn.commit()
            return len(df)

        self._ensure_columns(table, df)
        # Ensure a unique index on the key columns for ON CONFLICT to target.
        pk = ", ".join(f'"{k}"' for k in keys)
        self.conn.execute(
            f'CREATE UNIQUE INDEX IF NOT EXISTS "ux_{table}_key" ON "{table}" ({pk})'
        )

        cols = list(df.columns)
        col_list = ", ".join(f'"{c}"' for c in cols)
        placeholders = ", ".join("?" for _ in cols)
        update_cols = [c for c in cols if c not in keys]
        set_clause = ", ".join(f'"{c}"=excluded."{c}"' for c in update_cols)
        conflict = ", ".join(f'"{k}"' for k in keys)

        sql = (
            f'INSERT INTO "{table}" ({col_list}) VALUES ({placeholders}) '
            f"ON CONFLICT ({conflict}) DO UPDATE SET {set_clause}"
            if update_cols
            else f'INSERT INTO "{table}" ({col_list}) VALUES ({placeholders}) '
            f"ON CONFLICT ({conflict}) DO NOTHING"
        )
        self.conn.executemany(sql, df.itertuples(index=False, name=None))
        self.conn.commit()
        return len(df)

    def execute(self, sql: str, params: Iterable | None = None) -> None:
        """Escape hatch for one-off DDL/DML. Commits immediately."""
        self.conn.execute(sql, list(params) if params else [])
        self.conn.commit()
