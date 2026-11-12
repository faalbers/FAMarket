"""
One-time symbol-state fix for the data-system fixes (fix list Group D, 2026-11-12).

Every step below now also runs on each fetch; this brings the stored data in line
without waiting for the next run:

  1. **Company ids (§25)** — integer `cik` + `company_id` from the SEC ticker map
     (one SEC request), `symbols.refresh_company_ids`.
  2. **Types (§6)** — `symbols.resolve_types_from_quotes`: SEC-list "mutual funds"
     Yahoo calls EQUITY -> adr / stock; preferred-named stocks with no market cap ->
     preferred; then `financials` rows of every symbol outside stock / reit / adr are
     deleted (`purge_out_of_scope_financials`).
  3. **Delisted (§15)** — `symbols.mark_delisted`: price history stopped ->
     `is_delisted = 1`, out of the analysis.

A one-off backup of symbols.db + financials.db goes to
``backups/data_system_fixes/{stamp}_fix_symbol_state/`` first.

`--dry-run` runs the same code on throwaway copies of symbols.db and financials.db
in ``backups/data_system_fixes/_dry_run/`` (inside the project; deleted afterwards;
quotes.db / ohlcv.db are only read), so its counts are exactly what the real run
would do. analysis.db is NOT rebuilt — run an analysis afterwards.

Usage
-----
    python -m scripts.fix_symbol_state --dry-run
    python -m scripts.fix_symbol_state
"""

from __future__ import annotations

import argparse
import shutil
import sqlite3
from pathlib import Path

import pandas as pd

from config import settings
from core.backup import FIX_BACKUP_DIR, fix_backup
from core.database import Database
from core.logging_config import setup_logging
from core.net import configure_tls
from data_layer import symbols
from data_layer.fetchers.edgar_fetcher import EDGARFinancials


def _copy(src: Path, dest: Path) -> None:
    with sqlite3.connect(f"file:{src.resolve().as_posix()}?mode=ro", uri=True) as a, \
            sqlite3.connect(dest) as b:
        a.backup(b)


def _state() -> pd.DataFrame:
    with Database(settings.SYMBOLS_DB) as db:
        return db.read("symbols")


def _report(before: pd.DataFrame, after: pd.DataFrame, fin_before: int, fin_after: int) -> None:
    m = before.set_index("symbol")[["security_type"]].join(
        after.set_index("symbol")[["security_type", "cik", "company_id", "is_delisted"]],
        rsuffix="_new")
    moved = m[m["security_type"].fillna("") != m["security_type_new"].fillna("")]
    print("\n== types changed:", len(moved))
    if len(moved):
        print(moved.groupby(["security_type", "security_type_new"]).size().to_string())
    live = after[(after["is_active"] == 1) & (after["is_validated"] == 1)
                 & after["security_type"].isin(["stock", "adr"])]
    print(f"\n== CIK on live stocks/ADRs: {int(before.loc[before['symbol'].isin(live['symbol']), 'cik'].notna().sum())}"
          f" -> {int(live['cik'].notna().sum())} of {len(live)}")
    print(f"== financials rows: {fin_before:,} -> {fin_after:,} ({fin_before - fin_after:,} removed)")
    dead = after[(after["is_delisted"] == 1) & (after["is_active"] == 1) & (after["is_validated"] == 1)]
    print(f"\n== delisted (active + validated, so leaving the analysis): {len(dead)}")
    print(dead["security_type"].value_counts().to_string())


def _fin_rows() -> int:
    with Database(settings.FINANCIALS_DB) as f:
        return int(f.query("SELECT count(*) AS n FROM financials")["n"].iloc[0])


def main() -> None:
    p = argparse.ArgumentParser(description="One-time symbol-state fix (fix list Group D)")
    p.add_argument("--dry-run", action="store_true", help="run on temp copies; change nothing")
    args = p.parse_args()
    settings.ensure_runtime_dirs()
    configure_tls()
    setup_logging()

    tmp: Path | None = None
    if args.dry_run:
        tmp = FIX_BACKUP_DIR / "_dry_run"
        shutil.rmtree(tmp, ignore_errors=True)
        tmp.mkdir(parents=True)
        for attr in ("SYMBOLS_DB", "FINANCIALS_DB"):
            src = Path(getattr(settings, attr))
            dest = tmp / src.name
            _copy(src, dest)
            setattr(settings, attr, dest)
        print(f"DRY RUN on copies in {tmp}")
    else:
        print(f"Backup: {fix_backup([settings.SYMBOLS_DB, settings.FINANCIALS_DB], 'fix_symbol_state')}")

    before, fin_before = _state(), _fin_rows()
    with Database(settings.SYMBOLS_DB) as sdb:
        symbols.refresh_company_ids(sdb, EDGARFinancials()._cik_map())   # 1 SEC request
    symbols.resolve_types_from_quotes()
    with Database(settings.SYMBOLS_DB) as sdb:
        symbols.mark_delisted(sdb)
    _report(before, _state(), fin_before, _fin_rows())

    if tmp is not None:
        shutil.rmtree(tmp, ignore_errors=True)
    else:
        print("\nanalysis.db still holds the OLD universe — run an analysis to bring it in line.")


if __name__ == "__main__":
    main()
