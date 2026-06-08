"""
Dev runner for the full fetch pipeline (Phase 1).

    python -m scripts.run_fetch                         # full run (discovery + all data)
    python -m scripts.run_fetch --no-discover           # skip Polygon/EDGAR discovery
    python -m scripts.run_fetch --subset AAPL,MSFT,SPY  # fetch only these symbols
    python -m scripts.run_fetch --no-discover --subset AAPL,MSFT --no-lock

Use --subset (with --no-discover) for fast iteration on a handful of symbols, per
the roadmap's dev-testing strategy. Delete databases/*.db to reset to a clean
slate (the system auto-detects empty DBs as an initial load).
"""

from __future__ import annotations

import argparse

from data_layer.orchestrator import run_full_fetch


def main() -> None:
    p = argparse.ArgumentParser(description="Run the FAMarket fetch pipeline")
    p.add_argument("--no-discover", action="store_true", help="skip symbol discovery")
    p.add_argument("--subset", help="comma-separated symbols to fetch only")
    p.add_argument("--no-lock", action="store_true", help="ignore the 5-day fetch lock")
    p.add_argument("--no-backup", action="store_true", help="skip the pre-run backup")
    p.add_argument(
        "--allow-market-open", action="store_true",
        help="fetch even while the US market is open (off by default — prices would be intraday)",
    )
    args = p.parse_args()

    subset = [s.strip().upper() for s in args.subset.split(",")] if args.subset else None
    summary = run_full_fetch(
        discover=not args.no_discover,
        subset=subset,
        respect_lock=not args.no_lock,
        run_backup=not args.no_backup,
        block_when_market_open=not args.allow_market_open,
    )
    print("\n=== RUN SUMMARY ===")
    for stage, result in summary.items():
        print(f"  {stage:16} {result}")


if __name__ == "__main__":
    main()
