"""
TEMPORARY one-off repair script — DELETE THIS FILE once the rebase is done.

Rewrites every symbol's price history in ohlcv.db onto a single, current
split/dividend adjustment basis, using the fixed OHLCV write mode (full
per-symbol replace over settings.OHLCV_HISTORY_YEARS).

It runs the OHLCV fetch and NOTHING else — no symbol discovery, no quotes,
no financials, no signals, no reassessment, no analysis rebuild.

Gates, set for a true "refetch everything" pass:
  * respect_lock=False              — ignores the fetch cadence lock
  * FETCH_ABANDONMENT_ENABLED=False — ignores the staleness / no-data gates

Both are needed. They are independent switches, and with either one left on most
of the universe is skipped (a normal run right now has 0 symbols due).

Usage
-----
    python -m scripts.rebase_ohlcv --dry-run
    python -m scripts.rebase_ohlcv --subset AAPL,MSFT,MLI
    python -m scripts.rebase_ohlcv
    python -m scripts.rebase_ohlcv --retry-flagged   # mop up after a full pass

Yahoo intermittently returns a stub of a symbol's history instead of the whole
series. Those fetches trip the coverage guard and fall back to upsert, so the
symbol keeps its data but stays on the OLD adjustment basis. `--retry-flagged`
re-runs exactly those, which is minutes rather than hours. The flag clears
itself on a clean fetch, so rerun it every day or so until it reports nothing
left — truncation is transient, not permanent.

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
from data_layer import cancel, fetch_status, run_state
from data_layer.fetchers.yfinance_fetcher import YFinanceOHLCV
from data_layer.orchestrator import load_fetch_universe


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="One-off OHLCV adjustment-basis rebase")
    p.add_argument("--subset", help="comma-separated symbols to rebase only")
    p.add_argument(
        "--retry-flagged", action="store_true",
        help="rebase only the symbols whose LAST fetch tripped the truncation "
             "guard — they kept their old adjustment basis and still need one. "
             "Cannot be combined with --subset.",
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


def _flagged_symbols() -> list[str]:
    """Symbols whose MOST RECENT OHLCV fetch tripped the coverage guard.

    Those fell back to upsert, so they kept their history but are still on the
    old split/dividend adjustment basis. `coverage_flags` marks a flag `active`
    only while `coverage_checked_at == last_fetched`, so a symbol drops off this
    list as soon as one clean fetch supersedes its flag — rerun until it empties.
    """
    with Database(settings.SYMBOLS_DB) as sdb:
        fetch_status.ensure_table(sdb)
        flags = fetch_status.coverage_flags(sdb, YFinanceOHLCV.name)
    return sorted(sym for (sym, _), info in flags.items() if info["active"])


def _dry_run(subset: list[str] | None) -> None:
    """Network-free: show what each gate would admit, with and without the lock."""
    fetcher = YFinanceOHLCV()
    with Database(settings.SYMBOLS_DB) as sdb:
        fetch_status.ensure_table(sdb)
        universe = load_fetch_universe(sdb, subset)
        print(f"universe (active, non-index): {len(universe):,}")
        print(f"FETCH_ABANDONMENT_ENABLED   : {settings.FETCH_ABANDONMENT_ENABLED}")
        for label, respect_lock in (("lock ON ", True), ("lock OFF", False)):
            _, stats = fetcher.select_due(universe, sdb, respect_lock)
            print(f"  {label} -> " + " | ".join(f"{k}={v:,}" for k, v in stats.items()))


def main() -> None:
    args = _parse_args()
    settings.ensure_runtime_dirs()

    if args.retry_flagged:
        subset = _flagged_symbols()
        print(f"--retry-flagged: {len(subset):,} symbol(s) still on the old basis")
        if not subset:
            print("Nothing left to retry — every flagged symbol has since fetched clean.")
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
            universe = load_fetch_universe(sdb, subset)
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
            still = len(_flagged_symbols())
            if still:
                print(f"\n  {still:,} symbol(s) still on the old basis (Yahoo served a")
                print("  stub, so their history was kept rather than replaced).")
                print("  Rerun with --retry-flagged in a day or so; truncation is transient.")
            else:
                print("\n  Every symbol is on the current adjustment basis.")
            print("\n  analysis.db still holds the OLD numbers —")
            print("  run a normal fetch/analysis to bring it in line.")
    finally:
        guard.stop()


if __name__ == "__main__":
    main()
