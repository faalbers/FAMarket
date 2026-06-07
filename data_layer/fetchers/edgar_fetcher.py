"""
SEC EDGAR financials fetcher (Topic 3.1) — deep-history fallback for financial
statements. Free, no key (descriptive User-Agent only), SEC fair-access ~10 req/s.

WHY this exists: yfinance only exposes a rolling ~5-year / ~7-quarter window of
financials (see the [[yfinance-financials-depth-and-drift]] note). EDGAR's XBRL
company-facts API serves every figure a company has ever reported in a 10-K/10-Q,
often back 15+ years. This fetcher backfills that history into the SAME
`financials` table, in the SAME canonical columns yfinance uses, so the analysis
layer reads one unified statement history.

ADDITIVE-ONLY (the safety property): EDGAR is a *fallback*, never an override. The
write drops any incoming period already owned by yfinance and only inserts periods
yfinance doesn't have — mirroring the symbol-discovery rule that gap-fill sources
are additive and never clobber an authoritative source. A `source="edgar"` column
marks provenance; yfinance rows keep `source` NULL.

SHAPE: companyfacts JSON is `facts.us-gaap.<Concept>.units.<unit>[ {start, end,
val, fy, fp, form, filed, frame}, ... ]`. We map a curated set of us-gaap concepts
to yfinance-style snake_case columns and reshape to one wide row per
(symbol, period_end, freq).

CLASSIFICATION (verified against live AAPL data):
  * Flows (income/cashflow, have `start`): the same value is tagged inconsistently
    across fp/form, so freq is decided by DURATION — ~365d annual, ~90d quarterly;
    year-to-date (6mo/9mo) and odd spans are dropped.
  * Instants (balance sheet, no `start`): the same date appears as both a 10-Q
    comparative and a 10-K, so form/fp is unreliable. A balance-sheet date is
    `annual` when it equals a fiscal-year-end (derived from the annual flows),
    else `quarterly`.
  * Cross-tag history: a company can switch the us-gaap tag it uses over the years
    (e.g. SalesRevenueNet -> RevenueFromContractWithCustomer...). Tags are tried in
    preference order and a later tag fills only the periods earlier tags left
    blank, so the full history survives the switch.
  * Restatements (10-K/A with revised values) -> keep the latest `filed`.
"""

from __future__ import annotations

import calendar
from datetime import date, timedelta
from typing import TYPE_CHECKING

import pandas as pd

from config import secrets, settings
from data_layer.fetchers.base import BaseFetcher

if TYPE_CHECKING:
    from core.database import Database

SEC_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
COMPANYFACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json"

# Duration windows (days) for flow concepts. A genuine annual span is ~365 days
# and a fiscal quarter ~91; the slack absorbs 52/53-week fiscal calendars and the
# occasional short/long stub period. Anything else (YTD 6mo/9mo, transition
# periods) is intentionally dropped.
_ANNUAL_DAYS = (330, 400)
_QUARTER_DAYS = (80, 100)

# Mirrors yfinance's completeness rule so both sources agree on the flag.
_COMPLETENESS_FIELDS = ("total_revenue", "net_income")

# --------------------------------------------------------------------------- #
# Concept maps: canonical column -> ordered us-gaap tag preference list.
# Column names match yfinance's _norm_col output so values share one column.
# Earlier tags win; a later tag only fills a (period, freq) the earlier left blank.
# --------------------------------------------------------------------------- #
FLOW_CONCEPTS: dict[str, list[str]] = {
    "total_revenue": [
        "RevenueFromContractWithCustomerExcludingAssessedTax",
        "Revenues",
        "RevenueFromContractWithCustomerIncludingAssessedTax",
        "SalesRevenueNet",
    ],
    "cost_of_revenue": [
        "CostOfRevenue",
        "CostOfGoodsAndServicesSold",
        "CostOfGoodsSold",
    ],
    "gross_profit": ["GrossProfit"],
    "operating_income": ["OperatingIncomeLoss"],
    "pretax_income": [
        "IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest",
        "IncomeLossFromContinuingOperationsBeforeIncomeTaxesMinorityInterestAndIncomeLossFromEquityMethodInvestments",
    ],
    "tax_provision": ["IncomeTaxExpenseBenefit"],
    "net_income": ["NetIncomeLoss", "ProfitLoss"],
    "research_and_development": ["ResearchAndDevelopmentExpense"],
    "interest_expense": ["InterestExpense", "InterestExpenseNonoperating"],
    "basic_eps": ["EarningsPerShareBasic"],
    "diluted_eps": ["EarningsPerShareDiluted"],
    "operating_cash_flow": [
        "NetCashProvidedByUsedInOperatingActivities",
        "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations",
    ],
    "capital_expenditure": [
        "PaymentsToAcquirePropertyPlantAndEquipment",
        "PaymentsToAcquireProductiveAssets",
    ],
    "investing_cash_flow": ["NetCashProvidedByUsedInInvestingActivities"],
    "financing_cash_flow": ["NetCashProvidedByUsedInFinancingActivities"],
    "cash_dividends_paid": ["PaymentsOfDividendsCommonStock", "PaymentsOfDividends"],
}

INSTANT_CONCEPTS: dict[str, list[str]] = {
    "total_assets": ["Assets"],
    "current_assets": ["AssetsCurrent"],
    "total_liabilities_net_minority_interest": ["Liabilities"],
    "current_liabilities": ["LiabilitiesCurrent"],
    "stockholders_equity": [
        "StockholdersEquity",
        "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest",
    ],
    "cash_and_cash_equivalents": ["CashAndCashEquivalentsAtCarryingValue"],
    "retained_earnings": ["RetainedEarningsAccumulatedDeficit"],
    "inventory": ["InventoryNet"],
    "long_term_debt": ["LongTermDebtNoncurrent", "LongTermDebt"],
}

# EPS columns are reported in USD/shares, not USD.
_SHARE_PRICE_COLS = frozenset({"basic_eps", "diluted_eps"})
# Columns whose XBRL sign is the opposite of yfinance's convention. EDGAR reports
# cash outflows as positive payments (PaymentsToAcquire..., PaymentsOfDividends...);
# yfinance stores these cash-flow lines negative. Negate so the shared column keeps
# one consistent sign across both sources.
_NEGATE_COLS = frozenset({"capital_expenditure", "cash_dividends_paid"})


def _to_date(s: str | None) -> date | None:
    try:
        return date.fromisoformat(s) if s else None
    except (TypeError, ValueError):
        return None


def _month_end(d: date) -> date:
    """Snap a date to the NEAREST calendar month-end.

    yfinance reports period_end as a fiscal month-end (e.g. AAPL FY -> 09-30)
    while EDGAR keeps the true 52/53-week date (09-24, or occasionally a day or
    two into the next month). Snapping EDGAR to the nearest month-end puts both
    sources on one uniform grid, so the same fiscal period keys and dedupes
    exactly. "Nearest" (not just this month's end) sends a period that closes in
    the first days of a month back to the prior month-end, matching yfinance.
    """
    last = calendar.monthrange(d.year, d.month)[1]
    cur_end = date(d.year, d.month, last)
    prev_end = date(d.year, d.month, 1) - timedelta(days=1)
    return cur_end if (cur_end - d).days <= (d - prev_end).days else prev_end


def _month_end_str(s: str | None) -> str | None:
    """Month-end normalize an ISO date string; pass through if unparseable."""
    d = _to_date(s)
    return _month_end(d).isoformat() if d else s


def _flow_freq(start: str | None, end: str | None) -> str | None:
    """Annual/quarterly from a flow concept's reported duration, else None."""
    d0, d1 = _to_date(start), _to_date(end)
    if d0 is None or d1 is None:
        return None
    dur = (d1 - d0).days
    if _ANNUAL_DAYS[0] <= dur <= _ANNUAL_DAYS[1]:
        return "annual"
    if _QUARTER_DAYS[0] <= dur <= _QUARTER_DAYS[1]:
        return "quarterly"
    return None


def _pick_unit(units: dict, col: str) -> list | None:
    """Choose the unit series for a concept: USD/shares for EPS, else USD."""
    if col in _SHARE_PRICE_COLS:
        return units.get("USD/shares")
    if "USD" in units:
        return units["USD"]
    # Fall back to the sole unit if there's exactly one (rare alt currencies).
    return next(iter(units.values())) if len(units) == 1 else None


def _merge_tags(
    facts: dict,
    col: str,
    tags: list[str],
    classify,
) -> dict[tuple[str, str], float]:
    """Resolve one canonical column to {(period_end, freq): value}.

    Tags are tried in preference order; a later tag only fills (period, freq) keys
    the earlier tags didn't supply (so a mid-history tag switch keeps both eras).
    Within a single tag, the latest `filed` wins (restatements). `classify` maps a
    datapoint to a freq label (or None to drop it).
    """
    chosen: dict[tuple[str, str], tuple[float, str]] = {}
    claimed: set[tuple[str, str]] = set()
    for tag in tags:
        concept = facts.get(tag)
        if not concept:
            continue
        pts = _pick_unit(concept.get("units", {}), col)
        if not pts:
            continue
        this_tag: dict[tuple[str, str], tuple[float, str]] = {}
        for pt in pts:
            freq = classify(pt)
            if freq is None or pt.get("val") is None:
                continue
            key = (pt["end"], freq)
            filed = pt.get("filed", "") or ""
            if key not in this_tag or filed >= this_tag[key][1]:
                this_tag[key] = (pt["val"], filed)
        for key, valfiled in this_tag.items():
            if key not in claimed:  # higher-preference tag already owns it -> skip
                chosen[key] = valfiled
        claimed |= set(this_tag)
    return {k: v[0] for k, v in chosen.items()}


def _fiscal_year_ends(facts: dict) -> set[str]:
    """Set of fiscal-year-end dates, taken from every annual flow period seen.

    Used to label balance-sheet (instant) dates: an instant on a fiscal-year-end
    is annual, otherwise quarterly.
    """
    fye: set[str] = set()
    for tags in FLOW_CONCEPTS.values():
        for tag in tags:
            concept = facts.get(tag)
            if not concept:
                continue
            pts = _pick_unit(concept.get("units", {}), "")
            for pt in pts or []:
                if _flow_freq(pt.get("start"), pt.get("end")) == "annual":
                    fye.add(pt["end"])
    return fye


def extract_financials(symbol: str, facts: dict) -> pd.DataFrame:
    """Reshape one company's us-gaap facts into wide financials rows.

    Returns columns: symbol, period_end, freq, <canonical line items>,
    is_complete, source — one row per (period_end, freq). Empty if nothing maps.
    """
    fye = _fiscal_year_ends(facts)

    def _instant_freq(pt: dict) -> str | None:
        if pt.get("start"):  # instants must not carry a duration
            return None
        end = pt.get("end")
        if not end:
            return None
        if end in fye:
            return "annual"
        form, fp = pt.get("form", "") or "", pt.get("fp", "") or ""
        if "10-K" in form or fp == "FY":  # FYE the flows didn't capture
            return "annual"
        return "quarterly"

    # column -> {(period_end, freq): value}
    cols: dict[str, dict[tuple[str, str], float]] = {}
    for col, tags in FLOW_CONCEPTS.items():
        m = _merge_tags(facts, col, tags, lambda pt: _flow_freq(pt.get("start"), pt.get("end")))
        if m:
            cols[col] = m
    for col, tags in INSTANT_CONCEPTS.items():
        m = _merge_tags(facts, col, tags, _instant_freq)
        if m:
            cols[col] = m

    if not cols:
        return pd.DataFrame()

    # Pivot the per-column maps into one row per (period_end, freq). period_end is
    # month-end-normalized here so EDGAR shares one date grid with yfinance.
    rows: dict[tuple[str, str], dict] = {}
    for col, valmap in cols.items():
        sign = -1 if col in _NEGATE_COLS else 1
        for (end, freq), val in valmap.items():
            me = _month_end_str(end)
            row = rows.setdefault((me, freq), {"period_end": me, "freq": freq})
            row[col] = sign * val

    out = pd.DataFrame(list(rows.values()))
    out.insert(0, "symbol", symbol)
    present = [c for c in _COMPLETENESS_FIELDS if c in out.columns]
    out["is_complete"] = (
        out[present].notna().all(axis=1).astype(int) if present else 0
    )
    out["source"] = "edgar"
    return out.sort_values(["freq", "period_end"]).reset_index(drop=True)


class EDGARFinancials(BaseFetcher):
    """Deep-history financials backfill from SEC company-facts (additive-only)."""

    name = "edgar_financials"
    api = "edgar"
    applies_to = ("stock", "reit", "adr")  # same scope tier as yfinance financials
    target_db = "FINANCIALS_DB"
    table = "financials"
    write_mode = "upsert"
    upsert_key = ["symbol", "period_end", "freq"]
    # One-time backfill: a company's filed history is static, so once a symbol's
    # deep history is in place there's nothing to re-pull. yfinance keeps the recent
    # window fresh weekly; EDGAR only fills the old periods, once. A ~100-year lock
    # means a succeeded symbol is skipped on every future run; an errored symbol
    # (timestamp stays NULL) still retries, and respect_lock=False forces a refetch.
    lock_days = 36500

    def __init__(self, batch_size: int | None = None):
        super().__init__(batch_size)
        self._cik: dict[str, int] | None = None  # symbol -> CIK, lazy-loaded

    # -- CIK resolution ----------------------------------------------------- #
    def _cik_map(self) -> dict[str, int]:
        """symbol -> CIK from SEC company_tickers.json (cached for the run).

        SEC tickers already use the hyphenated convention that is our canonical
        `symbol`, so they key directly. A CIK can own several tickers; each ticker
        maps to one CIK, which is the direction we need.
        """
        if self._cik is None:
            import requests

            resp = requests.get(
                SEC_TICKERS_URL,
                headers={"User-Agent": secrets.SEC_USER_AGENT},
                timeout=30,
            )
            resp.raise_for_status()
            self._cik = {
                rec["ticker"].strip().upper(): int(rec["cik_str"])
                for rec in resp.json().values()
                if rec.get("ticker") and rec.get("cik_str") is not None
            }
            self.log.info("EDGAR CIK map — %d tickers", len(self._cik))
        return self._cik

    def fetch_one(self, symbol: str) -> pd.DataFrame | None:
        import requests

        cik = self._cik_map().get(symbol.upper())
        if cik is None:  # not an SEC domestic filer (e.g. many ADRs) -> nothing to add
            return None

        resp = requests.get(
            COMPANYFACTS_URL.format(cik=cik),
            headers={"User-Agent": secrets.SEC_USER_AGENT},
            timeout=60,
        )
        if resp.status_code == 404:  # filer exists but has no XBRL facts
            return None
        resp.raise_for_status()
        facts = resp.json().get("facts", {}).get("us-gaap", {})
        if not facts:
            return None
        rows = extract_financials(symbol, facts)
        return rows if not rows.empty else None

    # -- additive-only write ------------------------------------------------ #
    def _write(self, db: "Database", rows: pd.DataFrame) -> None:
        """Insert only periods yfinance doesn't already own.

        EDGAR is a fallback and must never overwrite — or duplicate — a period
        yfinance already covers. Both sources sit on a month-end grid (EDGAR is
        normalized at extraction; yfinance already month-ends), so a plain
        (symbol, period_end, freq) match cleanly identifies the shared fiscal
        period: any incoming EDGAR period already present from a non-edgar source
        is dropped, leaving EDGAR to fill only the genuinely older years. Rows
        EDGAR itself previously wrote (source='edgar') don't block and may be
        refreshed.
        """
        if rows.empty:
            return
        rows = self._drop_yf_owned(db, rows)
        if rows.empty:
            return
        super()._write(db, rows)

    def _drop_yf_owned(self, db: "Database", rows: pd.DataFrame) -> pd.DataFrame:
        if not db.table_exists(self.table):
            return rows
        syms = rows["symbol"].unique().tolist()
        placeholders = ", ".join("?" for _ in syms)
        has_source = "source" in db.columns(self.table)
        sel = "symbol, period_end, freq" + (", source" if has_source else "")
        stored = db.query(
            f'SELECT {sel} FROM "{self.table}" WHERE symbol IN ({placeholders})',
            syms,
        )
        if stored.empty:
            return rows
        if has_source:
            stored = stored[stored["source"].ne("edgar")]  # yfinance-owned only
        if stored.empty:
            return rows

        # Month-end normalize the stored side too, so a yfinance row that isn't
        # already a clean month-end still lines up with EDGAR's normalized grid.
        owned = {
            (s, _month_end_str(pe), fr)
            for s, pe, fr in zip(stored["symbol"], stored["period_end"], stored["freq"])
        }
        keep = [
            (s, pe, fr) not in owned
            for s, pe, fr in zip(rows["symbol"], rows["period_end"], rows["freq"])
        ]
        kept = rows[keep]
        dropped = len(rows) - len(kept)
        if dropped:
            self.log.info(
                "EDGAR — kept %d new periods, skipped %d already owned by yfinance",
                len(kept), dropped,
            )
        return kept
