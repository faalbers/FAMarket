"""
One-time recovery of symbols the fetch gates gave up on (fix list §15, 2026-11-12).

Before 2026-11-08 the 4-week price-staleness rule was measured against TODAY, so a
long pause between runs (a 5-week vacation) made healthy symbols look stale, and
"stale" or repeated "no data" meant never fetched again. Yahoo outages (150 symbols
missing 2026-10-02..11-06) do the same. The monthly re-check now prevents new
losses; this re-checks everything stuck so far, once:

  * switched off (`is_active = 0`)          — switched on, quote + prices fetched
  * prices given up (stale / no-data)       — prices fetched

All fetches go through the real fetchers (rate-limited, abandonment gates off).
Then the end-of-run reassessment switches the still-dead ones off again (their
`deactivated_date` starts the monthly re-check window) and `mark_delisted` updates
the delisted marks. A one-off backup of symbols.db + quotes.db is taken first;
ohlcv.db is not copied (13 GB) — a fetch only replaces a symbol's rows when Yahoo
returns its history, and the coverage guard keeps stored rows if the answer is short.

Registers in run_state like a normal fetch (Fetch Control shows it, Stop works).
Revived stocks get their financials on the next weekly run. analysis.db is not
rebuilt — run an analysis afterwards.

Usage
-----
    python -m scripts.recover_stuck_symbols --dry-run    # counts only, no network
    python -m scripts.recover_stuck_symbols
"""

from __future__ import annotations

import argparse
import time

import pandas as pd

from config import settings
from core.backup import fix_backup
from core.database import Database
from core.logging_config import get_logger, roll_log, setup_logging
from core.net import configure_tls
from core.shutdown_guard import ShutdownGuard
from data_layer import cancel, fetch_status, run_state, symbols
from data_layer.fetchers.yfinance_fetcher import YFinanceOHLCV, YFinanceQuotes
from data_layer.orchestrator import load_fetch_universe, load_ohlcv_universe


def _stuck(sdb: Database) -> tuple[list[str], list[str]]:
    """(switched-off symbols, price-given-up symbols) — read-only."""
    off = sdb.query(
        f"SELECT symbol FROM {symbols.TABLE} WHERE is_active = 0 "
        "AND (security_type IS NULL OR security_type != 'index')")["symbol"].tolist()
    ohlcv = YFinanceOHLCV()
    cand = ohlcv.select_symbols(load_ohlcv_universe(sdb))
    _, _, abandoned = fetch_status.classify_skips(sdb, cand, ohlcv.name, ohlcv.lock_days, False)
    stale = ohlcv.stale_symbols(cand, sdb)
    return off, sorted(set(abandoned) | stale)


def _live_counts(sdb: Database, syms: list[str]) -> pd.Series:
    df = sdb.query(f"SELECT symbol, security_type, is_active, is_delisted FROM {symbols.TABLE}")
    df = df[df["symbol"].isin(set(syms))]
    back = df[(df["is_active"] == 1) & (df["is_delisted"].fillna(0) != 1)]
    return back["security_type"].fillna("(none)").value_counts()


def main() -> None:
    p = argparse.ArgumentParser(description="One-time recovery of given-up symbols")
    p.add_argument("--dry-run", action="store_true", help="count only; no writes, no network")
    args = p.parse_args()
    settings.ensure_runtime_dirs()

    with Database(settings.SYMBOLS_DB) as sdb:
        fetch_status.ensure_table(sdb)
        off, given_up = _stuck(sdb)
    stuck = sorted(set(off) | set(given_up))
    print(f"Switched off: {len(off):,} | prices given up: {len(given_up):,} | total: {len(stuck):,}")
    print(f"Estimated requests: ~{len(off) + len(stuck):,} "
          f"(~{(len(off) + len(stuck)) / settings.RATE_LIMITS['yfinance'][0]:.0f} min at the rate limit)")
    if args.dry_run:
        return

    configure_tls()
    roll_log()
    setup_logging()
    cancel.clear()
    log = get_logger("recover_stuck_symbols")
    run_state.mark_running("Recovery of given-up symbols", "fetch")
    guard = ShutdownGuard("FAMarket symbol recovery is running. Shutting down now will interrupt it.")
    guard.start()
    started = time.monotonic()
    summary: dict = {"switched_off": len(off), "prices_given_up": len(given_up)}
    try:
        print(f"Backup: {fix_backup([settings.SYMBOLS_DB, settings.QUOTES_DB], 'recover_stuck_symbols')}")
        settings.FETCH_ABANDONMENT_ENABLED = False
        with Database(settings.SYMBOLS_DB) as sdb:
            # Switch everything stuck on and clear its no-data strikes for this pass.
            sdb.conn.executemany(f"UPDATE {symbols.TABLE} SET is_active = 1 WHERE symbol = ?",
                                 [(s,) for s in off])
            sdb.conn.executemany(f"UPDATE {fetch_status.TABLE} SET no_data_count = 0 WHERE symbol = ?",
                                 [(s,) for s in stuck])
            sdb.conn.commit()
            if off:
                summary["quotes"] = YFinanceQuotes().run(load_fetch_universe(sdb, off), sdb, False)
                symbols.resolve_types_from_quotes()
            if not cancel.is_cancelled():
                summary["ohlcv"] = YFinanceOHLCV().run(load_ohlcv_universe(sdb, stuck), sdb, False)
            stopped = cancel.is_cancelled()
            summary["reassessment"] = symbols.reassess_state(
                sdb, assess_symbols=stuck, allow_deactivate=not stopped)
            summary["delisted"] = symbols.mark_delisted(sdb)
            back = _live_counts(sdb, stuck)
        summary["back_live"] = int(back.sum())
        summary["elapsed_minutes"] = round((time.monotonic() - started) / 60, 1)
        log.info("Recovery — %d of %d stuck symbols are live again: %s",
                 int(back.sum()), len(stuck), back.to_dict())
    except Exception as exc:
        log.exception("Recovery failed — %s: %s", type(exc).__name__, exc)
        run_state.mark_error(f"{type(exc).__name__}: {exc}")
        raise
    else:
        (run_state.mark_cancelled if cancel.is_cancelled() else run_state.mark_done)(summary)
        print("\n=== RECOVERY SUMMARY ===")
        for k, v in summary.items():
            print(f"  {k:18} {v}")
        print("\nLive again by type:\n" + back.to_string())
        print("\n  analysis.db still holds the OLD universe — run an analysis to bring it in line.")
    finally:
        guard.stop()


if __name__ == "__main__":
    main()
