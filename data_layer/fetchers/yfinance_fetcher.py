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
from datetime import datetime, timezone
from typing import TYPE_CHECKING

import pandas as pd

from config import settings, type_map
from core.market_calendar import last_completed_session
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
    # closes (defense-in-depth behind the orchestrator's market-closed gate).
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

        hist = yf.Ticker(symbol).history(
            period=f"{settings.OHLCV_INITIAL_YEARS}y",
            auto_adjust=False,   # keep both Close and Adj Close
            actions=True,        # include Dividends + Stock Splits
        )
        rows = sanitize_ohlcv(symbol, hist)
        return rows if not rows.empty else None


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
