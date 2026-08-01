"""
yfinance fetchers (Topic 3.1) — primary source for quotes, OHLCV (+dividends),
and (next) financials.

Decisions realized here:
  * history() returns the Dividends column alongside OHLCV — dividend history comes
    for free in the same call, no separate dividend API.
  * Every scalar field from yfinance `info` is stored as its own column; the schema
    grows dynamically via the Database wrapper. Non-scalar fields (lists/dicts like
    companyOfficers) are dropped during sanitize.
  * quoteType is captured so the symbol layer can resolve security_type for
    EDGAR-sourced symbols that came in without a type.
  * All prices stored unadjusted AND adjusted; analysis uses adj_close.

Sanitize (Topic 9.4) is two-level:
  * field-level fix  — inf/NaN -> None, "N/A"/""/"None" strings -> None
  * record-level reject — no usable price -> skip the whole symbol (return None)

OHLCV write mode (confirmed): upsert keyed on (symbol, date), not raw append. Same
end state as "append new dates" but idempotent, so a re-run or an overlapping fetch
can't create duplicate rows.

PHASE 2 OPTIMIZATION (decided, deferred): OHLCV stays a per-symbol loop in Phase 1
so it shares the one rate-limit / retry / fetch_status model in base.py. yfinance's
yf.download() is just a threaded loop over Ticker().history() (no Yahoo-side batch
endpoint), so its internal threads bypass our @limits throttle entirely and risk
429/IP-block. Switching OHLCV to chunked yf.download (capped threads, per-chunk
pacing, reshape-to-long) belongs in Phase 2 alongside the multiprocessing work —
same controlled-concurrency problem.
"""

from __future__ import annotations

import math
import re
from datetime import datetime, timezone
from typing import TYPE_CHECKING

import pandas as pd

from config import settings, type_map
from core.database import Database
from core.market_calendar import last_completed_session, session_count
from data_layer import staleness
from data_layer.fetchers.base import BaseFetcher

if TYPE_CHECKING:
    from core.database import Database


def _is_scalar(v) -> bool:
    return v is None or isinstance(v, (int, float, str, bool))


def _clean_value(v):
    """Field-level sanitize: normalize bad values to None."""
    if isinstance(v, float) and (math.isinf(v) or math.isnan(v)):
        return None
    if isinstance(v, str):
        s = v.strip()
        if s in ("", "N/A", "n/a", "None", "nan", "NaN", "-"):
            return None
        return s
    return v


# --------------------------------------------------------------------------- #
# Quotes
# --------------------------------------------------------------------------- #
def sanitize_quote(symbol: str, info: dict) -> dict | None:
    """Sanitize a yfinance info dict into one quote row, or None to reject."""
    if not info:
        return None
    price = (
        info.get("currentPrice")
        or info.get("regularMarketPrice")
        or info.get("previousClose")
    )
    if not price:  # record-level reject: no usable price
        return None

    row = {k: _clean_value(v) for k, v in info.items() if _is_scalar(v)}
    row["symbol"] = symbol
    row["quote_type"] = info.get("quoteType")
    row["security_type_yf"] = type_map.normalize_type(info.get("quoteType"))
    row["fetched_at"] = datetime.now(timezone.utc).isoformat()
    return row


class YFinanceQuotes(BaseFetcher):
    name = "yfinance_quotes"
    api = "yfinance"
    applies_to = ()  # all security types get a quote
    target_db = "QUOTES_DB"
    table = "quotes"
    write_mode = "upsert"
    upsert_key = "symbol"

    def fetch_one(self, symbol: str) -> pd.DataFrame | None:
        import yfinance as yf

        ticker = yf.Ticker(symbol)
        info = ticker.info or {}
        row = sanitize_quote(symbol, info)
        if row is None:
            return None
        df = pd.DataFrame([row])
        return _enrich_quote(symbol, df, info, ticker)


# yfinance quoteTypes that expose a `funds_data` block (ETFs and mutual funds).
_FUND_QUOTE_TYPES = {"ETF", "MUTUALFUND"}


def _enrich_quote(symbol: str, df: pd.DataFrame, info: dict, ticker) -> pd.DataFrame:
    """Conditional enrichment by security type (Topic 9.4, ROADMAP 9.4).

    Funds (ETF + mutual fund) carry metadata beyond the plain `info` dict:
      * scalar overview fields that already live on `info` (totalAssets, navPrice…)
      * `funds_data.fund_overview` — category / family / legal type — flattened to
        one `fund_<key>` column each (no JSON blobs, per the one-field-one-column
        convention). NULL for every non-fund symbol.

    Best-effort by design: a funds_data failure never rejects the already-valid
    quote — it's retried on the next weekly run.
    """
    qtype = (info.get("quoteType") or "").upper()
    if qtype not in _FUND_QUOTE_TYPES:
        return df

    # Overview scalars carried on `info` (now applied to mutual funds too, not just ETFs).
    for col in ("totalAssets", "navPrice", "fundFamily", "category"):
        if col in info:
            df.loc[:, col] = _clean_value(info.get(col))

    # funds_data.fund_overview — a small flat dict; one sanitized column per key.
    for key, value in _fund_overview(ticker).items():
        df.loc[:, f"fund_{key}"] = _clean_value(value)
    return df


def _fund_overview(ticker) -> dict:
    """`ticker.funds_data.fund_overview` as a flat scalar dict, or {} on any failure.

    A second Yahoo request beyond `.info`, so it's issued only for fund quote types.
    Swallows all exceptions (missing funds_data attr, no data, or a transient Yahoo
    error) so enrichment degrades gracefully without failing the whole quote.
    """
    try:
        overview = ticker.funds_data.fund_overview
    except Exception:
        return {}
    if not isinstance(overview, dict):
        return {}
    return {k: v for k, v in overview.items() if _is_scalar(v)}


# --------------------------------------------------------------------------- #
# OHLCV (+ dividends)
# --------------------------------------------------------------------------- #
def sanitize_ohlcv(symbol: str, hist: pd.DataFrame) -> pd.DataFrame:
    """Reshape a yfinance history() frame into clean OHLCV rows."""
    if hist is None or hist.empty:
        return pd.DataFrame()
    df = hist.reset_index().rename(
        columns={
            "Date": "date",
            "Open": "open",
            "High": "high",
            "Low": "low",
            "Close": "close",
            "Adj Close": "adj_close",
            "Volume": "volume",
            "Dividends": "dividends",
            "Stock Splits": "splits",
        }
    )
    df["date"] = pd.to_datetime(df["date"], utc=True).dt.strftime("%Y-%m-%d")
    df = df.dropna(subset=["close"])          # record-level: rows without a close
    df = df[df["close"] > 0]
    # Completed-session gate: yfinance only stamps a day-date, so a bar fetched
    # mid-session (or before Yahoo finalizes the close) would be stored as that
    # day's *closing* price and never corrected until the symbol is re-fetched
    # after close — poisoning the analysis run in between. Drop any bar dated past
    # the last fully-settled session so the OHLCV store only ever holds final
    # closes. This is the SOLE intraday guard for OHLCV (the fetch pipeline itself
    # runs any time): while the market is open the last completed session is the
    # prior day, so today's in-progress bar is omitted here.
    cutoff = last_completed_session()
    if cutoff is not None:
        df = df[df["date"] <= cutoff.strftime("%Y-%m-%d")]
    df.insert(0, "symbol", symbol)
    keep = [
        "symbol", "date", "open", "high", "low", "close",
        "adj_close", "volume", "dividends", "splits",
    ]
    return df[[c for c in keep if c in df.columns]]


class YFinanceOHLCV(BaseFetcher):
    name = "yfinance_ohlcv"
    api = "yfinance"
    # Price history applies to traded securities plus mutual funds: yfinance
    # returns a daily NAV-strike series for funds (Open=High=Low=Close, Volume=0),
    # which is the fund's equivalent of a price history for returns/volatility.
    applies_to = (
        "stock", "etf", "reit", "adr", "closed_end_fund", "preferred", "index",
        "mutual_fund",
    )
    target_db = "OHLCV_DB"
    table = "ohlcv"
    write_mode = "upsert"          # idempotent by (symbol, date) — see module note
    upsert_key = ["symbol", "date"]
    stale_after_days = settings.OHLCV_STALE_WEEKS * 7  # base.stale_symbols by `date`

    def fetch_one(self, symbol: str) -> pd.DataFrame | None:
        import yfinance as yf

        # Per-symbol, must be cleared before each call so a previous symbol's
        # flag can't leak onto a clean one — read by base.py's per-symbol loop
        # right after this call returns.
        self._pending_flag: dict | None = None

        hist = yf.Ticker(symbol).history(
            period=f"{settings.OHLCV_INITIAL_YEARS}y",
            auto_adjust=False,   # keep both Close and Adj Close
            actions=True,        # include Dividends + Stock Splits
        )
        rows = sanitize_ohlcv(symbol, hist)
        if rows.empty:
            return None
        self._check_coverage(symbol, rows, self._val_db)
        return rows

    def _check_coverage(self, symbol: str, rows: pd.DataFrame, db: Database) -> None:
        """Flag a fetch that returned far fewer sessions than the symbol's own
        stored history implies it should have (the Yahoo silent-truncation bug —
        see dev_docs/Yfinance_History_Truncation_Issue.md). Accept-and-flag: the
        rows are still written as normal (harmless, upsert never deletes). A flag
        is surfaced three ways: a debug line now, a run-summary WARNING at the
        end of `run()`, and — via self._pending_flag, read by base.py right
        after fetch_one returns — persisted onto this symbol's fetch_status row
        (coverage_actual/coverage_expected/coverage_checked_at) for durable,
        queryable history (see fetch_status.coverage_flags()).

        Skipped when there's no existing stored data for the symbol — a
        first-ever fetch has no baseline to compare against.
        """
        if not settings.OHLCV_VALIDITY_CHECK_ENABLED:
            return
        existing = db.query('SELECT MIN(date) AS min_date FROM "ohlcv" WHERE symbol = ?', [symbol])
        min_date = existing["min_date"].iloc[0] if not existing.empty else None
        if min_date is None or pd.isna(min_date):
            return  # no baseline yet

        cutoff = last_completed_session()
        if cutoff is None:
            return
        floor = cutoff - pd.DateOffset(years=settings.OHLCV_INITIAL_YEARS)
        expected_start = max(pd.Timestamp(min_date), floor)
        expected = session_count(expected_start, cutoff)
        if expected <= 0:
            return

        actual = len(rows)
        coverage = actual / expected
        if coverage < settings.OHLCV_VALIDITY_MIN_COVERAGE_PCT:
            self._flagged.append({"symbol": symbol, "actual": actual, "expected": expected})
            self._pending_flag = {"actual": actual, "expected": expected}
            self.log.debug(
                "‖ %s coverage %.0f%% (%d/%d expected sessions since %s) — possible truncation",
                symbol, coverage * 100, actual, expected, expected_start.date(),
            )

    def run(
        self, symbols_df: pd.DataFrame, status_db: Database, respect_lock: bool = True
    ) -> dict:
        self._flagged: list[dict] = []
        self._val_db = Database(settings.OHLCV_DB)
        try:
            result = super().run(symbols_df, status_db, respect_lock)
        finally:
            self._val_db.close()
        if self._flagged:
            sample = ", ".join(
                f"{f['symbol']} ({f['actual']}/{f['expected']})" for f in self._flagged[:20]
            )
            self.log.warning(
                "Coverage check — %d symbol(s) returned far fewer sessions than expected "
                "(possible Yahoo truncation, see dev_docs/Yfinance_History_Truncation_Issue.md): %s%s",
                len(self._flagged), sample, ", ..." if len(self._flagged) > 20 else "",
            )
        return result


# --------------------------------------------------------------------------- #
# Financial statements (annual + quarterly)
# --------------------------------------------------------------------------- #
def _norm_col(name: str) -> str:
    """Normalize a yfinance line-item label to a snake_case column name."""
    if name in ("symbol", "period_end", "freq", "is_complete"):
        return name
    out = []
    for ch in str(name).strip():
        out.append(ch.lower() if (ch.isalnum()) else "_")
    col = "".join(out)
    while "__" in col:
        col = col.replace("__", "_")
    return col.strip("_")


# First-pass completeness definition (Topic 2.2 deferred this to coding phase):
# a period is "complete" when the core income figures are present. Tunable later.
_COMPLETENESS_FIELDS = ("total_revenue", "net_income")

# Period-end drift detector. Financials upsert on (symbol, period_end, freq), so a
# period_end that yfinance shifts by a few days (a fiscal-calendar change or a
# vendor revision) inserts a NEW row instead of updating the old one — leaving two
# near-duplicate periods. There's no safe automatic answer to "which date is
# canonical", so we surface it as a WARNING for manual review rather than merge.
# A genuine adjacent period is ~90 days (quarterly) / ~365 (annual) away, so any
# gap at/under this many days between two stored periods is almost certainly drift.
_PERIOD_DRIFT_DAYS = 20


def sanitize_financials(
    symbol: str,
    annual: tuple,
    quarterly: tuple,
) -> pd.DataFrame:
    """Merge income/balance/cashflow per period into wide rows, one per period.

    `annual` / `quarterly` are (income_stmt, balance_sheet, cashflow) tuples of
    yfinance DataFrames (line items x period-end dates). Each is transposed so
    period-ends become rows, then the three statements are joined on period-end.
    """
    frames: list[pd.DataFrame] = []
    for freq, statements in (("annual", annual), ("quarterly", quarterly)):
        parts = [
            d.T for d in statements
            if d is not None and not d.empty
        ]
        if not parts:
            continue
        merged = pd.concat(parts, axis=1)
        merged = merged.loc[:, ~merged.columns.duplicated()]  # drop repeated line items
        merged = merged.reset_index().rename(columns={"index": "period_end"})
        merged["period_end"] = pd.to_datetime(
            merged["period_end"], errors="coerce"
        ).dt.strftime("%Y-%m-%d")
        merged = merged.dropna(subset=["period_end"])
        merged.insert(0, "symbol", symbol)
        merged.insert(2, "freq", freq)
        frames.append(merged)

    if not frames:
        return pd.DataFrame()

    out = pd.concat(frames, ignore_index=True)
    out.columns = [_norm_col(c) for c in out.columns]
    out = out.loc[:, ~pd.Index(out.columns).duplicated()]

    # field-level sanitize on numeric line items
    for col in out.columns:
        if col in ("symbol", "period_end", "freq"):
            continue
        out[col] = pd.to_numeric(out[col], errors="coerce")
        out[col] = out[col].where(out[col].notna(), None)

    out = out.copy()  # defragment after per-column edits (silences PerformanceWarning)
    present = [c for c in _COMPLETENESS_FIELDS if c in out.columns]
    if present:
        out["is_complete"] = out[present].notna().all(axis=1).astype(int)
    else:
        out["is_complete"] = 0
    # Explicit provenance. yfinance is the primary financials source; EDGAR backfill
    # rows carry source="edgar". Set after the numeric sanitize loop so this text
    # column isn't coerced to NaN. (Added here, not in the loop above, for that
    # reason.)
    out["source"] = "yfinance"
    return out


class YFinanceFinancials(BaseFetcher):
    name = "yfinance_financials"
    api = "yfinance"
    applies_to = ("stock", "reit", "adr")  # financials only for these (Topic 3.3)
    target_db = "FINANCIALS_DB"
    table = "financials"
    write_mode = "upsert"
    upsert_key = ["symbol", "period_end", "freq"]

    def stale_symbols(self, candidates: list[str]) -> set[str]:
        return staleness.financials_stale(candidates)

    def not_due_symbols(self, candidates: list[str]) -> set[str]:
        # Statements appear ~4x/year; defer symbols whose next filing can't
        # exist yet (cycle + FINANCIALS_REPORT_LAG_DAYS). Trade-off: mid-cycle
        # restatements wait for the next due window.
        return staleness.financials_not_due(candidates)

    def fetch_one(self, symbol: str) -> pd.DataFrame | None:
        import yfinance as yf

        t = yf.Ticker(symbol)
        annual = (t.income_stmt, t.balance_sheet, t.cashflow)
        quarterly = (
            t.quarterly_income_stmt,
            t.quarterly_balance_sheet,
            t.quarterly_cashflow,
        )
        rows = sanitize_financials(symbol, annual, quarterly)
        return rows if not rows.empty else None

    def _write(self, db: Database, rows: pd.DataFrame) -> None:
        self._warn_period_drift(db, rows)
        super()._write(db, rows)

    def _warn_period_drift(self, db: Database, rows: pd.DataFrame) -> None:
        """WARN when two periods for the same (symbol, freq) fall within
        `_PERIOD_DRIFT_DAYS` of each other — the upsert-duplicate situation that
        arises when yfinance shifts a reported period_end. Checks the union of
        what's already stored and what's incoming, so it keeps flagging the
        duplicate on every run until it is resolved by hand.
        """
        if rows.empty:
            return
        syms = rows["symbol"].unique().tolist()
        if db.table_exists(self.table):
            placeholders = ", ".join("?" for _ in syms)
            stored = db.query(
                f'SELECT symbol, period_end, freq FROM "{self.table}" '
                f"WHERE symbol IN ({placeholders})",
                syms,
            )
        else:
            stored = pd.DataFrame(columns=["symbol", "period_end", "freq"])

        combined = pd.concat(
            [stored, rows[["symbol", "period_end", "freq"]]], ignore_index=True
        ).drop_duplicates(subset=["symbol", "period_end", "freq"])
        combined["_pe"] = pd.to_datetime(combined["period_end"], errors="coerce")
        combined = combined.dropna(subset=["_pe"])

        for (sym, freq), grp in combined.groupby(["symbol", "freq"]):
            grp = grp.sort_values("_pe")
            dates = grp["_pe"].tolist()
            labels = grp["period_end"].tolist()
            for i in range(1, len(dates)):
                gap = (dates[i] - dates[i - 1]).days
                if 1 <= gap <= _PERIOD_DRIFT_DAYS:
                    self.log.warning(
                        "Period-end drift — %s %s has near-duplicate periods %s and "
                        "%s (%d days apart); upsert keeps both, manual review needed",
                        sym, freq, labels[i - 1], labels[i], gap,
                    )


# --------------------------------------------------------------------------- #
# Analyst estimates (Topic 3.3 — forward-looking consensus)
# --------------------------------------------------------------------------- #
# Five yfinance Ticker properties, all derived from the one cached `earningsTrend`
# module (so reading all five is ~one network fetch), share a forward-HORIZON index
# (0q / +1q / 0y / +1y, plus LTG only on growth_estimates) — NOT a reported
# period_end. We store them tidy/long, one row per (symbol, horizon), each re-fetch
# replacing the row in place (no dated history: eps_trend already carries the
# 7/30/60/90-days-ago values within a single snapshot).
#
# Each property maps its yfinance columns to a prefixed snake_case column EXPLICITLY
# (not via a generic normalizer): eps_revisions mixes casings — `upLast7days` (lower
# d) vs `downLast7Days` (capital D) — that a normalizer would silently mishandle.
_EE_COLS = {  # earnings_estimate
    "avg": "eps_avg", "low": "eps_low", "high": "eps_high",
    "yearAgoEps": "eps_year_ago", "numberOfAnalysts": "eps_num_analysts",
    "growth": "eps_growth",
}
_RE_COLS = {  # revenue_estimate
    "avg": "rev_avg", "low": "rev_low", "high": "rev_high",
    "numberOfAnalysts": "rev_num_analysts", "yearAgoRevenue": "rev_year_ago",
    "growth": "rev_growth",
}
_GE_COLS = {  # growth_estimates (the only property that fills the LTG horizon)
    "stockTrend": "growth_stock_trend", "indexTrend": "growth_index_trend",
}
_ET_COLS = {  # eps_trend — the estimate's own revision history, in one snapshot
    "current": "eps_trend_current", "7daysAgo": "eps_trend_7d",
    "30daysAgo": "eps_trend_30d", "60daysAgo": "eps_trend_60d",
    "90daysAgo": "eps_trend_90d",
}
_ER_COLS = {  # eps_revisions — note the casing quirk on downLast7Days
    "upLast7days": "eps_rev_up_7d", "upLast30days": "eps_rev_up_30d",
    "downLast30days": "eps_rev_down_30d", "downLast7Days": "eps_rev_down_7d",
}

# Final column order for the stored frame (keys, then each property group, then
# bookkeeping). currency rides on every property except growth_estimates.
_ESTIMATE_COLUMNS = [
    "symbol", "horizon",
    *_EE_COLS.values(), *_RE_COLS.values(), *_GE_COLS.values(),
    *_ET_COLS.values(), *_ER_COLS.values(),
    "currency", "fetched_at",
]


def sanitize_estimates(symbol, earnings, revenue, growth, trend, revisions) -> pd.DataFrame:
    """Merge the five horizon-indexed estimate frames into tidy (symbol, horizon) rows.

    Each input is a yfinance DataFrame (or None/empty). Horizons are unioned across
    all five so LTG (growth_estimates only) is included; a horizon row with no metric
    value at all is dropped. Numerics are coerced (bad -> NaN -> stored NULL). Empty
    DataFrame when nothing usable survives -> base class records no-data.
    """
    sources = [
        (earnings, _EE_COLS), (revenue, _RE_COLS), (growth, _GE_COLS),
        (trend, _ET_COLS), (revisions, _ER_COLS),
    ]
    cells: dict[str, dict] = {}      # horizon -> {dest_col: value}
    currency: dict[str, str] = {}    # horizon -> currency (first source carrying it)
    for df, mapping in sources:
        if df is None or not hasattr(df, "empty") or df.empty:
            continue
        for horizon in df.index:
            dest = cells.setdefault(str(horizon), {})
            for src_col, dst_col in mapping.items():
                if src_col in df.columns:
                    dest[dst_col] = pd.to_numeric(df.at[horizon, src_col], errors="coerce")
            if "currency" in df.columns and str(horizon) not in currency:
                cv = _clean_value(df.at[horizon, "currency"])
                if cv:
                    currency[str(horizon)] = cv

    fetched = datetime.now(timezone.utc).isoformat()
    out_rows = []
    for horizon, vals in cells.items():
        if not any(pd.notna(v) for v in vals.values()):  # record-level reject
            continue
        out_rows.append({"symbol": symbol, "horizon": horizon, **vals,
                         "currency": currency.get(horizon), "fetched_at": fetched})
    if not out_rows:
        return pd.DataFrame()
    out = pd.DataFrame(out_rows)
    return out[[c for c in _ESTIMATE_COLUMNS if c in out.columns]]


def _estimate_frame(ticker, prop: str):
    """One yfinance Ticker property as a DataFrame, or None on any failure.

    Best-effort per property (mirrors `_fund_overview`): a single property can raise
    or come back unpopulated, and that must not reject the whole symbol. Used for
    every DataFrame-valued property this fetcher reads (the estimate frames, plus
    earnings_history / insider_purchases / major_holders).
    """
    try:
        df = getattr(ticker, prop)
    except Exception:
        return None
    if df is None or not hasattr(df, "empty") or df.empty:
        return None
    return df


def _safe_calendar(ticker):
    """`Ticker.calendar` (a dict of next-event dates) or None on any failure."""
    try:
        cal = ticker.calendar
    except Exception:
        return None
    return cal if isinstance(cal, dict) else None


def _as_iso_date(v):
    """A date / datetime / Timestamp -> 'YYYY-MM-DD' string, else None."""
    if v is None:
        return None
    try:
        ts = pd.Timestamp(v)
    except Exception:
        return None
    if pd.isna(ts):
        return None
    return ts.date().isoformat()


# --------------------------------------------------------------------------- #
# Earnings-surprise history (one cached `earningsHistory` request)
# --------------------------------------------------------------------------- #
# Tidy/long, one row per reported quarter. Stored RAW (surprise_percent is the
# yfinance fraction, e.g. 0.10; the analysis layer converts to a percent number and
# derives avg / beat-streak — calculations belong there, not here).
_SURPRISE_COLS = [
    "symbol", "period_end", "eps_estimate", "eps_actual",
    "eps_difference", "surprise_percent", "fetched_at",
]


def sanitize_earnings_surprise(symbol: str, eh) -> pd.DataFrame:
    """`earnings_history` -> tidy (symbol, period_end) rows. Empty -> no rows."""
    if eh is None or not hasattr(eh, "empty") or eh.empty:
        return pd.DataFrame()
    fetched = datetime.now(timezone.utc).isoformat()
    rows = []
    for q in eh.index:
        period = _as_iso_date(q)
        if not period:
            continue
        rows.append({
            "symbol": symbol,
            "period_end": period,
            "eps_estimate": pd.to_numeric(eh.at[q, "epsEstimate"], errors="coerce")
                if "epsEstimate" in eh.columns else float("nan"),
            "eps_actual": pd.to_numeric(eh.at[q, "epsActual"], errors="coerce")
                if "epsActual" in eh.columns else float("nan"),
            "eps_difference": pd.to_numeric(eh.at[q, "epsDifference"], errors="coerce")
                if "epsDifference" in eh.columns else float("nan"),
            "surprise_percent": pd.to_numeric(eh.at[q, "surprisePercent"], errors="coerce")
                if "surprisePercent" in eh.columns else float("nan"),
            "fetched_at": fetched,
        })
    if not rows:
        return pd.DataFrame()
    out = pd.DataFrame(rows)
    return out[[c for c in _SURPRISE_COLS if c in out.columns]]


# --------------------------------------------------------------------------- #
# Ownership / events snapshot (calendar + Holders request)
# --------------------------------------------------------------------------- #
# One row per symbol. calendar = next-event dates; insider_purchases
# (netSharePurchaseActivity) + major_holders both come from the single Holders
# request. Ownership PERCENTAGES (heldPercentInsiders / heldPercentInstitutions)
# already ride in quotes.db `info`, so we keep only the new institutionsCount here.
_INSIDER_SHARES = {  # netSharePurchaseActivity label -> "Shares" column dest
    "Purchases": "insider_buy_shares",
    "Sales": "insider_sell_shares",
    "Net Shares Purchased (Sold)": "insider_net_shares",
    "Total Insider Shares Held": "insider_total_held",
    "% Net Shares Purchased (Sold)": "insider_pct_net",
    "% Buy Shares": "insider_buy_pct",
    "% Sell Shares": "insider_sell_pct",
}
_INSIDER_TRANS = {  # netSharePurchaseActivity label -> "Trans" column dest
    "Purchases": "insider_buy_trans",
    "Sales": "insider_sell_trans",
    "Net Shares Purchased (Sold)": "insider_net_trans",
}
_OWNERSHIP_COLS = [
    "symbol", "next_earnings_date", "ex_dividend_date", "dividend_date",
    "insider_buy_shares", "insider_sell_shares", "insider_net_shares",
    "insider_total_held", "insider_pct_net", "insider_buy_pct", "insider_sell_pct",
    "insider_buy_trans", "insider_sell_trans", "insider_net_trans",
    "insider_period_months", "institutions_count", "fetched_at",
]


def sanitize_ownership(symbol: str, calendar, insider, major) -> pd.DataFrame:
    """calendar + insider_purchases + major_holders -> one snapshot row. Empty when
    nothing usable survives (record-level reject)."""
    vals: dict = {}

    if isinstance(calendar, dict):
        ed = calendar.get("Earnings Date")
        if isinstance(ed, (list, tuple)) and ed:
            vals["next_earnings_date"] = _as_iso_date(min(ed))
        elif ed:
            vals["next_earnings_date"] = _as_iso_date(ed)
        vals["ex_dividend_date"] = _as_iso_date(calendar.get("Ex-Dividend Date"))
        vals["dividend_date"] = _as_iso_date(calendar.get("Dividend Date"))

    if insider is not None and hasattr(insider, "empty") and not insider.empty:
        label_col = insider.columns[0]  # header carries the window, e.g. "...Last 6m"
        m = re.search(r"(\d+)\s*m", str(label_col))
        if m:
            vals["insider_period_months"] = int(m.group(1))
        for _, r in insider.iterrows():
            label = str(r[label_col]).strip()
            if label in _INSIDER_SHARES and "Shares" in insider.columns:
                vals[_INSIDER_SHARES[label]] = pd.to_numeric(r["Shares"], errors="coerce")
            if label in _INSIDER_TRANS and "Trans" in insider.columns:
                vals[_INSIDER_TRANS[label]] = pd.to_numeric(r["Trans"], errors="coerce")

    if major is not None and hasattr(major, "empty") and not major.empty:
        col = major.columns[0]
        if "institutionsCount" in major.index:
            vals["institutions_count"] = pd.to_numeric(
                major.at["institutionsCount", col], errors="coerce")

    meaningful = any(
        (isinstance(v, str) and v) or (not isinstance(v, str) and pd.notna(v))
        for v in vals.values()
    )
    if not meaningful:
        return pd.DataFrame()

    vals["symbol"] = symbol
    vals["fetched_at"] = datetime.now(timezone.utc).isoformat()
    out = pd.DataFrame([vals])
    return out[[c for c in _OWNERSHIP_COLS if c in out.columns]]


class YFinanceSignals(BaseFetcher):
    """Per-symbol forward / ownership signals — read off ONE Ticker, written RAW.

    Folds three yfinance request groups into one fetch_one (one throttle pass):
      * estimates       — earningsTrend + growth_estimates (existing)   -> `estimates`
      * earnings_history — earningsHistory                              -> `earnings_surprise`
      * calendar + Holders — calendarEvents + the 7-module Holders req  -> `ownership`
    ~5 Yahoo requests/symbol (under the Financials ~6 envelope — see
    dev_docs/yfinance_request_groups.md). Each property is best-effort; one missing
    property never rejects the symbol. fetch_one returns one frame tagged by
    `__table__`; `_write` splits it to the three tables (each its own key) in
    signals.db. Calculations are deferred to the analysis layer (raw storage).
    """

    name = "yfinance_signals"
    api = "yfinance"
    applies_to = ("stock", "reit", "adr")  # these signals exist only for operating cos.
    target_db = "SIGNALS_DB"
    table = "estimates"          # primary; `_write` fans out per `__table__`
    write_mode = "upsert"
    upsert_key = ["symbol", "horizon"]
    # Standard weekly lock (inherits FETCH_LOCK_DAYS); no not_due/stale overrides —
    # this data revises continuously (no filing cycle) and the snapshot is replaced
    # in place, so no-coverage names self-abandon via the no-data strike counter.

    _TABLE_KEYS = {
        "estimates": ["symbol", "horizon"],
        "earnings_surprise": ["symbol", "period_end"],
        "ownership": "symbol",
    }

    def fetch_one(self, symbol: str) -> pd.DataFrame | None:
        import yfinance as yf

        t = yf.Ticker(symbol)
        estimates = sanitize_estimates(
            symbol,
            _estimate_frame(t, "earnings_estimate"),
            _estimate_frame(t, "revenue_estimate"),
            _estimate_frame(t, "growth_estimates"),
            _estimate_frame(t, "eps_trend"),
            _estimate_frame(t, "eps_revisions"),
        )
        surprise = sanitize_earnings_surprise(
            symbol, _estimate_frame(t, "earnings_history")
        )
        ownership = sanitize_ownership(
            symbol,
            _safe_calendar(t),
            _estimate_frame(t, "insider_purchases"),
            _estimate_frame(t, "major_holders"),
        )

        frames = []
        for tbl, df in (
            ("estimates", estimates),
            ("earnings_surprise", surprise),
            ("ownership", ownership),
        ):
            if df is not None and not df.empty:
                df = df.copy()
                df["__table__"] = tbl
                frames.append(df)
        if not frames:
            return None
        return pd.concat(frames, ignore_index=True)

    def _write(self, db: Database, rows: pd.DataFrame) -> None:
        """Fan the tagged frame out to its tables, each with its own upsert key.

        The base run() concats per-symbol frames (union columns, NaN-filled) before
        calling _write; we split back on `__table__` and drop the all-NaN columns a
        table didn't fill, so each table keeps only its own schema (added dynamically
        on first write). One signals.db connection — no base-loop change needed.
        """
        if rows is None or rows.empty or "__table__" not in rows.columns:
            return
        for tbl, sub in rows.groupby("__table__"):
            sub = sub.drop(columns="__table__").dropna(axis=1, how="all")
            if sub.empty:
                continue
            db.upsert(str(tbl), sub, key=self._TABLE_KEYS[str(tbl)])
