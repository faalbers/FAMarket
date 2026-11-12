"""
One-time price-history fix for the data-system fixes (fix list Group C, 2026-11-12).

The OHLCV fetch now (a) re-fetches a symbol with yfinance's price repair when its
`adj_close` comes back broken and (b) writes `price_flags` (data_layer/price_checks.py).
Both only reach a symbol on its next fetch. This brings the stored data in line now:

  1. **Flag every stored symbol** — streams ohlcv.db by symbol, runs
     `find_price_flags`, and rewrites the `price_flags` table. Price rows are not
     touched. Undo: drop the table.
  2. **Re-fetch the symbols flagged `bad_adj`** through the real `YFinanceOHLCV`
     (repair-when-broken, rate-limited, abandonment gates off). Their current rows
     are first exported to a small SQLite file in
     ``backups/data_system_fixes/{stamp}_fix_stored_prices/`` — the 13 GB ohlcv.db is
     not copied. The fetcher rewrites their flags itself.

Registers in run_state like a normal fetch (Fetch Control shows it, Stop works).
analysis.db is NOT rebuilt — run an analysis afterwards.

Usage
-----
    python -m scripts.fix_stored_prices --dry-run      # counts per check, no writes, no network
    python -m scripts.fix_stored_prices --flags-only   # step 1 only
    python -m scripts.fix_stored_prices                # steps 1 + 2
"""

from __future__ import annotations

import argparse
import sqlite3
import time

import pandas as pd

from config import settings
from core.backup import FIX_BACKUP_DIR, now_stamp
from core.database import Database
from core.logging_config import get_logger, roll_log, setup_logging
from core.net import configure_tls
from core.shutdown_guard import ShutdownGuard
from data_layer import cancel, fetch_status, price_checks, run_state
from data_layer.fetchers.yfinance_fetcher import YFinanceOHLCV
from data_layer.orchestrator import load_ohlcv_universe

_SYMBOLS_PER_READ = 500


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="One-time price-history fix (fix list Group C)")
    p.add_argument("--dry-run", action="store_true", help="count flags per check; no writes, no network")
    p.add_argument("--flags-only", action="store_true", help="flag stored symbols; no re-fetch")
    return p.parse_args()


def _flag_all(write: bool) -> pd.DataFrame:
    """Run the checks over every stored symbol; rewrite `price_flags` if `write`."""
    with Database(settings.OHLCV_DB) as db:
        syms = db.query("SELECT DISTINCT symbol FROM ohlcv")["symbol"].tolist()
        parts: list[pd.DataFrame] = []
        for i in range(0, len(syms), _SYMBOLS_PER_READ):
            if cancel.is_cancelled():
                raise KeyboardInterrupt("stop requested")
            chunk = syms[i:i + _SYMBOLS_PER_READ]
            rows = db.query(
                "SELECT symbol, date, close, adj_close, volume, dividends, splits FROM ohlcv "
                f"WHERE symbol IN ({', '.join('?' for _ in chunk)}) ORDER BY symbol, date", chunk)
            parts.append(price_checks.flags_for(rows))
        flags = pd.concat(parts, ignore_index=True) if parts else pd.DataFrame(columns=price_checks.FLAG_COLUMNS)
        if write:
            if db.table_exists(price_checks.TABLE):
                db.execute(f'DELETE FROM "{price_checks.TABLE}"')
            db.append(price_checks.TABLE, flags)
    return flags


def _export_rows(symbols: list[str]) -> str:
    """Copy these symbols' current ohlcv rows to a small backup SQLite file."""
    dest = FIX_BACKUP_DIR / f"{now_stamp()}_fix_stored_prices"
    dest.mkdir(parents=True, exist_ok=True)
    path = dest / "ohlcv_refetched_symbols.db"
    with Database(settings.OHLCV_DB) as db:
        rows = db.query(f"SELECT * FROM ohlcv WHERE symbol IN ({', '.join('?' for _ in symbols)})", symbols)
    with sqlite3.connect(path) as out:
        rows.to_sql("ohlcv", out, index=False)
    return f"{path} ({len(rows):,} rows)"


def main() -> None:
    args = _parse_args()
    settings.ensure_runtime_dirs()
    started = time.monotonic()

    if args.dry_run:
        flags = _flag_all(write=False)
        _print_counts(flags)
        return

    configure_tls()
    roll_log()
    setup_logging()
    cancel.clear()
    log = get_logger("fix_stored_prices")
    run_state.mark_running("Price-history fix (flags + repair)", "fetch")
    guard = ShutdownGuard("FAMarket price-history fix is running. Shutting down now will interrupt it.")
    guard.start()
    summary: dict = {}
    try:
        flags = _flag_all(write=True)
        log.info("Price flags — %d written for %d symbols", len(flags), flags["symbol"].nunique())
        _print_counts(flags)
        summary["flags"] = len(flags)
        broken = sorted(flags.loc[flags["check"] == "bad_adj", "symbol"].unique())
        summary["bad_adj_symbols"] = len(broken)
        if broken and not args.flags_only:
            print(f"\nBackup of {len(broken)} symbols' rows: {_export_rows(broken)}")
            settings.FETCH_ABANDONMENT_ENABLED = False
            with Database(settings.SYMBOLS_DB) as sdb:
                fetch_status.ensure_table(sdb)
                universe = load_ohlcv_universe(sdb, broken)
                result = YFinanceOHLCV().run(universe, sdb, respect_lock=False)
            summary.update({f"refetch_{k}": v for k, v in result.items()})
    except KeyboardInterrupt:
        run_state.mark_cancelled(summary)
        print("\nStopped.")
        return
    except Exception as exc:
        log.exception("Price-history fix failed — %s: %s", type(exc).__name__, exc)
        run_state.mark_error(f"{type(exc).__name__}: {exc}")
        raise
    else:
        summary["elapsed_minutes"] = round((time.monotonic() - started) / 60, 1)
        run_state.mark_done(summary)
        print("\n=== PRICE-HISTORY FIX SUMMARY ===")
        for k, v in summary.items():
            print(f"  {k:28} {v}")
        print("\n  analysis.db still holds the OLD numbers — run an analysis to bring it in line.")
    finally:
        guard.stop()


def _print_counts(flags: pd.DataFrame) -> None:
    if flags.empty:
        print("No price flags.")
        return
    counts = flags.groupby("check").agg(flags=("symbol", "size"), symbols=("symbol", "nunique"))
    print(counts.to_string())


if __name__ == "__main__":
    main()
