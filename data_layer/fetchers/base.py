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
from data_layer import cancel, fetch_status, staleness


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
    # Staleness probe (data_layer/staleness.py): abandon a symbol whose newest
    # stored `stale_date_column` in `table` is older than `stale_after_days`. None
    # disables it. Fetchers with a multi-stream rule (financials: per-freq) override
    # stale_symbols() instead. The master switch is settings.FETCH_ABANDONMENT_ENABLED.
    stale_after_days: int | None = None
    stale_date_column: str = "date"

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

    def stale_symbols(self, candidates: list[str]) -> set[str]:
        """Symbols whose stored data is too stale to keep fetching (skipped forever).

        Default reads the declarative `stale_after_days`/`stale_date_column`
        attributes (the single-stream case, e.g. OHLCV); financials overrides for
        its per-freq rule. Recomputed from the data each run, so once skipped a
        symbol's stored date never advances and it stays skipped — turn off
        settings.FETCH_ABANDONMENT_ENABLED to retry it (the 5-day lock does NOT
        control this gate). See data_layer/staleness.py.
        """
        if self.stale_after_days is None:
            return set()
        return staleness.stale_by_max_date(
            getattr(settings, self.target_db), self.table,
            self.stale_date_column, self.stale_after_days, candidates,
        )

    def not_due_symbols(self, candidates: list[str]) -> set[str]:
        """Symbols whose next data point cannot exist yet (deferral, NOT abandonment).

        Default: none. The yfinance financials fetcher overrides this with the
        filing-cycle gate (staleness.financials_not_due) — statements appear ~4x
        a year, so most weeks a symbol can be skipped knowing nothing new exists.
        Deferred symbols come due again on their own; this is a viability gate
        governed by settings.FETCH_ABANDONMENT_ENABLED, NOT by the 5-day lock.
        """
        return set()

    def _write(self, db: Database, rows: pd.DataFrame) -> None:
        if rows.empty:
            return
        if self.write_mode == "append":
            db.append(self.table, rows)
        elif self.write_mode == "replace":
            db.replace(self.table, rows)
        else:
            db.upsert(self.table, rows, key=self.upsert_key)

    def select_due(
        self,
        symbols_df: pd.DataFrame,
        status_db: Database,
        respect_lock: bool = True,
    ) -> tuple[list[str], dict]:
        """Apply the fetch gates; return (symbols_to_fetch, gate-count breakdown).

        The single source of truth for *which* symbols a run would fetch — used by
        run() and by the orchestrator's dry-run fetch report. Network-free: every
        gate reads only fetch_status and the already-stored data.

        Two INDEPENDENT gate groups (narrowing order applies_to first, then both):
          * the 5-day cadence lock — toggled by `respect_lock` alone;
          * the viability gates (no-data abandonment, staleness, filing-cycle due
            date) — toggled by settings.FETCH_ABANDONMENT_ENABLED alone.
        So unchecking the lock no longer disables staleness/due-date, and a true
        "refetch everything" run needs respect_lock off AND the abandonment switch
        off. The returned stats sum as
        candidates = locked + abandoned + stale + not_due + due.
        """
        candidates = self.select_symbols(symbols_df)
        # Cadence lock (respect_lock) and abandonment (FETCH_ABANDONMENT_ENABLED) are
        # classified together here, but each gate is governed by its own switch
        # inside classify_skips — they do not depend on each other.
        due, locked, abandoned = fetch_status.classify_skips(
            status_db, candidates, self.name, self.lock_days, respect_lock
        )
        # Staleness + filing-cycle due-date: viability gates, on/off with the
        # abandonment master switch — never with the lock.
        if settings.FETCH_ABANDONMENT_ENABLED:
            stale = self.stale_symbols(candidates)
            not_due = self.not_due_symbols(candidates)
        else:
            stale = not_due = set()
        symbols = [s for s in due if s not in stale and s not in not_due]
        n_stale = sum(1 for s in due if s in stale)
        n_not_due = sum(1 for s in due if s in not_due and s not in stale)
        stats = {
            "candidates": len(candidates),
            "locked": len(locked),
            "abandoned": len(abandoned),
            "stale": n_stale,
            "not_due": n_not_due,
            "due": len(symbols),
        }
        return symbols, stats

    def run(
        self,
        symbols_df: pd.DataFrame,
        status_db: Database,
        respect_lock: bool = True,
    ) -> dict:
        """Fetch all due symbols for this fetcher. Returns a run summary dict."""
        symbols, stats = self.select_due(symbols_df, status_db, respect_lock)
        total = stats["due"]
        n_batches = (total + self.batch_size - 1) // self.batch_size if total else 0
        self.log.info(
            "Start — %d/%d symbols due (%d locked; %d abandoned; %d not yet due; "
            "%d skipped stale; %d batches of %d)",
            total, stats["candidates"], stats["locked"], stats["abandoned"],
            stats["not_due"], stats["stale"], n_batches, self.batch_size,
        )

        # A call can end three ways: returned rows (data), returned empty/None
        # (a successful check that found no data, e.g. a delisted ticker 404), or
        # raised after retries (failed). Counting "no-data" separately keeps the
        # summary honest — otherwise empties masquerade as successes. A no-data
        # result that pushes the pair to settings.MAX_NO_DATA_FETCHES abandons it
        # (skipped on all future normal runs); we report how many crossed that line.
        data = empty = failed = abandoned = 0
        cancelled = False
        out_db = Database(getattr(settings, self.target_db))
        try:
            for bi in range(n_batches):
                # Stop at the batch boundary (fetch_status is written per batch, so
                # progress so far is persisted and the run resumes cleanly later).
                if cancel.is_cancelled():
                    cancelled = True
                    self.log.warning(
                        "Cancelled — stopping after %d/%d batches", bi, n_batches
                    )
                    break
                chunk = symbols[bi * self.batch_size : (bi + 1) * self.batch_size]
                frames: list[pd.DataFrame] = []
                b_data = b_empty = b_fail = b_abandoned = 0
                for sym in chunk:
                    try:
                        rows = self._call(sym)
                        if rows is not None and not rows.empty:
                            frames.append(rows)
                            b_data += 1
                            # Generic extension point: a fetcher can stage a
                            # {"actual", "expected"} dict on self._pending_flag
                            # during fetch_one (e.g. yfinance_ohlcv's coverage
                            # check) to have it persisted with THIS symbol's
                            # last_fetched timestamp — see fetch_status.mark_success.
                            coverage = getattr(self, "_pending_flag", None)
                            fetch_status.mark_success(status_db, sym, self.name, coverage=coverage)
                        else:  # checked OK, but the symbol has no data
                            b_empty += 1
                            count = fetch_status.mark_no_data(status_db, sym, self.name)
                            if count >= settings.MAX_NO_DATA_FETCHES:
                                b_abandoned += 1
                    except Exception as exc:  # final failure after retries
                        fetch_status.mark_error(status_db, sym, self.name)
                        b_fail += 1
                        self.log.debug("‖ %s failed: %s", sym, exc)

                if frames:
                    self._write(out_db, pd.concat(frames, ignore_index=True))

                data += b_data
                empty += b_empty
                failed += b_fail
                abandoned += b_abandoned
                remaining = total - (bi + 1) * self.batch_size
                self.log.info(
                    "Batch %d/%d — Attempted: %d | Data: %d | No-data: %d | Failed: %d | Remaining: %d",
                    bi + 1, n_batches, len(chunk), b_data, b_empty, b_fail, max(remaining, 0),
                )
        finally:
            out_db.close()

        self.log.info(
            "Done — Data: %d | No-data: %d | Failed: %d | Abandoned: %d",
            data, empty, failed, abandoned,
        )
        return {"fetcher": self.name, "due": total, "data": data,
                "no_data": empty, "failed": failed, "abandoned": abandoned,
                "cancelled": cancelled}
