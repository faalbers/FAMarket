"""
One-time repair: rescale stored EDGAR share counts tagged at the wrong power of 1,000.

The same rule runs on every new EDGAR fetch (``cleaning_rules.repair_share_scale``,
wired into ``edgar_fetcher.extract_financials``). EDGAR rows are refreshed only
when a company files, so this script brings the stored rows in line once. Safe to
rerun: a second run changes nothing.

Touches ``financials.db`` / ``financials`` only, and only these columns of EDGAR
rows: ``diluted_average_shares``, ``basic_average_shares``,
``ordinary_shares_number``, ``share_issued``. Yahoo rows are never changed.

Before writing, it snapshots ``financials.db`` to
``backups/data_system_fixes/{stamp}_fix_share_scale/`` (outside the normal backup
rotation). Only changed rows and columns are written back. Close the VS Code
SQLite viewer first — it locks the file.

Afterwards a full analysis run is needed for analysis.db to pick up the changes
(per-share growth, Peer Strength, growth labels).

Usage
-----
    python -m scripts.fix_share_scale --dry-run   # counts only, no backup, no writes
    python -m scripts.fix_share_scale
"""

from __future__ import annotations

import argparse

from config import settings
from core.backup import fix_backup
from data_layer.cleaning_rules import SHARE_SCALE_COLS, repair_share_scale
from scripts.clean_stored_data import Target, clean_target

KEY = ["symbol", "period_end", "freq"]
TARGET = Target(settings.FINANCIALS_DB, "financials", KEY,
                [c for c in SHARE_SCALE_COLS if c not in KEY], repair_share_scale)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true", help="report counts only; no backup, no writes")
    args = ap.parse_args()
    if not args.dry_run:
        print(f"Backup: {fix_backup([TARGET.db_path], 'fix_share_scale')}")
    n = clean_target(TARGET, args.dry_run)
    print(f"\nDone — {'would update' if args.dry_run else 'updated'} {n:,} rows.")


if __name__ == "__main__":
    main()
