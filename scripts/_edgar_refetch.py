"""
Shared loop for the one-time SEC re-fetch scripts (`refetch_edgar_currency`,
`refetch_edgar_revenue`): re-read the given companies through the normal EDGAR
importer (`EDGARFinancials.fetch_one` + `_write`), after a fix backup of
`financials.db` and `symbols.db` (the importer also writes the rate-regulated
flag there). `fetch_status` is not touched, so the normal refresh schedule is
unchanged. Close the VS Code SQLite viewer first — it locks the file.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

import pandas as pd

from config import settings
from core.backup import fix_backup
from core.database import Database
from core.net import configure_tls
from data_layer.fetchers.edgar_fetcher import EDGARFinancials

BATCH = 25
PAUSE_S = 0.15  # stays well under SEC's ~10 requests a second


@dataclass
class Counts:
    data: int = 0       # companies with SEC data
    empty: int = 0      # no CIK / no us-gaap facts
    failed: int = 0     # request failed: stored rows left as they are
    other_ccy: int = 0  # SEC currency differs from Yahoo's: SEC rows dropped
    guarded: int = 0    # the new fetch would lose stored fiscal years


def refetch(syms: list[str], label: str, dry_run: bool, guard_history: bool = True) -> Counts:
    """Re-fetch `syms`; with `dry_run` only fetch and count. `guard_history=False`
    writes even companies whose new fetch loses stored years (counted either way)."""
    configure_tls()
    print(f"Targets: {len(syms)} companies")
    if not dry_run:
        print(f"Backup: {fix_backup([settings.FINANCIALS_DB, settings.SYMBOLS_DB], label)}")
    fetcher = EDGARFinancials()
    n = Counts()
    with Database(settings.FINANCIALS_DB) as db:
        for i in range(0, len(syms), BATCH):
            frames = []
            for sym in syms[i:i + BATCH]:
                if sym.upper() in fetcher._cik_map():
                    time.sleep(PAUSE_S)
                try:
                    rows = fetcher.fetch_one(sym)
                except Exception as exc:  # transient SEC error: leave the stored rows
                    n.failed += 1
                    print(f"  {sym}: failed ({type(exc).__name__})")
                    continue
                if rows is None or rows.empty:
                    n.empty += 1
                    continue
                n.data += 1
                frames.append(rows)
            if frames:
                batch = pd.concat(frames, ignore_index=True)
                kept, dropped = fetcher._drop_other_currency(batch.drop(columns=["regulatory"]))
                n.other_ccy += len(dropped)
                n.guarded += (kept["symbol"].nunique()
                              - fetcher._keep_unreproduced_history(db, kept)["symbol"].nunique())
                if not dry_run:
                    fetcher.guard_history = guard_history
                    fetcher._write(db, batch)
                    fetcher.guard_history = True
            print(f"  {min(i + BATCH, len(syms))}/{len(syms)}", flush=True)
    verb = "would write" if dry_run else "written"
    print(f"\nDone — SEC data for {n.data:,} companies ({verb}), none for {n.empty:,}, "
          f"failed {n.failed:,}; dropped as another currency than Yahoo's: {n.other_ccy:,}; "
          f"would lose stored years: {n.guarded:,}"
          f"{' (history guard off: written anyway)' if not guard_history else ' (kept as stored)'}.")
    return n
