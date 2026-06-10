"""
Opinionated SQLite wrapper.

Design decision (roadmap "Key Decisions"): no generic read/write. Every write
goes through an explicit method that names the intent — `append`, `replace`, or
`upsert(key=...)`. This was a deliberate improvement over the previous build,
where a generic write made it ambiguous what would happen to existing rows.

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
