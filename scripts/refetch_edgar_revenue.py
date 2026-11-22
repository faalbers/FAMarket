"""
One-time re-fetch: re-read SEC data for companies whose SEC revenue may be the
wrong line.

The SEC importer now checks each revenue tag against the company's stored Yahoo
revenue in the years both have (`edgar_fetcher._off_reference`): a tag more than
25% off is not used, so banks' fee-only contract revenue (Western Alliance 3% of
Yahoo's revenue, Huntington 19%) and sub-lines (General Mills' `Revenues` 2019-24,
10%) are dropped. It also reads `RevenuesNetOfInterestExpense`, the total Yahoo
uses for banks, brokers and card issuers (American Express, Zions). The stored
rows don't record which tag they came from, so they are re-read.

Targets (companies with stored SEC rows):
  * every financial (Yahoo sector "Financial Services") or REIT, and
  * every company whose revenue steps > 1.33x either way from its last SEC year
    to its first Yahoo year.

See `scripts/_edgar_refetch.py` for the backup and the write. Afterwards a full
analysis run is needed.

Usage
-----
    python -m scripts.refetch_edgar_revenue --dry-run   # fetch + report, no writes
    python -m scripts.refetch_edgar_revenue
"""

from __future__ import annotations

import argparse

import numpy as np

from config import settings
from core.database import Database
from scripts._edgar_refetch import refetch

SEAM_STEP = 1.33


def _targets() -> list[str]:
    with Database(settings.FINANCIALS_DB) as fdb:
        fin = fdb.query(
            "SELECT symbol, period_end, source, total_revenue FROM financials "
            "WHERE freq = 'annual' AND total_revenue > 0")
    with Database(settings.QUOTES_DB) as qdb:
        q = qdb.query("SELECT symbol, sector, quoteType FROM quotes")
    edgar = fin[fin["source"] == "edgar"]
    yahoo = fin[fin["source"] != "edgar"]
    last_e = edgar.sort_values("period_end").groupby("symbol").last()
    first_y = yahoo.sort_values("period_end").groupby("symbol").first()
    both = last_e.join(first_y, lsuffix="_e", rsuffix="_y", how="inner")
    both = both[both["period_end_e"] < both["period_end_y"]]
    step = np.abs(np.log(both["total_revenue_y"] / both["total_revenue_e"]))
    seam = set(both.index[step > np.log(SEAM_STEP)])
    fin_sector = set(q.loc[q["sector"].isin(["Financial Services", "Real Estate"]), "symbol"])
    return sorted(seam | (fin_sector & set(edgar["symbol"])))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true", help="fetch and report; no backup, no writes")
    args = ap.parse_args()
    refetch(_targets(), "refetch_edgar_revenue", args.dry_run)


if __name__ == "__main__":
    main()
