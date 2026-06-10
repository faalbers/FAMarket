"""
Fetch orchestrator (Topic 8) — the weekly Friday run, end to end.

Sequence:
  0. TLS + logging + rotating backup of all .db files.
  1. Group 1 — Symbol Discovery (Polygon + EDGAR) -> symbols.db.
  2. Group 2 — Data Fetch, single-threaded (Phase 1):
       a. yfinance quotes  (runs FIRST — resolves security_type)
       b. type write-back  (quotes.quoteType -> symbols.security_type)
       c. reload universe with resolved types
       d. yfinance OHLCV, yfinance financials  (filter by security_type)
       e. EDGAR financials  (additive-only deep-history backfill, after yfinance)
       f. FRED macro (standalone)
  3. End-of-run reassessment of is_active / is_validated.

The Streamlit Fetch Control panel calls these same functions; each Group 2 fetcher
can also be run independently (e.g. re-run just yfinance after a failure).

Dev testing (Topic 8.3b): pass `subset=[...]` to fetch only a handful of symbols,
and `discover=False` to skip the (slow) full Polygon discovery.
"""

from __future__ import annotations

import pandas as pd

from analysis_layer.pipeline import run_analysis
from config import settings
from core.backup import backup_all
from core.database import Database
from core.logging_config import get_logger, roll_log, setup_logging
from core.net import configure_tls
from data_layer import cancel, fetch_status, symbols
from data_layer.fetchers.edgar_fetcher import EDGARFinancials
from data_layer.fetchers.fred_fetcher import fetch_fred
from data_layer.fetchers.yfinance_fetcher import (
    YFinanceFinancials,
    YFinanceOHLCV,
    YFinanceQuotes,
)

log = get_logger("orchestrator")


def load_fetch_universe(
    db: Database, subset: list[str] | None = None
) -> pd.DataFrame:
    """Active symbols (+ type) eligible for fetching (Topic 3.1: is_active only)."""
    if not db.table_exists(symbols.TABLE):
        return pd.DataFrame(columns=["symbol", "security_type"])
    df = db.read(symbols.TABLE)
    if "is_active" in df.columns:
        df = df[df["is_active"] == 1]
    cols = [c for c in ("symbol", "security_type") if c in df.columns]
    df = df[cols]
    if subset is not None:
        df = df[df["symbol"].isin(subset)]
    return df.reset_index(drop=True)


def run_full_fetch(
    discover: bool = True,
    subset: list[str] | None = None,
    respect_lock: bool = True,
    run_backup: bool = True,
) -> dict:
    """Run the complete fetch pipeline. Returns a per-stage summary.

    Runs any time, including while the US market is open. The only price series
    that must never be captured intraday is OHLCV, and `sanitize_ohlcv` guards
    that itself: it drops any bar past the last fully-settled session, so an
    in-progress (today's) bar is omitted while the market is open.

    On a Stop, the fetch groups unwind at the next safe boundary. Analysis (Group
    3) still rebuilds from the data fetched so far when the stop requested it
    (`cancel.analyze_after_stop()` — the checkbox next to Stop, on by default);
    otherwise it is skipped and analysis.db is left untouched.
    """
    configure_tls()
    roll_log()        # one log per run: prior run archived as a versioned backup
    setup_logging()
    cancel.clear()    # drop any stop left over from a previous run
    summary: dict = {}

    # Any uncaught failure is logged here (with traceback) before it propagates, so
    # the run log records why a run died — not just the short message the UI shows.
    try:
        if run_backup:
            backup_all()

        # -- Groups 1 & 2: discovery + data fetch (return early on Stop) ---- #
        _run_fetch_groups(summary, discover, subset, respect_lock)

        # -- Group 3: analysis (full run: clean-slate rebuild; subset run: ---- #
        # -- subset rows merged into the existing analysis.db) ---------------- #
        # Runs after the symbols DB is closed; the analysis layer opens its own DBs
        # and only processes is_active+is_validated symbols. On a clean finish it
        # always runs; on a Stop it runs only if that stop asked for it.
        if summary.get("cancelled") and not cancel.analyze_after_stop():
            log.info("Fetch stopped — skipping analysis (Analyze after stop is off)")
            return summary
        if summary.get("cancelled"):
            # The normal end-of-Group-2 reassessment was skipped by the early return,
            # so symbols fetched this run aren't yet is_validated and would be
            # invisible to analysis. Run a validate-only pass (never deactivates
            # un-reached symbols) so the data fetched so far actually shows up.
            log.info("Fetch stopped — validating fetched symbols, then rebuilding analysis")
            with Database(settings.SYMBOLS_DB) as sdb:
                universe = load_fetch_universe(sdb, subset)
                summary["reassessment"] = symbols.reassess_state(
                    sdb,
                    assess_symbols=universe["symbol"].tolist(),
                    allow_deactivate=False,
                )
        summary["analysis"] = run_analysis(subset=subset)

        log.info("Full fetch complete — %s", {k: summary.get(k) for k in summary})
        return summary
    except Exception as exc:
        log.exception("Fetch failed — %s: %s", type(exc).__name__, exc)
        raise


def _run_fetch_groups(
    summary: dict,
    discover: bool,
    subset: list[str] | None,
    respect_lock: bool,
) -> None:
    """Group 1 (discovery) + Group 2 (data fetch + reassessment), writing `summary`.

    Returns early — leaving `summary['cancelled'] = True` — at the first stage
    boundary where a Stop was requested, rather than being killed mid-fetch. Every
    completed batch is already committed (`fetch_status` is per-batch), so the run
    resumes cleanly on a re-run. Reassessment is intentionally skipped on a Stop:
    re-deriving is_active/is_validated from a partial fetch could wrongly demote
    symbols not yet reached this run.
    """
    def _cancelled() -> bool:
        if cancel.is_cancelled():
            summary["cancelled"] = True
            log.warning("Fetch cancelled — stopping after the current stage")
            return True
        return False

    # -- Group 1: discovery ------------------------------------------------- #
    if discover:
        summary["discovery"] = symbols.run_discovery()
    if _cancelled():
        return

    # -- Group 2: data fetch ------------------------------------------------ #
    with Database(settings.SYMBOLS_DB) as sdb:
        fetch_status.ensure_table(sdb)

        universe = load_fetch_universe(sdb, subset)
        log.info("Group 2 — %d symbols in fetch universe", len(universe))

        summary["quotes"] = YFinanceQuotes().run(universe, sdb, respect_lock)
        if _cancelled():
            return
        summary["type_writeback"] = symbols.resolve_types_from_quotes()

        # Reload so OHLCV/financials see the freshly resolved security_type.
        universe = load_fetch_universe(sdb, subset)
        summary["ohlcv"] = YFinanceOHLCV().run(universe, sdb, respect_lock)
        if _cancelled():
            return
        summary["financials"] = YFinanceFinancials().run(universe, sdb, respect_lock)
        if _cancelled():
            return
        # EDGAR runs AFTER yfinance so yfinance owns the recent window first; EDGAR
        # only backfills the deep history yfinance can't reach (additive-only).
        summary["edgar_financials"] = EDGARFinancials().run(universe, sdb, respect_lock)
        if _cancelled():
            return

        summary["fred"] = fetch_fred()

        # -- Reassessment (only the symbols fetched this run) --------------- #
        summary["reassessment"] = symbols.reassess_state(
            sdb, assess_symbols=universe["symbol"].tolist()
        )
