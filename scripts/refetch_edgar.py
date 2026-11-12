"""
One-time EDGAR re-fetch for the data-system fixes (fix list Group B, 2026-11-11).

EDGAR was fetched once per company (lock 36500 days until 2026-11-11; it now
refreshes only for new filings — staleness.edgar_not_due), so the
Group B extraction changes only reach companies whose facts are fetched again:

  * fiscal years never from quarterly reports, in fiscal-year-end months (§4, §12)
  * `Revenues` tried before the contract-revenue sub-line, plus the seam test (§13)
  * new columns: diluted/basic average shares, non-interest expense, filed date,
    quarter-sum flag (§14, §22, §23, §4)
  * first-filed values for per-share and share-count columns
  * capex always negative, no facts dated after their filing (§4)

Steps, in order:
  1. One-off backup of financials.db + symbols.db into
     ``backups/data_system_fixes/{stamp}_refetch_edgar/`` (outside the rotation).
  2. One-time delete of Yahoo leftover periods — no revenue, net income AND total
     assets (§1). The Yahoo import now skips these; the stored ones are removed so
     EDGAR can fill those periods.
  3. EDGAR pass over the fetch universe, ignoring the lock and the refresh gate. Each fetched
     company's old EDGAR rows are replaced, so rows the old rules produced vanish.

Repair run (2026-11-11, after the full pass): ``--subset`` re-runs the companies
that lost years under the first version of the rules, and ``--restore`` puts back,
from a fix backup, the EDGAR rows of companies whose ticker the SEC now points at
a different or brand-new company record (SUPN -> Indivior; NAVI, JSM, XPRO, CLBK ->
new holding-company records with almost no history). Restored companies are left
out of the fetch.

Registers in run_state like a normal fetch: Fetch Control shows it and its Stop
button works. A stopped run continues with ``--resume`` (skips companies fetched
in the last 2 days). analysis.db is NOT rebuilt — run an analysis afterwards.

Usage
-----
    python -m scripts.refetch_edgar --dry-run                 # counts only, no network
    python -m scripts.refetch_edgar --subset AAPL,MSFT,AMZN   # test on a few
    python -m scripts.refetch_edgar                           # full pass
    python -m scripts.refetch_edgar --resume                  # continue after a Stop
    python -m scripts.refetch_edgar --subset A,B --restore C,D --from backups/data_system_fixes/<stamp>_refetch_edgar
"""

from __future__ import annotations

import argparse
import time
from datetime import date
from pathlib import Path

from config import settings
from core.backup import fix_backup
from core.database import Database
from core.logging_config import get_logger, roll_log, setup_logging
from core.net import configure_tls
from core.shutdown_guard import ShutdownGuard
from data_layer import cancel, fetch_status, run_state
from data_layer.cleaning_rules import CORE_FIELDS
from data_layer.fetchers.edgar_fetcher import EDGARFinancials
from data_layer.orchestrator import load_fetch_universe

# --resume: companies fetched this recently count as done.
_RESUME_LOCK_DAYS = 2


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="One-time EDGAR re-fetch (fix list Group B)")
    p.add_argument("--subset", help="comma-separated symbols only")
    p.add_argument("--dry-run", action="store_true",
                   help="report counts only: no backup, no deletes, no network")
    p.add_argument("--resume", action="store_true",
                   help=f"skip companies EDGAR-fetched in the last {_RESUME_LOCK_DAYS} days")
    p.add_argument("--restore", help="comma-separated symbols: put their EDGAR rows back "
                   "from the --from backup, and leave them out of the fetch")
    p.add_argument("--from", dest="restore_from",
                   help="fix backup folder holding the financials.db to restore from")
    args = p.parse_args()
    if bool(args.restore) != bool(args.restore_from):
        p.error("--restore and --from go together")
    return args


def _fix_flag_type(fdb: Database) -> bool:
    """`quarter_sum_flag` was first created as a TEXT column ('0'/'1'); rebuild it
    as INTEGER. Idempotent: does nothing once the column is numeric."""
    info = fdb.query("PRAGMA table_info(financials)")
    row = info[info["name"] == "quarter_sum_flag"]
    if row.empty or row["type"].iloc[0] != "TEXT":
        return False
    for sql in (
        "ALTER TABLE financials ADD COLUMN quarter_sum_flag_int INTEGER",
        "UPDATE financials SET quarter_sum_flag_int = CAST(quarter_sum_flag AS INTEGER) "
        "WHERE quarter_sum_flag IS NOT NULL",
        "ALTER TABLE financials DROP COLUMN quarter_sum_flag",
        "ALTER TABLE financials RENAME COLUMN quarter_sum_flag_int TO quarter_sum_flag",
    ):
        fdb.execute(sql)
    return True


def _restore(symbols: list[str], backup_dir: str) -> int:
    """Put a company's EDGAR rows back from a fix backup.

    For a ticker the SEC re-pointed at another company record: its old EDGAR rows
    are restored as they were (with the two Group B import rules that need no SEC
    data re-applied: no future dates, capex negative), and the EDGAR-only columns
    the wrong record wrote onto its Yahoo rows are cleared.
    """
    src = Path(backup_dir) / Path(settings.FINANCIALS_DB).name
    if not src.is_file():
        raise FileNotFoundError(src)
    marks = ", ".join("?" for _ in symbols)
    with Database(src) as bdb:
        old = bdb.query(f"SELECT * FROM financials WHERE source = 'edgar' AND symbol IN ({marks})", symbols)
    old = old[old["period_end"] <= date.today().isoformat()].dropna(axis=1, how="all")
    if "capital_expenditure" in old.columns:
        old["capital_expenditure"] = -old["capital_expenditure"].abs()
    with Database(settings.FINANCIALS_DB) as fdb:
        fdb.execute(f"DELETE FROM financials WHERE source = 'edgar' AND symbol IN ({marks})", symbols)
        fdb.execute(
            "UPDATE financials SET filed_date = NULL, non_interest_expense = NULL "
            f"WHERE (source IS NULL OR source != 'edgar') AND symbol IN ({marks})", symbols)
        if not old.empty:
            fdb.upsert("financials", old, key=["symbol", "period_end", "freq"])
    return len(old)


def _leftover_where(subset: list[str] | None) -> tuple[str, list[str]]:
    """SQL WHERE for Yahoo periods with no core field (§1), optionally per subset."""
    where = (
        "(source IS NULL OR source != 'edgar') AND "
        + " AND ".join(f"{c} IS NULL" for c in CORE_FIELDS)
    )
    params: list[str] = []
    if subset:
        where += f" AND symbol IN ({', '.join('?' for _ in subset)})"
        params = list(subset)
    return where, params


def main() -> None:
    args = _parse_args()
    settings.ensure_runtime_dirs()
    subset = [s.strip().upper() for s in args.subset.split(",")] if args.subset else None
    restore = [s.strip().upper() for s in args.restore.split(",")] if args.restore else []
    # The staleness / no-data gates would hold back exactly the old, quiet
    # companies whose history matters most here.
    settings.FETCH_ABANDONMENT_ENABLED = False

    fetcher = EDGARFinancials()
    if args.resume:
        fetcher.lock_days = _RESUME_LOCK_DAYS
    respect_lock = args.resume

    where, params = _leftover_where(subset)
    with Database(settings.FINANCIALS_DB) as fdb:
        leftovers = int(fdb.query(f"SELECT count(*) AS n FROM financials WHERE {where}", params)["n"].iloc[0])
    with Database(settings.SYMBOLS_DB) as sdb:
        fetch_status.ensure_table(sdb)
        universe = load_fetch_universe(sdb, subset)
        if restore:
            universe = universe[~universe["symbol"].isin(restore)]
        due, stats = fetcher.select_due(universe, sdb, respect_lock)

    print(f"Yahoo leftover periods to delete: {leftovers:,}")
    print(f"Fetch universe: {len(universe):,}  ->  EDGAR due: {len(due):,}  "
          + " | ".join(f"{k}={v:,}" for k, v in stats.items()))
    if args.dry_run:
        return

    configure_tls()
    roll_log()
    setup_logging()
    cancel.clear()
    log = get_logger("refetch_edgar")
    label = f"EDGAR re-fetch ({'subset' if subset else 'resume' if args.resume else 'full universe'})"
    run_state.mark_running(label, "fetch")
    guard = ShutdownGuard(
        f"FAMarket {label} is running. Shutting down or signing out now will interrupt it."
    )
    guard.start()
    started = time.monotonic()
    try:
        if not args.resume:
            snap = fix_backup([settings.FINANCIALS_DB, settings.SYMBOLS_DB], "refetch_edgar")
            print(f"Backup: {snap}")
            with Database(settings.FINANCIALS_DB) as fdb:
                fdb.execute(f"DELETE FROM financials WHERE {where}", params)
                if _fix_flag_type(fdb):
                    log.info("quarter_sum_flag column rebuilt as INTEGER")
            log.info("Deleted %d Yahoo leftover periods (no revenue, net income or assets)", leftovers)
            if restore:
                n = _restore(restore, args.restore_from)
                log.info("Restored %d EDGAR rows from %s for %s", n, args.restore_from, ", ".join(restore))

        with Database(settings.SYMBOLS_DB) as sdb:
            summary = fetcher.run(universe, sdb, respect_lock=respect_lock)
    except Exception as exc:
        log.exception("EDGAR re-fetch failed — %s: %s", type(exc).__name__, exc)
        run_state.mark_error(f"{type(exc).__name__}: {exc}")
        raise
    else:
        summary["elapsed_minutes"] = round((time.monotonic() - started) / 60, 1)
        if summary.get("cancelled"):
            run_state.mark_cancelled(summary)
        else:
            run_state.mark_done(summary)
        print("\n=== EDGAR RE-FETCH SUMMARY ===")
        for k, v in summary.items():
            print(f"  {k:16} {v}")
        if summary.get("cancelled"):
            print("\n  Stopped early. Continue with: python -m scripts.refetch_edgar --resume")
        print("\n  analysis.db still holds the OLD numbers — run an analysis to bring it in line.")
    finally:
        guard.stop()


if __name__ == "__main__":
    main()
