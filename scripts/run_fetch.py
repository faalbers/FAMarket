"""
Runner for the full fetch pipeline (Phase 1) — also the detached entry point.

    python -m scripts.run_fetch                         # full run (discovery + all data)
    python -m scripts.run_fetch --no-discover           # skip Polygon/EDGAR discovery
    python -m scripts.run_fetch --subset AAPL,MSFT,SPY  # fetch only these symbols
    python -m scripts.run_fetch --no-discover --subset AAPL,MSFT --no-lock
    python -m scripts.run_fetch --analysis-only         # rebuild analysis.db only, no fetch

Use --subset (with --no-discover) for fast iteration on a handful of symbols, per
the roadmap's dev-testing strategy. Delete databases/*.db to reset to a clean
slate (the system auto-detects empty DBs as an initial load).

The Fetch Control page launches this module as a DETACHED process (see
data_layer/launcher.py) so the fetch survives closing the app. Either way the run
records its lifecycle in data_layer/run_state.py (the cross-process state file) so
the UI knows whether a fetch is running and how the last one ended.
"""

from __future__ import annotations

import argparse

from analysis_layer.pipeline import run_analysis
from config import settings
from core.logging_config import get_logger, roll_log
from data_layer import run_state
from data_layer.orchestrator import run_full_fetch


def _run(args: argparse.Namespace) -> dict:
    """Execute the chosen pipeline and return its summary dict."""
    subset = [s.strip().upper() for s in args.subset.split(",")] if args.subset else None
    if args.analysis_only:
        # Analysis-only rebuild (no fetch). Roll the log first, exactly as the
        # orchestrator does at the top of a fetch, so this run gets a fresh log.
        roll_log()
        return {"analysis": run_analysis(subset=subset)}
    return run_full_fetch(
        discover=not args.no_discover,
        subset=subset,
        respect_lock=not args.no_lock,
        run_backup=not args.no_backup,
    )


def main() -> None:
    p = argparse.ArgumentParser(description="Run the FAMarket fetch pipeline")
    p.add_argument("--no-discover", action="store_true", help="skip symbol discovery")
    p.add_argument("--subset", help="comma-separated symbols to fetch only")
    p.add_argument(
        "--no-lock",
        action="store_true",
        help="ignore the 5-day fetch lock (viability gates still apply; set "
        "FETCH_ABANDONMENT_ENABLED=False to bypass abandonment/staleness/due-date)",
    )
    p.add_argument("--no-backup", action="store_true", help="skip the pre-run backup")
    p.add_argument(
        "--analysis-only", action="store_true",
        help="rebuild analysis.db from already-fetched data; no fetch",
    )
    p.add_argument("--label", default=None, help="display label for the run-state file")
    args = p.parse_args()

    settings.ensure_runtime_dirs()
    mode = "analysis" if args.analysis_only else "fetch"
    label = args.label or ("analysis only" if args.analysis_only else "full universe")

    # Record this run in the cross-process state file (own PID, so liveness checks
    # track the real worker even behind the venv launcher shim).
    run_state.mark_running(label, mode)
    try:
        summary = _run(args)
    except Exception as exc:
        get_logger("run_fetch").exception(
            "Run failed — %s: %s", type(exc).__name__, exc
        )
        run_state.mark_error(f"{type(exc).__name__}: {exc}")
        raise
    if summary.get("cancelled"):
        run_state.mark_cancelled(summary)
    else:
        run_state.mark_done(summary)

    print("\n=== RUN SUMMARY ===")
    for stage, result in summary.items():
        print(f"  {stage:16} {result}")


if __name__ == "__main__":
    main()
