"""
Analysis orchestrator (Topic 4.2 — FETCH / ANALYSIS PHASE DESIGN).

Runs the recalculation after a fetch:
  1. Load the Phase 1 DBs into pandas (symbols, quotes, financials, ohlcv, macro)
     — restricted to the subset symbols when one is given.
  2. Universe = symbols with is_active=True AND is_validated=True.
  3. Per symbol: metrics (+reconcile) · technical · intrinsic value.
  4. Subset runs only: merge with the existing analysis table — rows for subset
     symbols are replaced, every other symbol's row is kept as-is.
  5. Cross-symbol stages on the (merged) frame: peer comparisons, then category
     scores + Overall + rs_rank (added with the peers/scoring modules) — so on a
     subset run the ranks/medians still span the full stored universe.
  6. Database.replace() analysis.db — the frame (full or merged) becomes the table.
  7. Record analysis_meta: analyzed_at + prices_as_of + n_symbols.

Prices: each symbol's canonical price is the adj_close of the last completed
session (the OHLCV fetch already caps to it, so it's the latest stored date).

The Streamlit Fetch Control panel calls run_analysis() right after a fetch; it can
also be run standalone. `subset=[...]` recomputes only those symbols (dev testing)
and leaves the rest of analysis.db intact; without it, the classic full
clean-slate rebuild.

rs_rank needs each symbol's raw weighted trailing return, so that input is
persisted as the `rs_raw` column — on a subset run the un-recomputed rows feed
their stored rs_raw back into the universe-wide re-rank. Rows written before
rs_raw existed rank NaN until the next full run.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pandas as pd

from config import settings
from core.database import Database
from core.logging_config import get_logger
from core import meminfo
from analysis_layer import _periods as P
from analysis_layer import metrics, technical, intrinsic_value, peers, scoring
from analysis_layer import estimates as estimates_metrics
from analysis_layer import signals as signals_metrics
from analysis_layer import sector_index
from core.market_calendar import last_completed_session
from analysis_layer.screen_type import classify as classify_screen_type

log = get_logger("analysis")

TABLE = "analysis"
META_TABLE = "analysis_meta"

# Heartbeat cadence for the per-symbol loop — a "still alive" pulse on long full-
# universe runs without per-symbol noise. Small dev subsets never reach it.
_PROGRESS_EVERY = 500

# Identity columns carried alongside the computed metrics (symbol is the key).
# `screen_type` is the sector/industry-derived filtering group (standard / bank /
# insurance / reit / etf / …) — computed here so Filter/Output read one canonical value.
_IDENTITY = ["symbol", "name", "security_type", "screen_type", "sector", "industry",
             "fund_family", "price"]


# Lower bound on the OHLCV window: rs_rank's 4 quarters × ~63 trading days + 1
# mark ≈ 253 trading days ≈ 365 calendar days, plus weekend/holiday slack.
_MIN_OHLCV_LOOKBACK_DAYS = 400

# Bar columns the analysis actually consumes (technical indicators + canonical
# price). `open` is unused; `dividends`/`splits` come from the sparse side reads.
_OHLCV_COLS = "symbol, date, high, low, close, adj_close, volume"


def _subset_clause(subset: list[str] | None) -> tuple[str | None, list[str] | None]:
    """SQL `symbol IN (…)` filter (clause, params) — (None, None) for a full run."""
    if not subset:
        return None, None
    return f"symbol IN ({', '.join('?' for _ in subset)})", list(subset)


def _load(subset: list[str] | None = None) -> dict[str, pd.DataFrame]:
    """Read every Phase 1 database needed by the analysis layer into pandas.

    With a subset, the per-symbol tables are read pre-filtered to those symbols
    (symbols.db and macro.db stay full — the universe gate needs the former, the
    latter has no symbol column).
    """
    out: dict[str, pd.DataFrame] = {}
    with Database(settings.SYMBOLS_DB) as db:
        out["symbols"] = db.read("symbols")
    where, params = _subset_clause(subset)
    for name, path in (("quotes", settings.QUOTES_DB), ("financials", settings.FINANCIALS_DB)):
        with Database(path) as db:
            out[name] = db.read(name, where=where, params=params)
    # Analyst estimates (forward growth / PEG / revision momentum). Optional — the
    # DB may not exist on a setup that never ran the estimates fetcher; read() over a
    # missing table/DB just yields an empty frame, which estimates.compute tolerates.
    with Database(settings.SIGNALS_DB) as db:
        out["estimates"] = db.read("estimates", where=where, params=params)
        # RAW earnings-surprise + ownership tables (analysis_layer/signals.py derives
        # the per-symbol metrics). Missing tables read back empty -> NaN metrics.
        out["earnings_surprise"] = db.read("earnings_surprise", where=where, params=params)
        out["ownership"] = db.read("ownership", where=where, params=params)
    with Database(settings.MACRO_DB) as db:
        out["macro"] = db.read("macro")
    fin = out["financials"]
    if not fin.empty and "period_end" in fin.columns:
        # One vectorized parse for the whole table; _periods.prepare() consumes it
        # instead of re-parsing period_end strings per symbol.
        fin["period_end_dt"] = pd.to_datetime(fin["period_end"], errors="coerce")
    out.update(_load_ohlcv(subset))
    return out


def _load_ohlcv(subset: list[str] | None = None) -> dict[str, pd.DataFrame]:
    """Date-floored OHLCV bars + full-history sparse dividend/split events.

    The full table (~70M rows at a 50k universe) balloons past physical RAM in
    pandas, but no indicator needs more than ~253 trading days — so only the
    trailing ANALYSIS_OHLCV_LOOKBACK_DAYS of bars are read. The two deep-history
    consumers — dividends (div_growth_5y, streaks) and splits (EPS adjustment) —
    are event rows (a handful per symbol-year), read separately with NO floor.
    `date` is parsed to datetime64 once here, not per symbol in the loop.
    """
    empty = {"ohlcv": pd.DataFrame(), "dividends": pd.DataFrame(), "splits": pd.DataFrame()}
    with Database(settings.OHLCV_DB) as db:
        if db.is_empty("ohlcv"):
            return empty
        lookback = settings.ANALYSIS_OHLCV_LOOKBACK_DAYS
        if lookback < _MIN_OHLCV_LOOKBACK_DAYS:
            log.warning("Analysis — ANALYSIS_OHLCV_LOOKBACK_DAYS=%d is below the %d-day "
                        "floor rs_rank needs; clamping to %d",
                        lookback, _MIN_OHLCV_LOOKBACK_DAYS, _MIN_OHLCV_LOOKBACK_DAYS)
            lookback = _MIN_OHLCV_LOOKBACK_DAYS
        last = db.query("SELECT MAX(date) AS d FROM ohlcv")["d"].iloc[0]
        cutoff = (pd.Timestamp(last) - pd.Timedelta(days=lookback)).strftime("%Y-%m-%d")
        sub_clause, sub_params = _subset_clause(subset)
        sub_sql = f" AND {sub_clause}" if sub_clause else ""
        sub_params = sub_params or []
        chunks = list(db.query(f"SELECT {_OHLCV_COLS} FROM ohlcv WHERE date >= ?{sub_sql}",
                               [cutoff, *sub_params], chunksize=2_000_000))
        out = {"ohlcv": pd.concat(chunks, ignore_index=True) if chunks else pd.DataFrame()}
        del chunks
        have = db.columns("ohlcv")
        out["dividends"] = (db.query("SELECT symbol, date, dividends FROM ohlcv "
                                     f"WHERE dividends > 0{sub_sql} ORDER BY symbol, date",
                                     sub_params)
                            if "dividends" in have else pd.DataFrame())
        out["splits"] = (db.query("SELECT symbol, date, splits FROM ohlcv "
                                  f"WHERE splits != 0 AND splits != 1{sub_sql} "
                                  "ORDER BY symbol, date", sub_params)
                         if "splits" in have else pd.DataFrame())
    for df in out.values():
        if not df.empty:
            df["date"] = pd.to_datetime(df["date"])
    return out


def _events_by_symbol(events: pd.DataFrame, col: str) -> dict[str, pd.Series]:
    """{symbol -> datetime-indexed Series of event values} from a sparse frame."""
    if events.empty:
        return {}
    return {s: pd.Series(g[col].to_numpy(), index=pd.DatetimeIndex(g["date"]))
            for s, g in events.groupby("symbol", sort=False)}


def _universe(symbols: pd.DataFrame, subset: list[str] | None) -> pd.DataFrame:
    """Active + validated symbols (Topic 8: the analysis processing gate)."""
    df = symbols
    for flag in ("is_active", "is_validated"):
        if flag in df.columns:
            df = df[df[flag] == 1]
    if subset is not None:
        df = df[df["symbol"].isin(subset)]
    return df.reset_index(drop=True)


def _risk_free(macro: pd.DataFrame) -> float:
    """Latest 10-year Treasury yield as an annual fraction (for the DCF)."""
    if macro.empty or "series" not in macro.columns:
        return settings.DCF_TERMINAL_GROWTH + settings.DCF_MIN_DISCOUNT_SPREAD
    s = macro[macro["series"] == "treasury_10y"].sort_values("date")
    return float(s["value"].iloc[-1]) / 100 if not s.empty else 0.04


def run_analysis(subset: list[str] | None = None) -> dict:
    """Recalculate analysis.db. Returns a run summary.

    Without a subset: full clean-slate rebuild of the whole universe. With one:
    only the subset is recomputed, merged into the existing table (their old rows
    replaced, everything else kept), and the cross-symbol stages re-run over the
    merged frame so ranks and peer medians stay universe-wide.
    """
    subset = list(subset) if subset else None  # treat [] like None (full run)
    data = _load(subset)
    universe = _universe(data["symbols"], subset)
    if universe.empty:
        log.warning("Analysis — no active+validated symbols; nothing to do")
        return {"symbols": 0}

    quotes = data["quotes"].set_index("symbol") if not data["quotes"].empty else pd.DataFrame()
    financials, ohlcv = data["financials"], data.pop("ohlcv")
    risk_free = _risk_free(data["macro"])
    prices_as_of = ohlcv["date"].max().strftime("%Y-%m-%d") if not ohlcv.empty else None
    log.info("Analysis — %d symbols, prices as of %s, risk-free %.3f",
             len(universe), prices_as_of, risk_free)

    # Index the big frames ONCE by symbol — the per-symbol loop otherwise
    # re-scans the whole OHLCV table (millions of rows) on every iteration. One
    # groupby turns 38k full-frame boolean masks into O(1) pre-sorted slices.
    empty_ohlcv = ohlcv.iloc[0:0]
    ohlcv_by = ({s: g.sort_values("date") for s, g in ohlcv.groupby("symbol", sort=False)}
                if not ohlcv.empty else {})
    del ohlcv  # the groups copy the data; don't hold a second full frame alive
    div_by = _events_by_symbol(data.pop("dividends"), "dividends")
    split_by = _events_by_symbol(data.pop("splits"), "splits")
    # Prepare each symbol's period frames ONCE (freq split + date index + sort);
    # the ~50 _periods calls per symbol then reduce to column lookups. Doing it
    # here moves ~2/3 of the old loop cost into one upfront pass.
    empty_fin = P.prepare(financials.iloc[0:0])
    fin_by = ({s: P.prepare(g) for s, g in financials.groupby("symbol", sort=False)}
              if not financials.empty and "symbol" in financials.columns else {})
    # Estimates: one row per (symbol, horizon) -> {symbol: frame indexed by horizon},
    # so the per-symbol lookup is O(1) (same pre-group pattern as the other panels).
    est = data.pop("estimates")
    est_by = ({s: g.set_index("horizon") for s, g in est.groupby("symbol", sort=False)}
              if not est.empty and "symbol" in est.columns else {})
    # Earnings-surprise (many rows/symbol) + ownership (one row/symbol), pre-grouped
    # O(1). `signals_asof` = the analysis vintage, so days_to_next_earnings is one
    # date diff against the last completed session (computed once, not per symbol).
    surp = data.pop("earnings_surprise")
    surp_by = ({s: g for s, g in surp.groupby("symbol", sort=False)}
               if not surp.empty and "symbol" in surp.columns else {})
    own = data.pop("ownership")
    own_by = ({s: g.iloc[0] for s, g in own.groupby("symbol", sort=False)}
              if not own.empty and "symbol" in own.columns else {})
    signals_asof = pd.Timestamp(last_completed_session())

    reconcile: list = []
    rows: list[dict] = []
    n_universe = len(universe)
    for i, rec in enumerate(universe.itertuples(index=False), start=1):
        sym = rec.symbol
        fsym = fin_by.get(sym, empty_fin)
        osym = ohlcv_by.get(sym, empty_ohlcv)
        price = float(osym["adj_close"].iloc[-1]) if len(osym) else float("nan")
        quote = quotes.loc[sym] if sym in quotes.index else None

        m = metrics.compute(sym, fsym, quote, price,
                            dividends=div_by.get(sym), splits=split_by.get(sym),
                            as_of=(osym["date"].iloc[-1] if len(osym) else None),
                            reconcile=reconcile)
        t = technical.compute(sym, osym)
        iv = intrinsic_value.compute(sym, fsym, quote, price, m, risk_free)
        est_m = estimates_metrics.compute(sym, est_by.get(sym), forward_pe=m.get("forward_pe"))
        sig_m = signals_metrics.compute(sym, surp_by.get(sym), own_by.get(sym), asof=signals_asof)
        sec = quote.get("sector") if quote is not None else None
        ind = quote.get("industry") if quote is not None else None
        # Fund provider/sponsor — funds only (NULL for stocks). Filtered as a
        # classification value on the Filter page; lives only in quotes.db, so it
        # must be copied into the analysis row to be screenable (no cross-DB joins).
        fund_family = quote.get("fund_family") if quote is not None else None
        sec_type = getattr(rec, "security_type", None)
        rows.append({
            "symbol": sym,
            "name": getattr(rec, "name", None),
            "security_type": sec_type,
            "screen_type": classify_screen_type(sec_type, sec, ind),
            "sector": sec,
            "industry": ind,
            "fund_family": fund_family,
            "price": price,
            # raw weighted return -> universe-ranked into rs_rank in scoring; kept
            # in the table so subset runs can re-rank the un-recomputed rows too
            "rs_raw": technical.relative_strength_raw(osym),
            **m, **t, **iv, **est_m, **sig_m,
        })
        if i % _PROGRESS_EVERY == 0:
            log.info("Analysis — per-symbol metrics %d/%d (%d to go)…",
                     i, n_universe, n_universe - i)

    df = pd.DataFrame(rows)
    n_recomputed = len(df)
    if subset is not None:
        df, prices_as_of = _merge_existing(df, subset, prices_as_of)
    df = _order_columns(df)

    # -- cross-symbol stages --------------------------------------------------- #
    log.info("Analysis — peer comparisons across %d symbols…", len(df))
    df = peers.compute(df)
    log.info("Analysis — scoring (ranks + category scores)…")
    df = scoring.compute(df)  # rs_rank, category scores, overall_score

    log.info("Analysis — writing analysis.db…")
    _write(df, prices_as_of)
    _log_reconcile(reconcile, n_recomputed)
    log.info("analysis.db — %d symbols (%d recomputed), %d columns written",
             len(df), n_recomputed, df.shape[1])

    # Sector / sub-industry index series — universe-wide, so full runs only. Built
    # from the panels already in memory; isolated in try/except so a failure here
    # never discards the analysis.db write that just succeeded.
    index_summary: dict | None = None
    if subset is None:
        try:
            log.info("Analysis — building sector/industry indices…")
            index_summary = sector_index.build_and_write(
                universe["symbol"].tolist(), quotes, financials, ohlcv_by, prices_as_of)
        except Exception:
            log.exception("Sector/industry index build failed; analysis.db is unaffected")

    ram = meminfo.peak_ram_summary()
    if ram:
        log.info("Analysis — %s", ram)

    return {"symbols": len(df), "recomputed": n_recomputed, "columns": df.shape[1],
            "reconcile_divergences": len(reconcile), "prices_as_of": prices_as_of,
            "indices": index_summary}


def _merge_existing(df: pd.DataFrame, subset: list[str],
                    prices_as_of: str | None) -> tuple[pd.DataFrame, str | None]:
    """Subset run: splice the recomputed rows into the stored analysis table.

    Every stored row whose symbol is in the subset is dropped — replaced by its
    fresh row, or removed outright if the symbol no longer passes the universe
    gate — and all other rows are kept untouched. The cross-symbol stages then
    run over this merged frame, so peer medians, percentile scores and rs_rank
    are recomputed universe-wide (rs_rank via the persisted rs_raw column; rows
    written before rs_raw existed rank NaN until the next full run).
    prices_as_of becomes the newer of the two vintages.
    """
    with Database(settings.ANALYSIS_DB) as db:
        existing = db.read(TABLE)
        meta = db.read(META_TABLE)
    if existing.empty:
        return df, prices_as_of
    kept = existing[~existing["symbol"].isin(subset)]
    log.info("Analysis — subset run: %d fresh rows merged with %d kept rows "
             "(%d replaced/removed)", len(df), len(kept), len(existing) - len(kept))
    if not meta.empty and "prices_as_of" in meta.columns:
        prev = meta["prices_as_of"].iloc[-1]
        if prev is not None and (prices_as_of is None or str(prev) > prices_as_of):
            prices_as_of = str(prev)
    return pd.concat([kept, df], ignore_index=True), prices_as_of


def _order_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Identity columns first, then everything else in stable insertion order."""
    ident = [c for c in _IDENTITY if c in df.columns]
    return df[ident + [c for c in df.columns if c not in ident]]


def _write(df: pd.DataFrame, prices_as_of: str | None) -> None:
    meta = pd.DataFrame([{
        "analyzed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "prices_as_of": prices_as_of,
        "n_symbols": len(df),
    }])
    with Database(settings.ANALYSIS_DB) as db:
        db.replace(TABLE, df)
        db.replace(META_TABLE, meta)


def _log_reconcile(reconcile: list, n_symbols: int) -> None:
    """One summary line for the compute-vs-yfinance divergences (no per-row noise)."""
    if not reconcile:
        log.info("Reconciliation — no divergences > %.0f%%", settings.RECONCILE_TOLERANCE_PCT * 100)
        return
    rdf = pd.DataFrame(reconcile)
    by_metric = rdf["metric"].value_counts().to_dict()
    log.info("Reconciliation — %d divergences > %.0f%% across %d/%d symbols; by metric: %s",
             len(rdf), settings.RECONCILE_TOLERANCE_PCT * 100,
             rdf["symbol"].nunique(), n_symbols, by_metric)
