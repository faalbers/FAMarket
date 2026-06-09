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
    """
    configure_tls()
    roll_log()        # one log per run: prior run archived as a versioned backup
    setup_logging()
    cancel.clear()    # drop any stop left over from a previous run
    summary: dict = {}

    def _cancelled() -> bool:
        """True if a stop was requested; records it on the summary and logs once.

        Polled at stage boundaries — a cancelled run stops cleanly here rather than
        being killed mid-fetch, and skips the remaining stages (including the
        analysis rebuild, so analysis.db is never rebuilt from partial data).
        """
        if cancel.is_cancelled():
            summary["cancelled"] = True
            log.warning("Fetch cancelled — stopping after the current stage")
            return True
        return False

    if run_backup:
        backup_all()

    # -- Group 1: discovery ------------------------------------------------- #
    if discover:
        summary["discovery"] = symbols.run_discovery()
    if _cancelled():
        return summary

    # -- Group 2: data fetch ------------------------------------------------ #
    with Database(settings.SYMBOLS_DB) as sdb:
        fetch_status.ensure_table(sdb)

        universe = load_fetch_universe(sdb, subset)
        log.info("Group 2 — %d symbols in fetch universe", len(universe))

        summary["quotes"] = YFinanceQuotes().run(universe, sdb, respect_lock)
        if _cancelled():
            return summary
        summary["type_writeback"] = symbols.resolve_types_from_quotes()

        # Reload so OHLCV/financials see the freshly resolved security_type.
        universe = load_fetch_universe(sdb, subset)
        summary["ohlcv"] = YFinanceOHLCV().run(universe, sdb, respect_lock)
        if _cancelled():
            return summary
        summary["financials"] = YFinanceFinancials().run(universe, sdb, respect_lock)
        if _cancelled():
            return summary
        # EDGAR runs AFTER yfinance so yfinance owns the recent window first; EDGAR
        # only backfills the deep history yfinance can't reach (additive-only).
        summary["edgar_financials"] = EDGARFinancials().run(universe, sdb, respect_lock)
        if _cancelled():
            return summary

        summary["fred"] = fetch_fred()

        # -- Reassessment (only the symbols fetched this run) --------------- #
        summary["reassessment"] = symbols.reassess_state(
            sdb, assess_symbols=universe["symbol"].tolist()
        )
    if _cancelled():
        return summary

    # -- Group 3: analysis (clean-slate rebuild of analysis.db) ------------- #
    # Runs after the symbols DB is closed; the analysis layer opens its own DBs
    # and only processes is_active+is_validated symbols set by the reassessment.
    summary["analysis"] = run_analysis(subset=subset)

    log.info("Full fetch complete — %s", {k: summary.get(k) for k in summary})
    return summary
