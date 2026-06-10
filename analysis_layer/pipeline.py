"""
Analysis orchestrator (Topic 4.2 — FETCH / ANALYSIS PHASE DESIGN).

Runs the full clean-slate recalculation after a fetch:
  1. Load the Phase 1 DBs into pandas (symbols, quotes, financials, ohlcv, macro).
  2. Universe = symbols with is_active=True AND is_validated=True.
  3. Per symbol: metrics (+reconcile) · technical · intrinsic value.
  4. Cross-symbol stages on the assembled frame: peer comparisons, then category
     scores + Overall + rs_rank (added with the peers/scoring modules).
  5. Database.replace() analysis.db — full rebuild, no deltas.
  6. Record analysis_meta: analyzed_at + prices_as_of + n_symbols.

Prices: each symbol's canonical price is the adj_close of the last completed
session (the OHLCV fetch already caps to it, so it's the latest stored date).

The Streamlit Fetch Control panel calls run_analysis() right after a fetch; it can
also be run standalone. `subset=[...]` limits the universe for dev testing.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pandas as pd

from config import settings
from core.database import Database
from core.logging_config import get_logger
from analysis_layer import metrics, technical, intrinsic_value, peers, scoring
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
_IDENTITY = ["symbol", "name", "security_type", "screen_type", "sector", "industry", "price"]


# Lower bound on the OHLCV window: rs_rank's 4 quarters × ~63 trading days + 1
# mark ≈ 253 trading days ≈ 365 calendar days, plus weekend/holiday slack.
_MIN_OHLCV_LOOKBACK_DAYS = 400

# Bar columns the analysis actually consumes (technical indicators + canonical
# price). `open` is unused; `dividends`/`splits` come from the sparse side reads.
_OHLCV_COLS = "symbol, date, high, low, close, adj_close, volume"


def _load() -> dict[str, pd.DataFrame]:
    """Read every Phase 1 database needed by the analysis layer into pandas."""
    out: dict[str, pd.DataFrame] = {}
    with Database(settings.SYMBOLS_DB) as db:
        out["symbols"] = db.read("symbols")
    for name, path in (("quotes", settings.QUOTES_DB), ("financials", settings.FINANCIALS_DB),
                       ("macro", settings.MACRO_DB)):
        with Database(path) as db:
            out[name] = db.read(name)
    out.update(_load_ohlcv())
    return out


def _load_ohlcv() -> dict[str, pd.DataFrame]:
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
        chunks = list(db.query(f"SELECT {_OHLCV_COLS} FROM ohlcv WHERE date >= ?",
                               [cutoff], chunksize=2_000_000))
        out = {"ohlcv": pd.concat(chunks, ignore_index=True) if chunks else pd.DataFrame()}
        del chunks
        have = db.columns("ohlcv")
        out["dividends"] = (db.query("SELECT symbol, date, dividends FROM ohlcv "
                                     "WHERE dividends > 0 ORDER BY symbol, date")
                            if "dividends" in have else pd.DataFrame())
        out["splits"] = (db.query("SELECT symbol, date, splits FROM ohlcv "
                                  "WHERE splits != 0 AND splits != 1 ORDER BY symbol, date")
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
    """Full clean-slate recalculation of analysis.db. Returns a run summary."""
    data = _load()
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
    empty_fin = financials.iloc[0:0]
    fin_by = ({s: g for s, g in financials.groupby("symbol", sort=False)}
              if not financials.empty and "symbol" in financials.columns else {})

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
        sec = quote.get("sector") if quote is not None else None
        ind = quote.get("industry") if quote is not None else None
        sec_type = getattr(rec, "security_type", None)
        rows.append({
            "symbol": sym,
            "name": getattr(rec, "name", None),
            "security_type": sec_type,
            "screen_type": classify_screen_type(sec_type, sec, ind),
            "sector": sec,
            "industry": ind,
            "price": price,
            # raw weighted return -> universe-ranked into rs_rank in scoring (dropped after)
            "_rs_raw": technical.relative_strength_raw(osym),
            **m, **t, **iv,
        })
        if i % _PROGRESS_EVERY == 0:
            log.info("Analysis — per-symbol metrics %d/%d (%d to go)…",
                     i, n_universe, n_universe - i)

    df = pd.DataFrame(rows)
    df = _order_columns(df)

    # -- cross-symbol stages --------------------------------------------------- #
    log.info("Analysis — peer comparisons across %d symbols…", len(df))
    df = peers.compute(df)
    log.info("Analysis — scoring (ranks + category scores)…")
    df = scoring.compute(df)  # rs_rank, category scores, overall_score

    log.info("Analysis — writing analysis.db…")
    _write(df, prices_as_of)
    _log_reconcile(reconcile, len(df))
    log.info("analysis.db — %d symbols, %d columns written", len(df), df.shape[1])
    return {"symbols": len(df), "columns": df.shape[1],
            "reconcile_divergences": len(reconcile), "prices_as_of": prices_as_of}


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
