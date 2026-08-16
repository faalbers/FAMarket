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
from data_layer.fetchers.base import BaseFetcher
from data_layer.fetchers.edgar_fetcher import EDGARFinancials
from data_layer.fetchers.fred_fetcher import fetch_fred
from data_layer.fetchers.yfinance_fetcher import (
    YFinanceFinancials,
    YFinanceOHLCV,
    YFinanceQuotes,
    YFinanceSignals,
)

log = get_logger("orchestrator")


def load_fetch_universe(
    db: Database, subset: list[str] | None = None
) -> pd.DataFrame:
    """Active symbols (+ type) eligible for fetching (Topic 3.1: is_active only).

    Indices are excluded: they are reference-only (Polygon's `indices` market,
    typed `index` at discovery time) and carry no fetchable fundamentals, so the
    Data Fetch group skips them entirely. (Benchmark OHLCV, if ever needed, is an
    analysis-layer concern — see ROADMAP Topic on indices.)
    """
    if not db.table_exists(symbols.TABLE):
        return pd.DataFrame(columns=["symbol", "security_type"])
    df = db.read(symbols.TABLE)
    if "is_active" in df.columns:
        df = df[df["is_active"] == 1]
    if "security_type" in df.columns:
        df = df[df["security_type"] != "index"]
    cols = [c for c in ("symbol", "security_type") if c in df.columns]
    df = df[cols]
    if subset is not None:
        df = df[df["symbol"].isin(subset)]
    return df.reset_index(drop=True)


def load_ohlcv_universe(
    db: Database, subset: list[str] | None = None
) -> pd.DataFrame:
    """The shared fetch universe PLUS the benchmark indices (Topic on indices).

    OHLCV is the one fetcher indices belong in: they have no fundamentals to
    fetch, but they do have price history, and a benchmark series is worth
    charting against. `load_fetch_universe` drops every index for the other
    fetchers' sake, so the allow-list is re-added here and ONLY here.

    Deliberately not done by relaxing the filter in `load_fetch_universe`:
    YFinanceQuotes has `applies_to = ()` (all types), so an index reaching the
    shared universe would also be quoted — and a quote alone marks an index
    is_validated (`symbols.reassess_state`: "everything else -> quote"), which
    would drop it into the analysis universe and produce junk screening rows.
    Keeping the benchmarks out of the shared universe also keeps them out of the
    end-of-run reassessment, so their is_active/is_validated are never touched.
    """
    universe = load_fetch_universe(db, subset)
    benchmarks = [s for s in settings.BENCHMARK_SYMBOLS
                  if subset is None or s in set(subset)]
    if not benchmarks:
        return universe
    # security_type "index" is kept by YFinanceOHLCV.select_symbols (its
    # applies_to lists it) and rejected by every other Group 2 fetcher's.
    extra = pd.DataFrame({"symbol": benchmarks, "security_type": "index"})
    known = set(universe["symbol"]) if not universe.empty else set()
    extra = extra[~extra["symbol"].isin(known)]      # never double-list a symbol
    if extra.empty:
        return universe
    cols = list(universe.columns) if not universe.empty else list(extra.columns)
    return pd.concat([universe, extra.reindex(columns=cols)], ignore_index=True)


#: Group 2 fetchers in run order, with a friendly label for the dry-run report.
#: (FRED is omitted — it fetches a fixed set of macro series, not a symbol universe.)
#:
#: KEEP IN SYNC with the per-fetcher `.run(...)` calls in `_run_fetch_groups()`
#: (the real Group 2 sequence). These two rosters are deliberately separate — the
#: real run interleaves non-gate steps (type write-back between quotes and OHLCV)
#: that the report skips — so adding/removing a Group 2 fetcher means editing BOTH.
#: Gate *logic* itself is shared via `BaseFetcher.select_due()` and needs no change
#: here. See `report_fetch()`.
def _report_fetchers() -> list[tuple[str, BaseFetcher]]:
    return [
        ("yfinance quotes", YFinanceQuotes()),
        ("yfinance OHLCV", YFinanceOHLCV()),
        ("yfinance financials", YFinanceFinancials()),
        ("yfinance signals", YFinanceSignals()),
        ("EDGAR financials", EDGARFinancials()),
    ]


def report_fetch(
    subset: list[str] | None = None, respect_lock: bool = True
) -> dict:
    """Dry-run the Group 2 gates: how many symbols each fetcher would fetch now.

    Network-free — every gate (applies_to, the 5-day lock, abandonment, staleness,
    the financials filing-cycle due date) reads only fetch_status and the stored
    data. The 5-day lock (respect_lock) and the viability gates (abandonment /
    staleness / due-date, governed by FETCH_ABANDONMENT_ENABLED) are independent.
    Returns the fetch-universe size, both gate switches, plus per fetcher the
    candidate/locked/abandoned/stale/not_due/due breakdown (see select_due).

    Caveat the UI surfaces: in a real run yfinance quotes runs first and resolves
    security_type, then the universe is reloaded; this report uses the *currently
    stored* types, so OHLCV/financials counts assume types don't change this run.
    """
    report: dict = {
        "respect_lock": respect_lock,
        "abandonment_enabled": settings.FETCH_ABANDONMENT_ENABLED,
        "lock_days": settings.FETCH_LOCK_DAYS,
    }
    with Database(settings.SYMBOLS_DB) as sdb:
        fetch_status.ensure_table(sdb)
        universe = load_fetch_universe(sdb, subset)
        report["universe"] = len(universe)
        # OHLCV runs against a wider universe than the rest (the benchmark
        # indices), exactly as the real run does — score it against that one or
        # the report understates it.
        ohlcv_universe = load_ohlcv_universe(sdb, subset)
        steps = []
        for label, fetcher in _report_fetchers():
            step_universe = (
                ohlcv_universe if isinstance(fetcher, YFinanceOHLCV) else universe
            )
            _, stats = fetcher.select_due(step_universe, sdb, respect_lock)
            steps.append({"step": label, **stats})
        report["steps"] = steps
    return report


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

    On a Stop, the fetch groups unwind at the next safe boundary. A validate-only
    reassessment of the run's symbols always runs (so the data fetched so far is
    reflected in is_validated). Analysis (Group 3) then rebuilds from that data
    when the stop requested it (`cancel.analyze_after_stop()` — the checkbox next
    to Stop, on by default); otherwise analysis is skipped and analysis.db is left
    untouched, but the reassessment has still happened.
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

        # -- Stop-path reassessment (always runs on a Stop) ----------------- #
        # The normal end-of-Group-2 reassessment was skipped by the early return,
        # so symbols fetched this run aren't yet is_validated. Run a validate-only
        # pass (never deactivates un-reached symbols), scoped to the run's symbols,
        # so the data fetched so far is reflected. This happens regardless of the
        # "Analyze after stop" choice — only the analysis rebuild below is gated.
        if summary.get("cancelled"):
            log.info("Fetch stopped — reassessing the fetched symbols (updated only)")
            with Database(settings.SYMBOLS_DB) as sdb:
                universe = load_fetch_universe(sdb, subset)
                summary["reassessment"] = symbols.reassess_state(
                    sdb,
                    assess_symbols=universe["symbol"].tolist(),
                    allow_deactivate=False,
                )

        # -- Group 3: analysis (full run: clean-slate rebuild; subset run: ---- #
        # -- subset rows merged into the existing analysis.db) ---------------- #
        # Runs after the symbols DB is closed; the analysis layer opens its own DBs
        # and only processes is_active+is_validated symbols. On a clean finish it
        # always runs; on a Stop it runs only if that stop asked for it (the
        # reassessment above has already run either way).
        if summary.get("cancelled") and not cancel.analyze_after_stop():
            log.info("Fetch stopped — skipping analysis (Analyze after stop is off)")
            return summary
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
    # The per-fetcher `.run(...)` roster below is mirrored by `_report_fetchers()`
    # (the Report Fetch dry run). KEEP THE TWO IN SYNC: add/remove a Group 2 fetcher
    # in both. Only the roster is duplicated — the gate logic is shared through
    # `BaseFetcher.select_due()`, which both `.run()` and the report call.
    with Database(settings.SYMBOLS_DB) as sdb:
        fetch_status.ensure_table(sdb)
        # Benchmarks discovery never found (^GSPC, ^IXIC) get an identity row so
        # nothing holds price history without a name. Additive-only, idempotent.
        symbols.ensure_benchmark_symbols(sdb)

        universe = load_fetch_universe(sdb, subset)
        log.info("Group 2 — %d symbols in fetch universe", len(universe))

        summary["quotes"] = YFinanceQuotes().run(universe, sdb, respect_lock)
        if _cancelled():
            return
        summary["type_writeback"] = symbols.resolve_types_from_quotes()

        # Reload so OHLCV/financials see the freshly resolved security_type.
        universe = load_fetch_universe(sdb, subset)
        # OHLCV alone also fetches the benchmark indices — see
        # load_ohlcv_universe for why they must not join the shared universe.
        summary["ohlcv"] = YFinanceOHLCV().run(
            load_ohlcv_universe(sdb, subset), sdb, respect_lock
        )
        if _cancelled():
            return
        summary["financials"] = YFinanceFinancials().run(universe, sdb, respect_lock)
        if _cancelled():
            return
        # yfinance signals — same universe (resolved types), one pass over the same
        # Ticker: forward estimates + earnings-surprise history + ownership snapshot
        # into signals.db (~5 requests/symbol, under the Financials envelope).
        summary["signals"] = YFinanceSignals().run(universe, sdb, respect_lock)
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
