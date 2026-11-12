"""
One-time cleanup: apply the Group A cleaning rules to rows already stored.

The same rules run on every new fetch (``data_layer/cleaning_rules.py``, wired into
the yfinance and EDGAR sanitize steps). New fetches only fix what they re-download,
and financials can be deferred for months, so this script brings the stored rows
in line once. Safe to rerun: a second run changes nothing.

Touches three databases, nothing else:
  * quotes.db     / quotes     — §5 recommendationMean 0, §7 beta / payout / gross
                                 margin out of range, §25.5 longName from shortName
  * signals.db    / estimates  — §5 placeholder 0s where no analyst covers the row
  * financials.db / financials — §24 stockholders_equity stored as 0

Before writing, it snapshots those three files to
``backups/data_system_fixes/{stamp}_clean_stored_data/`` (outside the normal backup
rotation; never deleted automatically). Only changed rows and only the rule columns
are written back. Close the VS Code SQLite viewer first — it locks the files.

Afterwards a full analysis run is needed for analysis.db to pick up the changes
(e.g. roe / pb / debt_to_equity for partnerships such as BEP, WES).

Usage
-----
    python -m scripts.clean_stored_data --dry-run   # counts only, no backup, no writes
    python -m scripts.clean_stored_data
"""

from __future__ import annotations

import argparse
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from config import settings
from core.backup import fix_backup
from core.database import Database
from data_layer.cleaning_rules import (
    Counts,
    clean_estimates,
    clean_financials,
    clean_quotes,
)


@dataclass(frozen=True)
class Target:
    db_path: Path
    table: str
    key: list[str]
    cols: list[str]                     # columns the rule reads or writes
    rule: Callable[[pd.DataFrame], tuple[pd.DataFrame, Counts]]


TARGETS = [
    Target(settings.QUOTES_DB, "quotes", ["symbol"],
           ["recommendationMean", "beta", "payoutRatio", "grossMargins", "longName", "shortName"],
           clean_quotes),
    Target(settings.SIGNALS_DB, "estimates", ["symbol", "horizon"],
           ["rev_num_analysts", "rev_avg", "rev_low", "rev_high", "rev_year_ago", "rev_growth",
            "eps_num_analysts", "eps_avg", "eps_low", "eps_high", "eps_year_ago", "eps_growth"],
           clean_estimates),
    Target(settings.FINANCIALS_DB, "financials", ["symbol", "period_end", "freq"],
           ["stockholders_equity", "common_stock_equity"],
           clean_financials),
]


def _changed(before: pd.Series, after: pd.Series) -> pd.Series:
    """Null-aware per-cell inequality."""
    both_null = before.isna() & after.isna()
    return ~(both_null | (before == after))


def clean_target(t: Target, dry_run: bool) -> int:
    """Apply one target's rule; return the number of rows written (or that would be)."""
    with Database(t.db_path) as db:
        existing = set(db.columns(t.table))
        cols = [c for c in t.cols if c in existing]
        sel = ", ".join(f'"{c}"' for c in t.key + cols)
        before = db.query(f'SELECT {sel} FROM "{t.table}"')
        after, counts = t.rule(before.copy())

        written_cols = [c for c in cols if c in after.columns]
        diff = pd.DataFrame({c: _changed(before[c], after[c]) for c in written_cols})
        row_changed = diff.any(axis=1)
        changed_cols = [c for c in written_cols if diff[c].any()]

        print(f"\n{t.db_path.name} / {t.table}: {len(before):,} rows read")
        for rule, n in counts.items():
            print(f"  {rule:<36} {n:>8,} cells")
        print(f"  rows to update: {int(row_changed.sum()):,}  (columns: {', '.join(changed_cols) or '-'})")

        if dry_run or not row_changed.any():
            return int(row_changed.sum())
        out = after.loc[row_changed, t.key + changed_cols]
        out = out.astype(object).where(out.notna(), None)
        db.upsert(t.table, out, key=t.key)
        return len(out)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true", help="report counts only; no backup, no writes")
    args = ap.parse_args()

    if not args.dry_run:
        snap = fix_backup([t.db_path for t in TARGETS], "clean_stored_data")
        print(f"Backup: {snap}")

    total = sum(clean_target(t, args.dry_run) for t in TARGETS)
    verb = "would update" if args.dry_run else "updated"
    print(f"\nDone — {verb} {total:,} rows.")


if __name__ == "__main__":
    main()
