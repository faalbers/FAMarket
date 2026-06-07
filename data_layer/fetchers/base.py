"""
Base fetcher contract shared by all per-symbol data-fetch APIs (Topics 2.3, 9.4).

A fetcher receives the full symbol set (with types) and filters internally to the
security types it applies to. For each due symbol (not locked in fetch_status) it
runs the standard flow inside `fetch_one`:

    fetch raw -> sanitize -> conditional enrichment -> sanitize enrichment

and returns a DataFrame of clean rows (possibly empty). The base class handles the
cross-cutting concerns identically for every fetcher:

  * batching (settings.DEFAULT_BATCH_SIZE or a per-fetcher override)
  * rate limiting (ratelimit, from settings.RATE_LIMITS[api])
  * automatic retry on transient failure (tenacity)
  * summary-level batch logging (Topic 9.3 format)
  * fetch_status bookkeeping: skip locked, mark success/error, per-batch writes so
    progress survives an interrupted run

FRED is intentionally NOT a BaseFetcher subclass — it fetches a handful of macro
series, not a per-symbol universe, so it has its own simpler module.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

import pandas as pd
from ratelimit import limits, sleep_and_retry
from tenacity import retry, stop_after_attempt, wait_fixed

from config import settings
from core.database import Database
from core.logging_config import get_logger
from data_layer import fetch_status


class BaseFetcher(ABC):
    # -- subclass configuration --------------------------------------------- #
    name: str = "base"                  # fetch_status fetcher_name, e.g. "yfinance_quotes"
    api: str = "yfinance"               # key into settings.RATE_LIMITS
    applies_to: tuple[str, ...] = ()    # security types handled; () = all types
    target_db: str = ""                 # path attr name on settings, e.g. "QUOTES_DB"
    table: str = ""                     # destination table
    write_mode: str = "upsert"          # "upsert" | "append" | "replace"
    upsert_key: str | list[str] = "symbol"
    # Per-symbol skip window after a successful fetch. None = settings.FETCH_LOCK_DAYS
    # (the weekly default — refetch every run). A large value makes a fetcher a
    # one-time backfill: once a symbol succeeds it is skipped on all future runs
    # (errors still retry, and respect_lock=False forces a refetch).
    lock_days: int | None = None

    def __init__(self, batch_size: int | None = None):
        self.log = get_logger(self.name)
        self.batch_size = batch_size or settings.DEFAULT_BATCH_SIZE
        calls, period = settings.RATE_LIMITS.get(self.api, (60, 60))

        # fetch_one wrapped with retry (transient failures) then rate limiting.
        retrying = retry(
            stop=stop_after_attempt(settings.RETRY_MAX_ATTEMPTS),
            wait=wait_fixed(settings.RETRY_WAIT_SECONDS),
            reraise=True,
        )(self.fetch_one)

        @sleep_and_retry
        @limits(calls=calls, period=period)
        def _throttled(symbol: str):
            return retrying(symbol)

        self._call = _throttled

    # -- subclass hook ------------------------------------------------------ #
    @abstractmethod
    def fetch_one(self, symbol: str) -> pd.DataFrame | None:
        """Fetch + sanitize (+ enrich) one symbol. Return rows, or empty/None.

        Raise on a genuine API failure (transient errors are retried; a final
        raise is logged and counted as an error). Returning empty/None means the
        call succeeded but the symbol legitimately has no data.
        """
        ...

    # -- orchestration ------------------------------------------------------ #
    def select_symbols(self, symbols_df: pd.DataFrame) -> list[str]:
        """Subset the universe to the security types this fetcher handles."""
        if not self.applies_to or "security_type" not in symbols_df.columns:
            return symbols_df["symbol"].tolist()
        mask = symbols_df["security_type"].isin(self.applies_to)
        return symbols_df.loc[mask, "symbol"].tolist()

    def _write(self, db: Database, rows: pd.DataFrame) -> None:
        if rows.empty:
            return
        if self.write_mode == "append":
            db.append(self.table, rows)
        elif self.write_mode == "replace":
            db.replace(self.table, rows)
        else:
            db.upsert(self.table, rows, key=self.upsert_key)

    def run(
        self,
        symbols_df: pd.DataFrame,
        status_db: Database,
        respect_lock: bool = True,
    ) -> dict:
        """Fetch all due symbols for this fetcher. Returns a run summary dict."""
        candidates = self.select_symbols(symbols_df)
        symbols = (
            fetch_status.due_symbols(status_db, candidates, self.name, self.lock_days)
            if respect_lock
            else candidates
        )
        total = len(symbols)
        n_batches = (total + self.batch_size - 1) // self.batch_size if total else 0
        self.log.info(
            "Start — %d/%d symbols due (%d batches of %d)",
            total, len(candidates), n_batches, self.batch_size,
        )

        success = failed = 0
        out_db = Database(getattr(settings, self.target_db))
        try:
            for bi in range(n_batches):
                chunk = symbols[bi * self.batch_size : (bi + 1) * self.batch_size]
                frames: list[pd.DataFrame] = []
                b_ok = b_fail = 0
                for sym in chunk:
                    try:
                        rows = self._call(sym)
                        if rows is not None and not rows.empty:
                            frames.append(rows)
                        fetch_status.mark_success(status_db, sym, self.name)
                        b_ok += 1
                    except Exception as exc:  # final failure after retries
                        fetch_status.mark_error(status_db, sym, self.name)
                        b_fail += 1
                        self.log.debug("‖ %s failed: %s", sym, exc)

                if frames:
                    self._write(out_db, pd.concat(frames, ignore_index=True))

                success += b_ok
                failed += b_fail
                remaining = total - (bi + 1) * self.batch_size
                self.log.info(
                    "Batch %d/%d — Fetched: %d | Success: %d | Failed: %d | Remaining: %d",
                    bi + 1, n_batches, len(chunk), b_ok, b_fail, max(remaining, 0),
                )
        finally:
            out_db.close()

        self.log.info("Done — Success: %d | Failed: %d", success, failed)
        return {"fetcher": self.name, "due": total, "success": success, "failed": failed}
