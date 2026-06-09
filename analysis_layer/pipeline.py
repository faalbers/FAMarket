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
from analysis_layer import _periods, metrics, technical, intrinsic_value, peers, scoring
from analysis_layer.screen_type import classify as classify_screen_type

log = get_logger("analysis")

TABLE = "analysis"
META_TABLE = "analysis_meta"

# Identity columns carried alongside the computed metrics (symbol is the key).
# `screen_type` is the sector/industry-derived filtering group (standard / bank /
# insurance / reit / etf / …) — computed here so Filter/Output read one canonical value.
_IDENTITY = ["symbol", "name", "security_type", "screen_type", "sector", "industry", "price"]


def _load() -> dict[str, pd.DataFrame]:
    """Read every Phase 1 database needed by the analysis layer into pandas."""
    out: dict[str, pd.DataFrame] = {}
    with Database(settings.SYMBOLS_DB) as db:
        out["symbols"] = db.read("symbols")
    for name, path in (("quotes", settings.QUOTES_DB), ("financials", settings.FINANCIALS_DB),
                       ("ohlcv", settings.OHLCV_DB), ("macro", settings.MACRO_DB)):
        with Database(path) as db:
            out[name] = db.read(name if name != "macro" else "macro")
    return out


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
    financials, ohlcv = data["financials"], data["ohlcv"]
    risk_free = _risk_free(data["macro"])
    prices_as_of = str(ohlcv["date"].max()) if not ohlcv.empty else None
    log.info("Analysis — %d symbols, prices as of %s, risk-free %.3f",
             len(universe), prices_as_of, risk_free)

    reconcile: list = []
    rows: list[dict] = []
    for rec in universe.itertuples(index=False):
        sym = rec.symbol
        fsym = _periods.by_symbol(financials, sym)
        osym = ohlcv[ohlcv["symbol"] == sym].sort_values("date") if not ohlcv.empty else ohlcv
        price = float(osym["adj_close"].iloc[-1]) if len(osym) else float("nan")
        quote = quotes.loc[sym] if sym in quotes.index else None

        m = metrics.compute(sym, fsym, quote, osym, price, reconcile=reconcile)
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

    df = pd.DataFrame(rows)
    df = _order_columns(df)

    # -- cross-symbol stages --------------------------------------------------- #
    df = peers.compute(df)
    df = scoring.compute(df)  # rs_rank, category scores, overall_score

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
