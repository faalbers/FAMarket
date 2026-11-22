"""
One-time re-fetch: re-read SEC data for companies that don't report in US dollars.

The SEC importer used to pick US dollars for each line item whenever a filing had
any dollar figures. Foreign filers add a dollar "convenience translation" of their
latest year, so their stored history mixed dollars with yen / yuan / euros, and
EPS reported only in the home currency was dropped. The importer now picks ONE
reporting currency per company (``edgar_fetcher._reporting_currency``) and drops a
company's SEC history when that currency differs from Yahoo's
``financialCurrency`` (``EDGARFinancials._drop_other_currency``). The stored rows
can't be converted in place — the home-currency figures were never saved — so
this script re-reads those companies through the normal importer write.

Targets: every stock / REIT / ADR whose Yahoo ``financialCurrency`` is not USD,
plus every company with stored SEC rows and no Yahoo currency. One SEC request
each (none for a symbol without a CIK). ``fetch_status`` is not touched, so the
normal refresh schedule is unchanged.

Before writing, it snapshots ``financials.db`` and ``symbols.db`` (the importer
also writes the rate-regulated flag there) to
``backups/data_system_fixes/{stamp}_refetch_edgar_currency/``. Close the VS Code
SQLite viewer first — it locks the file. Afterwards a full analysis run is needed.

Usage
-----
    python -m scripts.refetch_edgar_currency --dry-run   # fetch + report, no writes
    python -m scripts.refetch_edgar_currency
"""

from __future__ import annotations

import argparse
import time

import pandas as pd

from config import settings
from core.backup import fix_backup
from core.database import Database
from core.net import configure_tls
from data_layer.fetchers.edgar_fetcher import EDGARFinancials

BATCH = 25
PAUSE_S = 0.15  # stays well under SEC's ~10 requests a second


def _targets() -> list[str]:
    with Database(settings.QUOTES_DB) as qdb:
        ccy = qdb.query("SELECT symbol, financialCurrency FROM quotes")
    with Database(settings.SYMBOLS_DB) as sdb:
        types = sdb.query("SELECT symbol, security_type FROM symbols")
    with Database(settings.FINANCIALS_DB) as fdb:
        edgar = set(fdb.query("SELECT DISTINCT symbol FROM financials WHERE source = 'edgar'")["symbol"])
    ccy_by = {s: c for s, c in zip(ccy["symbol"], ccy["financialCurrency"]) if isinstance(c, str) and c}
    in_scope = set(types.loc[types["security_type"].isin(EDGARFinancials.applies_to), "symbol"])
    foreign = {s for s, c in ccy_by.items() if c != "USD" and s in in_scope}
    unknown = {s for s in edgar if s not in ccy_by}
    return sorted(foreign | unknown)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true", help="fetch and report; no backup, no writes")
    args = ap.parse_args()
    configure_tls()

    syms = _targets()
    print(f"Targets: {len(syms)} companies")
    if not args.dry_run:
        print(f"Backup: {fix_backup([settings.FINANCIALS_DB, settings.SYMBOLS_DB], 'refetch_edgar_currency')}")

    fetcher = EDGARFinancials()
    data = empty = failed = other_ccy = guarded = 0
    with Database(settings.FINANCIALS_DB) as db:
        for i in range(0, len(syms), BATCH):
            frames = []
            for sym in syms[i:i + BATCH]:
                if sym.upper() in fetcher._cik_map():
                    time.sleep(PAUSE_S)
                try:
                    rows = fetcher.fetch_one(sym)
                except Exception as exc:  # transient SEC error: leave the stored rows
                    failed += 1
                    print(f"  {sym}: failed ({type(exc).__name__})")
                    continue
                if rows is None or rows.empty:
                    empty += 1
                    continue
                data += 1
                frames.append(rows)
            if not frames:
                continue
            batch = pd.concat(frames, ignore_index=True)
            kept, dropped = fetcher._drop_other_currency(batch.drop(columns=["regulatory"]))
            other_ccy += len(dropped)
            guarded += kept["symbol"].nunique() - fetcher._keep_unreproduced_history(db, kept)["symbol"].nunique()
            if not args.dry_run:
                # History guard off for the write: here a "lost" year is an early
                # year the company tagged only as a USD convenience translation
                # (MOMO, NOAH, VIPS, VSA; dry run 2026-11-22), and keeping the old
                # rows would keep the mixed currencies. The count above still
                # reports any such company.
                fetcher.guard_history = False
                fetcher._write(db, batch)
                fetcher.guard_history = True
            print(f"  {min(i + BATCH, len(syms))}/{len(syms)}")

    verb = "would" if args.dry_run else "did"
    print(f"\nDone — SEC data for {data:,} companies ({verb} write), none for {empty:,}, "
          f"failed {failed:,}; dropped as another currency than Yahoo's: {other_ccy:,}; "
          f"lose early USD-only years (history guard off here): {guarded:,}.")


if __name__ == "__main__":
    main()
