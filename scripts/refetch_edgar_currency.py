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

from config import settings
from core.database import Database
from data_layer.fetchers.edgar_fetcher import EDGARFinancials
from scripts._edgar_refetch import refetch


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
    # History guard off for the write: here a "lost" year is an early year the
    # company tagged only as a USD convenience translation (MOMO, NOAH, VIPS, VSA;
    # dry run 2026-11-22), and keeping the old rows would keep the mixed currencies.
    refetch(_targets(), "refetch_edgar_currency", args.dry_run, guard_history=False)


if __name__ == "__main__":
    main()
