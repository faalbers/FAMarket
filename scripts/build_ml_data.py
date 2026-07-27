"""
Standalone ML training-data export — NOT part of the fetch/analysis pipeline.

Reconstructs DAILY historical values of Technical indicators plus
Valuation/Profitability/Growth/Balance-sheet, Income and Intrinsic-Value metrics
for a chosen group of symbols, by reusing the SAME formula functions as
analysis_layer (metrics.py, technical.py, intrinsic_value.py) — each is fed a
point-in-time slice of OHLCV/financials/macro data ending at day X instead of
"as of today," and just recomputed once per historical trading day.

    python -m scripts.build_ml_data --symbols-file selections/ml.syms
    python -m scripts.build_ml_data --symbols AAPL,MSFT --start-date 2024-01-01

Output: one table (`ml_data`) in databases/ml_data.db, one row per (symbol, date).

Known limitations:
  * financials.db has no filing/report date, only period_end — "known as of day X"
    is approximated as period_end + FINANCIALS_REPORT_LAG_DAYS (45d). Not exact,
    since the true SEC filing date isn't stored anywhere in this project.
  * quotes.db only ever holds a CURRENT snapshot (no history), so historical rows
    pass quote=None into metrics.compute()/intrinsic_value.compute(). Effects:
    sharesOutstanding falls back to the financials-based fields
    (ordinary_shares_number / share_issued — actually more point-in-time-correct
    than a live share count would be); the foreign-currency mismatch check
    defaults to "ok" (fine for the vast majority of US-listed symbols).
  * sector / industry / security_type are stamped from CURRENT quotes.db /
    symbols.db values on every row for a symbol — these rarely change, but are
    NOT reconstructed historically.
  * Excludes Scores (Value/Quality/Growth/Momentum/Income/Overall) and RS Rank —
    both need whole-universe peer ranking, out of scope for a symbol-group export.
  * Excludes Estimates and Ownership — yfinance snapshot tables only, no history
    has ever been archived, so there is nothing to reconstruct.
  * No forward-return label columns. `price` (adj_close) is exported per day;
    forward returns for whatever horizon(s) are needed get computed downstream by
    shifting that column, matched on date (not row-offset) to stay correct across
    any rare missing trading day.
  * Only actual trading-session dates are included (matches ohlcv.db's existing
    session gating) — no weekend/holiday rows, no gap-filling of genuine missing
    days either.
  * --start-date/--end-date only trim which rows get WRITTEN — the full available
    history is still loaded and computed underneath so early rows in a trimmed
    range keep their true lookback (e.g. a 200-day moving average on the first
    requested day still sees the 200 days before it). They don't reduce runtime;
    trim the symbol group for that.

Yearly fallback for gappy quarters: metrics.compute() requires 4 CONSECUTIVE
known quarters (no gap over 45 days... FINANCIALS_REPORT_LAG_DAYS, actually 120d
for the gap check) to compute TTM net_income/revenue/ebitda/fcf — a real, fairly
common data-completeness issue in financials.db (missing quarters or blank
fields), not something specific to this script (the SAME rule blanks out ~8% of
stocks' P/E in the live analysis.db today too). `_fill_ttm_gaps()` (below) fills
pe/eps_ttm/ps/ev_revenue/ev_ebitda/p_fcf with the latest YEARLY figure whenever
the quarterly TTM version is unavailable, using the same one-line ratio formulas
metrics.py already has — metrics.py/_periods.py are untouched. Adds `ni_basis`/
`revenue_basis`/`ebitda_basis`/`fcf_basis` columns ("ttm"/"annual"/"none") so
filled rows are identifiable — a yearly figure can be up to ~21 months stale vs.
TTM's ~3 months, so these ratios mean slightly different things depending on
basis.

Performance: symbols are independent, so --workers > 1 parallelizes across them
with a process pool (each ~70s/symbol of full 10y history is pure CPU work, no
shared state). Within a symbol, the OHLCV window fed to technical.compute() is
capped at _TECH_LOOKBACK_DAYS trading days — every indicator it computes tops out
at a 252-day lookback, so feeding it the whole multi-year history past that point
is pure waste (technical.compute() takes the last row as "today" regardless of
how far back the window starts, so capping the front doesn't change any result,
just how much history it has to re-scan on each historical day).
"""

from __future__ import annotations

import argparse
import bisect
import os
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

import pandas as pd

from analysis_layer import _periods as P
from analysis_layer import intrinsic_value, metrics, technical
from config import settings
from core.database import Database
from core.logging_config import get_logger, setup_logging
from ui.selection_io import load_selection

log = get_logger("ml_data")

TABLE = "ml_data"

# >= the longest indicator lookback (technical.py's _WEEK52/_TREND_WINDOW = 252
# trading days) with headroom for EMA-based indicators (MACD/RSI) to settle.
# Matches the rationale behind pipeline.py's own _MIN_OHLCV_LOOKBACK_DAYS floor.
_TECH_LOOKBACK_DAYS = 400


def _symbols_from_args(args: argparse.Namespace) -> list[str]:
    if args.symbols_file:
        sel = load_selection(args.symbols_file)
        symbols = list(sel["items"].keys())
    elif args.symbols:
        symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    else:
        raise SystemExit("Pass --symbols-file <path.syms> or --symbols SYM1,SYM2,...")
    if not symbols:
        raise SystemExit("Symbol list is empty")
    return symbols


def _placeholders(symbols: list[str]) -> str:
    return ",".join("?" * len(symbols))


def _load_ohlcv(symbols: list[str]) -> pd.DataFrame:
    """Full, unfloored OHLCV history for just these symbols.

    Unlike the analysis pipeline (which caps to ANALYSIS_OHLCV_LOOKBACK_DAYS to
    keep a ~50k-symbol universe in RAM), a small symbol group's full history is
    cheap to load in full — and every day needs its true trailing window anyway.
    """
    with Database(settings.OHLCV_DB) as db:
        have = db.columns("ohlcv")
        cols = ["symbol", "date", "high", "low", "close", "adj_close", "volume"]
        cols += [c for c in ("dividends", "splits") if c in have]
        df = db.query(
            f"SELECT {', '.join(cols)} FROM ohlcv WHERE symbol IN ({_placeholders(symbols)}) "
            "ORDER BY symbol, date",
            symbols,
        )
    if not df.empty:
        df["date"] = pd.to_datetime(df["date"])
    return df


def _load_financials(symbols: list[str]) -> pd.DataFrame:
    with Database(settings.FINANCIALS_DB) as db:
        df = db.read("financials", where=f"symbol IN ({_placeholders(symbols)})", params=symbols)
    if not df.empty and "period_end" in df.columns:
        df["period_end_dt"] = pd.to_datetime(df["period_end"], errors="coerce")
    return df


def _load_macro() -> pd.DataFrame:
    with Database(settings.MACRO_DB) as db:
        df = db.read("macro")
    if not df.empty and "date" in df.columns:
        df["date"] = pd.to_datetime(df["date"])
    return df


def _load_static_tags(symbols: list[str]) -> dict[str, dict]:
    """Current sector/industry/security_type per symbol — a snapshot, not
    reconstructed historically (see module docstring)."""
    tags = {s: {"sector": None, "industry": None, "security_type": None} for s in symbols}
    ph = _placeholders(symbols)
    with Database(settings.SYMBOLS_DB) as db:
        sdf = db.read("symbols", where=f"symbol IN ({ph})", params=symbols)
    for _, r in sdf.iterrows():
        tags[str(r["symbol"])]["security_type"] = r.get("security_type")
    with Database(settings.QUOTES_DB) as db:
        qdf = db.read("quotes", where=f"symbol IN ({ph})", params=symbols)
    for _, r in qdf.iterrows():
        t = tags[str(r["symbol"])]
        t["sector"] = r.get("sector")
        t["industry"] = r.get("industry")
    return tags


def _risk_free_asof(macro: pd.DataFrame, x_date: pd.Timestamp) -> float:
    """Same logic as analysis_layer.pipeline._risk_free, filtered to date <= X."""
    if macro.empty or "series" not in macro.columns:
        return settings.DCF_TERMINAL_GROWTH + settings.DCF_MIN_DISCOUNT_SPREAD
    s = macro[(macro["series"] == "treasury_10y") & (macro["date"] <= x_date)].sort_values("date")
    return float(s["value"].iloc[-1]) / 100 if not s.empty else 0.04


class _FinancialsStates:
    """Lazily-built, per-symbol cache of the distinct 'known as of' financials
    states (point-in-time approximation — see module docstring).

    A new quarter only becomes "known" on its own `period_end + lag` date, so
    across a ~63-trading-day quarter the applicable SymbolPeriods is IDENTICAL —
    building it fresh every day (the original approach) was pure waste. This
    reuses the SAME SymbolPeriods object across every day in a state's window,
    which is also what makes the identity-based memoization in
    `_scoped_periods_cache` below safe (see its docstring).
    """

    def __init__(self, fsym: pd.DataFrame):
        self._fsym = fsym
        self._cache: dict[int, P.SymbolPeriods] = {}
        if fsym.empty or "period_end_dt" not in fsym.columns:
            self._known_at: list[pd.Timestamp] = []
        else:
            lag = pd.Timedelta(days=settings.FINANCIALS_REPORT_LAG_DAYS)
            self._known_at = sorted((fsym["period_end_dt"] + lag).dropna().unique())

    def asof(self, x_date: pd.Timestamp) -> P.SymbolPeriods:
        idx = bisect.bisect_right(self._known_at, x_date)
        state = self._cache.get(idx)
        if state is None:
            known = (self._fsym[self._fsym["period_end_dt"] + pd.Timedelta(
                days=settings.FINANCIALS_REPORT_LAG_DAYS) <= x_date]
                if self._known_at else self._fsym)
            state = P.prepare(known)
            self._cache[idx] = state
        return state


class _ScopedPeriodsCache:
    """Memoizes analysis_layer._periods._series and metrics.split_adjust for the
    duration of one `with` block, keyed on Python object identity (id()).

    Why this is safe: every SymbolPeriods instance these calls receive comes from
    `_FinancialsStates.asof()` above, which reuses ONE object per financials state
    for the whole symbol — so id() collisions can't happen (nothing whose id is
    used as a key gets garbage-collected while this cache is alive; it's a local
    dict held by the enclosing `with` block, torn down — patch restored, cache
    dropped — before that object could be freed and its id reused). Scoped to one
    symbol (not a module-level cache) so nothing leaks across symbols or workers.

    Why it's needed at all: `metrics.compute()`/`intrinsic_value.compute()` must
    still run fresh every trading day (real price -> real P/E etc.), but the
    financials-parsing they do internally (~46 _periods calls/day, profiled at
    ~75% of this script's runtime) is IDENTICAL across every day sharing the same
    financials state — this cache eliminates exactly that redundant re-parsing
    without changing a single formula in metrics.py/_periods.py.
    """

    def __enter__(self) -> "_ScopedPeriodsCache":
        self._orig_series = P._series
        self._orig_split_adjust = metrics.split_adjust
        series_cache: dict[tuple, pd.Series] = {}
        split_cache: dict[tuple, pd.Series] = {}

        def cached_series(fin, freq, field):
            key = (id(fin), freq, field)
            if key not in series_cache:
                series_cache[key] = self._orig_series(fin, freq, field)
            return series_cache[key]

        def cached_split_adjust(s, splits):
            key = (id(s), id(splits))
            if key not in split_cache:
                split_cache[key] = self._orig_split_adjust(s, splits)
            return split_cache[key]

        P._series = cached_series
        metrics.split_adjust = cached_split_adjust
        return self

    def __exit__(self, *exc) -> None:
        P._series = self._orig_series
        metrics.split_adjust = self._orig_split_adjust


def _event_series(osym: pd.DataFrame, col: str) -> pd.Series | None:
    if col not in osym.columns:
        return None
    mask = osym[col].notna() & (osym[col] != 0) if col == "splits" else osym[col] > 0
    if not mask.any():
        return None
    return pd.Series(osym.loc[mask, col].to_numpy(), index=pd.DatetimeIndex(osym.loc[mask, "date"]))


def _fill_ttm_gaps(fsym_asof: P.SymbolPeriods, m: dict, price: float) -> None:
    """Fill every ratio metrics.compute() left blank because its trailing-4-quarter
    input had a gap (real financials.db data-completeness issue — see conversation)
    with the same ratio computed off the latest YEARLY figure instead. Every
    formula below is metrics.py's own public function/formula, called directly —
    only the profit/revenue/ebitda/gross-profit/operating-income/fcf/dividends-paid
    INPUT switches from quarterly-TTM to latest-annual when TTM wasn't available.
    `P.latest()` (balance-sheet items: equity, debt, cash, assets, liabilities)
    already falls back quarterly->annual on its own — only `P.ttm()`-fed FLOW
    items need this. Adds *_basis columns ("ttm"/"annual"/"none") identifying
    which rows were filled. Mutates `m` in place; metrics.py/_periods.py untouched.

    Affected outputs: pe, eps_ttm, roe, roa, ps, ev_revenue, ev_ebitda, p_fcf,
    net_margin, gross_margin, operating_margin, fcf_margin, debt_to_ebitda,
    altman_z, div_payout_ratio, div_coverage.
    """
    mktcap = m.get("market_cap")
    total_debt = P.latest(fsym_asof, "total_debt")
    cash = P.latest(fsym_asof, "cash_and_cash_equivalents")
    equity = P.latest(fsym_asof, "stockholders_equity")
    total_assets = P.latest(fsym_asof, "total_assets")
    total_liab = P.latest(fsym_asof, "total_liabilities_net_minority_interest")
    ev = (mktcap + total_debt - cash) if pd.notna(mktcap) else float("nan")

    def annual_last(field: str) -> float:
        s = P.annual(fsym_asof, field)
        return float(s.iloc[-1]) if len(s) else float("nan")

    def fallback(basis_key: str, needs: tuple[str, ...], ttm_val: float, annual_field: str) -> float:
        """The TRUE current figure for this row: `ttm_val` if it's actually
        available, else the latest annual one. Checked directly against
        `ttm_val` (not just "is some ratio in `needs` blank") so the basis label
        stays accurate even when a ratio is blank for an UNRELATED reason (e.g.
        `pe` blank from missing shares while `ni` itself was fine) — in that case
        this still correctly reports "ttm" and re-applying it changes nothing.
        Only actually looks up the annual figure when `ttm_val` is missing AND
        something in `needs` needs it, since annual_last() isn't free.
        """
        if pd.notna(ttm_val):
            m[basis_key] = "ttm"
            return ttm_val
        if not any(pd.isna(m.get(k)) for k in needs):
            m[basis_key] = "ttm"  # nothing downstream is blank; irrelevant which basis
            return float("nan")
        val = annual_last(annual_field)
        m[basis_key] = "annual" if pd.notna(val) else "none"
        return val

    ni = fallback("ni_basis", ("pe", "eps_ttm", "roe", "roa", "net_margin", "div_payout_ratio"),
                  P.ttm(fsym_asof, "net_income"), "net_income")
    rev = fallback("revenue_basis",
                    ("ps", "ev_revenue", "gross_margin", "operating_margin", "net_margin", "fcf_margin"),
                    P.ttm(fsym_asof, "total_revenue"), "total_revenue")
    ebitda = fallback("ebitda_basis", ("ev_ebitda", "debt_to_ebitda"),
                       P.ttm(fsym_asof, "ebitda"), "ebitda")
    gp = fallback("gp_basis", ("gross_margin",), P.ttm(fsym_asof, "gross_profit"), "gross_profit")
    ebit = fallback("ebit_basis", ("operating_margin", "altman_z"),
                     P.ttm(fsym_asof, "operating_income"), "operating_income")
    # fcf gets its own bespoke block (not the generic `fallback()`) because its
    # annual fallback isn't a plain `P.annual()` lookup — `metrics.fcf_annual()`
    # derives OCF+capex first, only falling back to the raw (sparse) reported
    # field, and that same preference needs to apply here too.
    fcf_ttm_val = metrics.fcf_ttm(fsym_asof)
    if pd.notna(fcf_ttm_val):
        fcf, fcf_basis = fcf_ttm_val, "ttm"
    elif any(pd.isna(m.get(k)) for k in ("p_fcf", "fcf_margin", "div_coverage")):
        fcf_a = metrics.fcf_annual(fsym_asof)
        fcf = float(fcf_a.iloc[-1]) if len(fcf_a) else float("nan")
        fcf_basis = "annual" if pd.notna(fcf) else "none"
    else:
        fcf, fcf_basis = float("nan"), "ttm"
    m["fcf_basis"] = fcf_basis
    div_paid_ttm = P.ttm(fsym_asof, "cash_dividends_paid")
    div_paid = fallback("div_paid_basis", ("div_payout_ratio", "div_coverage"),
                         abs(div_paid_ttm) if pd.notna(div_paid_ttm) else div_paid_ttm,
                         "cash_dividends_paid")
    if m.get("div_paid_basis") == "annual" and pd.notna(div_paid):
        div_paid = abs(div_paid)

    def have(*vals) -> bool:
        return all(v is not None and pd.notna(v) for v in vals)

    if pd.isna(m.get("pe")) and pd.notna(mktcap) and have(ni):
        m["pe"] = metrics._div(mktcap, ni)
    if pd.isna(m.get("eps_ttm")) and pd.notna(mktcap) and price and have(ni):
        m["eps_ttm"] = metrics._div(ni, metrics._div(mktcap, price))
    if pd.isna(m.get("roe")) and have(ni):
        m["roe"] = metrics.roe(ni, equity)
    if pd.isna(m.get("roa")) and have(ni):
        m["roa"] = metrics.roa(ni, total_assets)
    if pd.isna(m.get("ps")) and pd.notna(mktcap) and have(rev):
        m["ps"] = metrics._div(mktcap, rev)
    if pd.isna(m.get("ev_revenue")) and pd.notna(ev) and have(rev):
        m["ev_revenue"] = metrics._div(ev, rev)
    if pd.isna(m.get("net_margin")) and have(ni, rev):
        m["net_margin"] = metrics.net_margin(ni, rev)
    if pd.isna(m.get("gross_margin")) and have(gp, rev):
        m["gross_margin"] = metrics.gross_margin(gp, rev)
    if pd.isna(m.get("operating_margin")) and have(ebit, rev):
        m["operating_margin"] = metrics.operating_margin(ebit, rev)
    if pd.isna(m.get("fcf_margin")) and have(fcf, rev):
        m["fcf_margin"] = metrics.fcf_margin(fcf, rev)
    if pd.isna(m.get("ev_ebitda")) and pd.notna(ev) and have(ebitda):
        m["ev_ebitda"] = metrics._div(ev, ebitda)
    if pd.isna(m.get("debt_to_ebitda")) and have(ebitda):
        m["debt_to_ebitda"] = metrics.debt_to_ebitda(total_debt, ebitda)
    if pd.isna(m.get("p_fcf")) and pd.notna(mktcap) and have(fcf):
        m["p_fcf"] = metrics._div(mktcap, fcf)
    if pd.isna(m.get("altman_z")) and have(ebit, rev):
        m["altman_z"] = metrics._altman_z(fsym_asof, ebit, rev, mktcap, total_assets, total_liab)
    if pd.isna(m.get("div_payout_ratio")) and have(ni, div_paid):
        m["div_payout_ratio"] = metrics._pct(metrics._div(div_paid, ni))
    if pd.isna(m.get("div_coverage")) and have(fcf, div_paid):
        m["div_coverage"] = metrics._div(fcf, div_paid)

    # roic = NOPAT / invested_capital, NOPAT = ebit * (1 - tax_rate). `ebit`
    # already has its fallback above (ebit_basis); tax_rate needs its own two
    # TTM inputs (tax_provision, pretax_income), not used anywhere else.
    if pd.isna(m.get("roic")):
        tax_prov_ttm = P.ttm(fsym_asof, "tax_provision")
        pretax_ttm = P.ttm(fsym_asof, "pretax_income")
        if pd.notna(tax_prov_ttm) and pd.notna(pretax_ttm):
            tax_rate, m["roic_basis"] = metrics._div(tax_prov_ttm, pretax_ttm), "ttm"
        else:
            tax_prov_a, pretax_a = annual_last("tax_provision"), annual_last("pretax_income")
            tax_rate = metrics._div(tax_prov_a, pretax_a)
            m["roic_basis"] = "annual" if pd.notna(tax_rate) else "none"
        if have(ebit) and pd.notna(tax_rate):
            tax_rate = min(max(tax_rate, 0.0), 1.0)
            nopat = ebit * (1 - tax_rate)
            m["roic"] = metrics._pct(metrics._div(nopat, P.latest(fsym_asof, "invested_capital")))
    else:
        m["roic_basis"] = "ttm"

    # peg = pe / eps_cagr_3y — recomputed here because metrics.compute() built it
    # from the ORIGINAL (possibly still-blank) pe, before the pe fix above ran.
    if pd.isna(m.get("peg")):
        g3 = m.get("eps_cagr_3y")
        if pd.notna(m.get("pe")) and pd.notna(g3) and g3 > 0:
            m["peg"] = metrics._div(m["pe"], g3)


def _build_symbol(symbol: str, osym: pd.DataFrame, fsym: pd.DataFrame, macro: pd.DataFrame,
                   tag: dict, start: pd.Timestamp | None, end: pd.Timestamp | None) -> pd.DataFrame:
    """One symbol's full daily row history (technical + fundamental + intrinsic value).

    `osym`/`fsym` are already this symbol's own rows (pre-grouped by the caller —
    both the sequential and multiprocess paths dispatch per-symbol slices so a
    worker never has to pickle/filter the whole multi-symbol frame). Module-level
    so it's picklable for ProcessPoolExecutor.
    """
    if osym.empty:
        return pd.DataFrame()
    dividends = _event_series(osym, "dividends")
    splits = _event_series(osym, "splits")
    states = _FinancialsStates(fsym)

    rows: list[dict] = []
    with _ScopedPeriodsCache():
        for i in range(len(osym)):
            x_date = osym["date"].iloc[i]
            if (start is not None and x_date < start) or (end is not None and x_date > end):
                continue
            lo = max(0, i + 1 - _TECH_LOOKBACK_DAYS)
            ohlcv_asof = osym.iloc[lo : i + 1]
            price = float(ohlcv_asof["adj_close"].iloc[-1])

            fsym_asof = states.asof(x_date)
            m = metrics.compute(symbol, fsym_asof, None, price,
                                 dividends=dividends, splits=splits, as_of=x_date)
            _fill_ttm_gaps(fsym_asof, m, price)
            t = technical.compute(symbol, ohlcv_asof)
            iv = intrinsic_value.compute(symbol, fsym_asof, None, price, m,
                                          _risk_free_asof(macro, x_date))

            rows.append({
                "symbol": symbol,
                "date": x_date.strftime("%Y-%m-%d"),  # SQLite has no native datetime type
                "sector": tag.get("sector"),
                "industry": tag.get("industry"),
                "security_type": tag.get("security_type"),
                "price": price,
                **m, **t, **iv,
            })
    return pd.DataFrame(rows)


def main() -> None:
    setup_logging()
    p = argparse.ArgumentParser(
        description="Export a daily historical ML feature table for a symbol group -> ml_data.db")
    p.add_argument("--symbols-file", type=Path, help=".syms selection (e.g. selections/ml.syms)")
    p.add_argument("--symbols", type=str, help="Comma-separated symbols (alternative to --symbols-file)")
    p.add_argument("--start-date", type=str, default=None, help="YYYY-MM-DD; default = full history")
    p.add_argument("--end-date", type=str, default=None, help="YYYY-MM-DD; default = latest available")
    p.add_argument("--out", type=Path, default=settings.ML_DATA_DB, help="Output .db path")
    p.add_argument("--workers", type=int, default=min(8, os.cpu_count() or 1),
                    help="Parallel worker processes, one symbol per task (1 = sequential, "
                         "no subprocess overhead). Default: min(8, cpu count).")
    args = p.parse_args()

    symbols = _symbols_from_args(args)
    start = pd.Timestamp(args.start_date) if args.start_date else None
    end = pd.Timestamp(args.end_date) if args.end_date else None
    log.info("ML data export — %d symbols, %d worker(s) -> %s", len(symbols), args.workers, args.out)

    ohlcv = _load_ohlcv(symbols)
    fin = _load_financials(symbols)
    macro = _load_macro()
    tags = _load_static_tags(symbols)

    empty_ohlcv, empty_fin = ohlcv.iloc[0:0], fin.iloc[0:0]
    ohlcv_by = ({s: g.sort_values("date").reset_index(drop=True)
                 for s, g in ohlcv.groupby("symbol", sort=False)} if not ohlcv.empty else {})
    fin_by = ({s: g for s, g in fin.groupby("symbol", sort=False)} if not fin.empty else {})

    total_rows = 0
    with Database(args.out) as db:
        def _write(symbol: str, df: pd.DataFrame, done: int) -> None:
            nonlocal total_rows
            if df.empty:
                log.warning("ML data — %s: no OHLCV history in range, skipped", symbol)
                return
            db.upsert(TABLE, df, key=["symbol", "date"])
            total_rows += len(df)
            log.info("ML data — %d/%d %s: %d rows", done, len(symbols), symbol, len(df))

        if args.workers <= 1:
            for i, symbol in enumerate(symbols, start=1):
                df = _build_symbol(symbol, ohlcv_by.get(symbol, empty_ohlcv),
                                    fin_by.get(symbol, empty_fin), macro, tags.get(symbol, {}), start, end)
                _write(symbol, df, i)
        else:
            # Each task gets only its own symbol's slice (not the whole multi-symbol
            # frame) — keeps inter-process pickling cheap. DB writes stay in this
            # process: SQLite doesn't handle concurrent writers, so workers only
            # compute, the main process serializes every upsert as results arrive.
            with ProcessPoolExecutor(max_workers=args.workers) as ex:
                futures = {
                    ex.submit(_build_symbol, symbol, ohlcv_by.get(symbol, empty_ohlcv),
                              fin_by.get(symbol, empty_fin), macro, tags.get(symbol, {}), start, end): symbol
                    for symbol in symbols
                }
                for i, fut in enumerate(as_completed(futures), start=1):
                    _write(futures[fut], fut.result(), i)

    log.info("ML data export complete — %d rows across %d symbols -> %s",
              total_rows, len(symbols), args.out)
    print(f"Done — {total_rows} rows across {len(symbols)} symbols written to {args.out}")


if __name__ == "__main__":
    main()
