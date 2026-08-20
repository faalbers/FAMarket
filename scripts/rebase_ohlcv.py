"""
OHLCV maintenance tool: force a price-history refetch, and report coverage health.

Not part of any run. Normal fetches already keep the price store correct — OHLCV
writes with `replace_by` over a fixed settings.OHLCV_HISTORY_YEARS window, so
every fetch rewrites a symbol's whole series onto the CURRENT split/dividend
adjustment basis and no row can strand on an old one. This script exists for the
two cases that sit outside that: forcing a rewrite when the normal gates would
skip everything, and answering "which symbols does the price store have a problem
with, and is any of it my fault?".

It ran once as a one-off repair (2026-08-15..20, after the write mode moved from a
sliding upsert window to replace_by, leaving ~912k rows on a stale basis). That
repair is DONE. Kept because both modes stay useful — see "When to reach for this".

It runs the OHLCV fetch and NOTHING else — no symbol discovery, no quotes,
no financials, no signals, no reassessment, no analysis rebuild.

When to reach for this
----------------------
  * settings.OHLCV_HISTORY_YEARS changed. Widening the window doesn't backfill on
    its own: the fetch cadence lock means most symbols aren't due, so nothing
    refetches for days. A full pass here rewrites everything at the new depth.
  * A write-path bug is suspected of stranding rows again. Same reasoning as the
    original repair — a forced full pass is the clean-slate answer.
  * Coverage health, any time. `--retry-flagged` prints the standing flag
    breakdown before it fetches anything, and nothing else in the app surfaces it
    (no API route, no UI page). Cheap: it only refetches what's worth refetching.

A normal weekly run is the right tool for everything else.

Gates, set for a true "refetch everything" pass:
  * respect_lock=False              — ignores the fetch cadence lock
  * FETCH_ABANDONMENT_ENABLED=False — ignores the staleness / no-data gates

Both are needed. They are independent switches, and with either one left on most
of the universe is skipped (a normal run outside the weekly window has 0 due).

Usage
-----
    python -m scripts.rebase_ohlcv --dry-run
    python -m scripts.rebase_ohlcv --subset AAPL,MSFT,MLI
    python -m scripts.rebase_ohlcv
    python -m scripts.rebase_ohlcv --retry-flagged   # coverage health + mop-up

Reading --retry-flagged
-----------------------
Yahoo sometimes returns a stub of a symbol's history instead of the whole series.
Those fetches trip the coverage guard (yfinance_fetcher._check_coverage) and fall
back to upsert, so the symbol keeps its data but stays on the OLD adjustment
basis. This mode re-runs those — minutes rather than hours.

It only retries flags a refetch could actually change, and reports the rest by
reason (fetch_status.COVERAGE_*):
  * source_reset — Yahoo moved firstTradeDate forward past the history we hold,
    so those years are gone upstream. Retrying returns the same stub every time
    (verified 2026-08-19: identical response for start=1996 / start=2024 /
    period=max / period=1y).
  * thin — too few stored bars for the ratio to mean anything. Money-market
    funds, rights and warrants: Yahoo serves one bar per fetch, so "1 returned
    vs 12 stored" is normal, not a fault.

Only the `truncated` count is a backlog. The other two are a standing description
of what Yahoo will and won't serve, so they persist by design — a number that
stops falling is the expected end state, not a stuck job. Baseline after the
2026-08-20 repair: 997 source_reset, 132 thin, 4 truncated (all four mutual
funds). A jump in `truncated`, or `source_reset` climbing among STOCKS, is worth
a look; the standing counts drifting slowly are not.

Logging matches a normal run: the previous log is archived and a fresh
logs/famarket.log is written, and the run registers in run_state so the UI's
Fetch Control shows it (its Stop button works — the run unwinds at the next
batch boundary and what was fetched stays written).
"""

from __future__ import annotations

import argparse
import time

from config import settings
from core.backup import backup_all
from core.database import Database
from core.logging_config import get_logger, roll_log, setup_logging
from core.net import configure_tls
from core.shutdown_guard import ShutdownGuard
from data_layer import cancel, fetch_status, run_state, symbols
from data_layer.fetchers.yfinance_fetcher import YFinanceOHLCV
from data_layer.orchestrator import load_ohlcv_universe


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Force an OHLCV refetch onto the current adjustment basis, "
                    "and report coverage health"
    )
    p.add_argument("--subset", help="comma-separated symbols to rebase only")
    p.add_argument(
        "--retry-flagged", action="store_true",
        help="print the standing coverage-flag breakdown, then rebase only the "
             "symbols a refetch could actually help (see 'Reading --retry-flagged' "
             "in the module docstring). Cannot be combined with --subset.",
    )
    p.add_argument(
        "--dry-run", action="store_true",
        help="report how many symbols the gates let through, then exit (no network)",
    )
    p.add_argument(
        "--backup", action="store_true",
        help="take the standard pre-run backup of every .db first (backups/ is "
             "already large and this copies ohlcv.db, so it is opt-in here)",
    )
    p.add_argument(
        "--keep-gates", action="store_true",
        help="leave FETCH_ABANDONMENT_ENABLED as configured instead of forcing it "
             "off; stale/abandoned symbols then keep their old adjustment basis",
    )
    args = p.parse_args()
    if args.retry_flagged and args.subset:
        p.error("--retry-flagged and --subset are mutually exclusive")
    return args


# Why each non-retryable reason is skipped, in the words the operator needs at
# the prompt. Keyed by the fetch_status.COVERAGE_* value.
_SKIP_NOTES = {
    fetch_status.COVERAGE_SOURCE_RESET:
        "Yahoo reset their record, so the older history is gone upstream",
    fetch_status.COVERAGE_THIN:
        "too few stored bars to judge; Yahoo serves one bar per fetch for these",
}


def _flagged_symbols() -> tuple[list[str], dict[str, int]]:
    """(symbols worth retrying, {reason: count} for the ones that aren't).

    A flag counts only while it is the symbol's most recent fetch
    (`coverage_checked_at == last_fetched`), so one clean fetch drops a symbol
    off the list. Of those still flagged, only the ones a refetch could change
    are returned — see fetch_status.retryable_coverage_flags.
    """
    with Database(settings.SYMBOLS_DB) as sdb:
        fetch_status.ensure_table(sdb)
        return fetch_status.retryable_coverage_flags(sdb, YFinanceOHLCV.name)


def _dry_run(subset: list[str] | None) -> None:
    """Network-free: show what each gate would admit, with and without the lock."""
    fetcher = YFinanceOHLCV()
    with Database(settings.SYMBOLS_DB) as sdb:
        fetch_status.ensure_table(sdb)
        universe = load_ohlcv_universe(sdb, subset)
        print(f"OHLCV universe (+benchmarks): {len(universe):,}")
        print(f"FETCH_ABANDONMENT_ENABLED   : {settings.FETCH_ABANDONMENT_ENABLED}")
        for label, respect_lock in (("lock ON ", True), ("lock OFF", False)):
            _, stats = fetcher.select_due(universe, sdb, respect_lock)
            print(f"  {label} -> " + " | ".join(f"{k}={v:,}" for k, v in stats.items()))


def main() -> None:
    args = _parse_args()
    settings.ensure_runtime_dirs()

    if args.retry_flagged:
        subset, skipped = _flagged_symbols()
        print(f"--retry-flagged: {len(subset):,} symbol(s) worth refetching")
        for reason, note in _SKIP_NOTES.items():
            if skipped.get(reason):
                print(f"                 {skipped[reason]:,} skipped ({reason}) — {note}")
        if not subset:
            print("Nothing left to retry — every retryable flag has since fetched clean.")
            return
    else:
        subset = (
            [s.strip().upper() for s in args.subset.split(",")] if args.subset else None
        )

    # Viability gates off BEFORE anything reads them: staleness and the no-data
    # abandonment counter would otherwise hold back exactly the symbols whose
    # stored history is most likely to be on a stale basis.
    if not args.keep_gates:
        settings.FETCH_ABANDONMENT_ENABLED = False

    if args.dry_run:
        _dry_run(subset)
        return

    # Same startup sequence a real run uses (orchestrator.run_full_fetch).
    configure_tls()          # TLS interception on this machine — before any request
    roll_log()               # archive the previous run's log, start a fresh one
    setup_logging()
    cancel.clear()           # drop a Stop left over from an earlier run

    log = get_logger("rebase_ohlcv")
    if args.retry_flagged:
        label = f"OHLCV rebase (retry {len(subset or [])} flagged)"
    else:
        label = f"OHLCV rebase ({'subset' if subset else 'full universe'})"
    run_state.mark_running(label, "fetch")

    guard = ShutdownGuard(
        f"FAMarket {label} is running. "
        "Shutting down or signing out now will interrupt it."
    )
    guard.start()
    started = time.monotonic()
    try:
        if args.backup:
            log.info("Pre-run backup — copying databases")
            backup_all()

        log.info(
            "OHLCV rebase — %d-year window, full per-symbol replace, "
            "respect_lock=False, abandonment=%s",
            settings.OHLCV_HISTORY_YEARS, settings.FETCH_ABANDONMENT_ENABLED,
        )

        with Database(settings.SYMBOLS_DB) as sdb:
            fetch_status.ensure_table(sdb)
            symbols.ensure_benchmark_symbols(sdb)
            universe = load_ohlcv_universe(sdb, subset)
            log.info("Universe — %d active symbols", len(universe))
            summary = YFinanceOHLCV().run(universe, sdb, respect_lock=False)

    except Exception as exc:
        log.exception("OHLCV rebase failed — %s: %s", type(exc).__name__, exc)
        run_state.mark_error(f"{type(exc).__name__}: {exc}")
        raise
    else:
        elapsed = time.monotonic() - started
        summary["elapsed_minutes"] = round(elapsed / 60, 1)
        if summary.get("cancelled"):
            run_state.mark_cancelled(summary)
        else:
            run_state.mark_done(summary)

        print("\n=== OHLCV REBASE SUMMARY ===")
        for k, v in summary.items():
            print(f"  {k:16} {v}")
        if summary.get("cancelled"):
            print("\n  Stopped early. Rerun the same command to continue —")
            print("  symbols already rebased are simply rewritten again.")
        else:
            still, skipped = _flagged_symbols()
            if still:
                print(f"\n  {len(still):,} symbol(s) still on the old basis (Yahoo served")
                print("  a stub, so their history was kept rather than replaced).")
                print("  Rerun with --retry-flagged in a day or so — this kind is transient.")
            for reason, note in _SKIP_NOTES.items():
                if skipped.get(reason):
                    print(f"\n  {skipped[reason]:,} symbol(s) flagged '{reason}' — {note}")
            if not still and not skipped:
                print("\n  Every symbol is on the current adjustment basis.")
            print("\n  analysis.db still holds the OLD numbers —")
            print("  run a normal fetch/analysis to bring it in line.")
    finally:
        guard.stop()


if __name__ == "__main__":
    main()
