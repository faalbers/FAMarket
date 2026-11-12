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

CLASSIFICATION (verified against live AAPL data; tightened 2026-11-11, fix list §4/§12):
  * Flows (income/cashflow, have `start`): freq starts from DURATION — ~365d annual,
    ~90d quarterly; year-to-date (6mo/9mo) and odd spans are dropped. A ~365d span
    is NOT enough for `annual`, though: Amazon's 10-Qs report trailing-12-month
    cash flows (Apr→Mar), and JBTM's FY2013 10-K tagged FY values under wrong
    dates (Apr→Mar). Fiscal-year-end months = the month of the LATEST annual end
    in each annual-report filing (`accn`; 10-K / 20-F / 40-F, else any
    non-quarterly filing for a company with none). Then two tiers:
      1. a ~365d fact from an annual report in a fiscal-year-end month — accepted;
      2. a ~365d fact from any other non-quarterly filing in such a month (6-K
         results, 8-K recast, proxy table), or from an annual report at another
         month-end (a year-end change: Goldman Nov -> Dec 2008) — accepted only
         >= 300 days from every accepted year (`_fiscal_year_ends`).
    Quarterly reports (10-Q) never supply a year. TTM and misdated spans fail
    both tiers. Revised 2026-11-11 after the full re-fetch: annual-report-only
    lost Canadian National (6-K only), Disney/P&G recast years and Goldman's
    November years. The `frame` field was tested as an alternative and rejected:
    some of Amazon's TTM facts carry a CY frame.
  * Instants (balance sheet, no `start`): the same date appears as both a 10-Q
    comparative and a 10-K, so form/fp is unreliable FOR INSTANTS. A balance-sheet
    date is `annual` when it equals a fiscal-year-end (the accepted annual flow
    ends), else `quarterly`.
  * Facts dated after their own filing (`end` > `filed`) are dropped — debt-
    maturity amounts tagged at future dates, not reporting periods.
  * Cross-tag history: a company can switch the us-gaap tag it uses over the years
    (e.g. SalesRevenueNet -> RevenueFromContractWithCustomer...). Tags are tried in
    preference order and a later tag fills only the periods earlier tags left
    blank, so the full history survives the switch.
  * Restatements (10-K/A with revised values) -> keep the latest `filed` for money
    columns. Per-share and share-count columns keep the FIRST filed value instead
    (`_FIRST_FILED_COLS`): later filings restate them for splits, so "latest" mixes
    split bases inside one series (NVDA FY2020 0.618B first filed, 2.472B after a
    later 10-K restated it for the 2021 4:1). First-filed values all sit on the
    basis of their own filing date, so value x splits after `filed_date` = today's
    basis, exactly.
  * `filed_date` = the earliest `filed` among the facts that make up a row.
"""

from __future__ import annotations

import calendar
import re
from collections.abc import Iterator
from datetime import date, timedelta
from typing import TYPE_CHECKING

import pandas as pd
from ratelimit import limits, sleep_and_retry

from config import secrets, settings
from data_layer import fetch_status, staleness
from data_layer.cleaning_rules import clean_financials
from data_layer.fetchers.base import BaseFetcher

if TYPE_CHECKING:
    from core.database import Database

SEC_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
COMPANYFACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json"
# CIK-resolution fallbacks for tickers missing from SEC_TICKERS_URL (a curated,
# occasionally-incomplete convenience file — see EDGARFinancials._resolve_missing_ciks).
CIK_LOOKUP_URL = "https://www.sec.gov/Archives/edgar/cik-lookup-data.txt"
BROWSE_EDGAR_URL = "https://www.sec.gov/cgi-bin/browse-edgar"

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
    # Broad total first (fix list §13). The ASC 606 contract-revenue tags are a
    # SUB-LINE for REITs, insurers and banks (no lease income, premiums or interest):
    # MetLife FY2018 1.26B vs Revenues 67.9B. On 57 sample companies, matching Yahoo
    # within 2% went 149/220 -> 185/220 with Revenues first; the rest are definition
    # gaps (excise tax, bank netting), not sub-lines.
    "total_revenue": [
        "Revenues",
        "RevenueFromContractWithCustomerExcludingAssessedTax",
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
    # Weighted-average share counts (fix list §14) — Yahoo only covers ~4 recent years.
    "diluted_average_shares": ["WeightedAverageNumberOfDilutedSharesOutstanding"],
    "basic_average_shares": ["WeightedAverageNumberOfSharesOutstandingBasic"],
    # Bank total non-interest expense (fix list §23) — Yahoo returns only some of
    # its pieces. An EDGAR-only column: also written onto Yahoo-owned periods.
    "non_interest_expense": ["NoninterestExpense"],
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
    # Share counts (balance-sheet cover-page figures) — same columns yfinance's
    # OrdinarySharesNumber/ShareIssued fill, so sector_index.py's shares-outstanding
    # panel picks up EDGAR-backfilled history for free. Reported per CIK, not per
    # listed class, so a multi-class company gets the SAME count on every one of its
    # tickers — sector_index._dedupe_share_classes() is what guards against that.
    "ordinary_shares_number": ["CommonStockSharesOutstanding"],
    "share_issued": ["CommonStockSharesIssued"],
}

# EPS columns are reported in USD/shares, not USD.
_SHARE_PRICE_COLS = frozenset({"basic_eps", "diluted_eps"})
# Point-in-time share counts: subject to the isolated-spike filter.
_SHARE_COUNT_COLS = frozenset({"ordinary_shares_number", "share_issued"})
# Every column reported in the XBRL "shares" unit, not USD.
_SHARES_UNIT_COLS = _SHARE_COUNT_COLS | {"diluted_average_shares", "basic_average_shares"}
# Keep the first-filed value (see CLASSIFICATION above): later filings restate
# these for splits, which would mix split bases inside one series.
_FIRST_FILED_COLS = _SHARE_PRICE_COLS | _SHARES_UNIT_COLS
# Columns Yahoo never supplies: written onto Yahoo-owned periods too (`_write`).
_EDGAR_ONLY_COLS = ("filed_date", "non_interest_expense")
# Columns whose XBRL sign is the opposite of yfinance's convention. EDGAR reports
# cash outflows as positive payments (PaymentsToAcquire..., PaymentsOfDividends...);
# yfinance stores these cash-flow lines negative. Negate so the shared column keeps
# one consistent sign across both sources.
_NEGATE_COLS = frozenset({"capital_expenditure", "cash_dividends_paid"})
# Always stored negative: a positive raw capex is a filer sign slip (fix list §4 —
# 227 of 243 affected companies were negative in every other period).
_ALWAYS_NEGATIVE_COLS = frozenset({"capital_expenditure"})
# Forms that are an annual report. 20-F / 40-F: foreign filers reporting in US GAAP.
_ANNUAL_FORMS = ("10-K", "20-F", "40-F")
# Quarterly reports (10-Q, 10-Q/A, 10-QT): never a source of a full-year figure —
# Amazon's 10-Qs carry trailing-12-month cash flows. Every other form may be: an
# 8-K recast, a proxy's pay-vs-performance table, a foreign filer's 6-K results.
_QUARTERLY_FORMS = ("10-Q",)
# A full-year figure ending outside the fiscal-year-end months (a company that
# changed its year-end: Goldman Sachs Nov -> Dec 2008) is kept only this far from
# every regular year — a misdated span (JBTM, 3 months off) sits closer.
_MIN_YEAR_GAP_DAYS = 300

# --------------------------------------------------------------------------- #
# CIK-resolution fallback name matching (Layers 2/3 — see
# EDGARFinancials._resolve_missing_ciks). SEC_TICKERS_URL is a curated,
# occasionally-incomplete convenience file; these clean a symbol's `name` for
# matching against the broader, name-keyed CIK_LOOKUP_URL registry and against
# BROWSE_EDGAR_URL's prefix-matching company search.
# --------------------------------------------------------------------------- #
# Security-type noise Polygon appends to `name` that SEC's registries never have.
_NAME_SECURITY_SUFFIXES = (
    r"common stock", r"class [a-z] common stock", r"class [a-z] ordinary shares",
    r"ordinary shares", r"depositary shares", r"american depositary shares",
    r"[0-9.]+ percent series [a-z][^,]*", r"series [a-z] preferred stock",
    r"warrants", r"units", r"rights",
)
_NAME_SECURITY_SUFFIX_RE = re.compile(
    r"[,\s]+(" + "|".join(_NAME_SECURITY_SUFFIXES) + r")\s*\Z", re.IGNORECASE
)
# Generic legal-entity words SEC's registered name usually abbreviates (Corp not
# Corporation) — browse-edgar's company search is a prefix match against that
# abbreviated form, so a query ending in the unabbreviated word often misses.
_NAME_LEGAL_SUFFIX_RE = re.compile(
    r"[,\s]+(corporation|incorporated|limited|company)\s*\Z", re.IGNORECASE
)


def _norm_name(s: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", s.upper()) if s else ""


def _clean_security_suffix(name: str) -> str:
    """Strip ticker-metadata noise (Common Stock, Ordinary Shares, ...) that
    SEC's own company registries never carry.
    """
    name = name.replace("%", " percent ")
    prev = None
    while prev != name:
        prev = name
        name = _NAME_SECURITY_SUFFIX_RE.sub("", name).strip()
    return name


def _browse_query_name(name: str) -> str:
    """Further-trimmed name for the browse-edgar prefix search — also drops the
    generic legal-suffix word, since SEC's registered name is usually abbreviated.
    """
    name = _clean_security_suffix(name)
    prev = None
    while prev != name:
        prev = name
        name = _NAME_LEGAL_SUFFIX_RE.sub("", name).strip()
    return name


# ASC 980 "Regulated Operations" tags — only rate-regulated entities (utilities,
# some REITs/pipelines) report these at all, so presence alone is the signal;
# we never need the dollar values themselves. Checked directly against the raw
# facts (not run through INSTANT_CONCEPTS) since this is a one-time per-company
# detection, not a financials.db time series.
_REGULATORY_TAGS = (
    "RegulatoryAssets", "RegulatoryAssetsCurrent", "RegulatoryAssetsNoncurrent",
    "RegulatoryLiabilities", "RegulatoryLiabilityCurrent", "RegulatoryLiabilityNoncurrent",
)


def _has_regulatory_data(facts: dict) -> bool:
    return any(
        facts.get(tag, {}).get("units") for tag in _REGULATORY_TAGS
    )


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
    """Choose the unit series for a concept: USD/shares for EPS, shares for share
    counts, else USD."""
    if col in _SHARE_PRICE_COLS:
        return units.get("USD/shares")
    if col in _SHARES_UNIT_COLS:
        return units.get("shares")
    if "USD" in units:
        return units["USD"]
    # Fall back to the sole unit if there's exactly one (rare alt currencies).
    return next(iter(units.values())) if len(units) == 1 else None


def _merge_tags(
    facts: dict,
    col: str,
    tags: list[str],
    classify,
) -> dict[tuple[str, str], tuple[float, str, str]]:
    """Resolve one canonical column to {(period_end, freq): (value, first_filed, tag)}.

    Tags are tried in preference order; a later tag only fills (period, freq) keys
    the earlier tags didn't supply (so a mid-history tag switch keeps both eras).
    Within a single tag, the latest `filed` wins (restatements) — or the first
    `filed` for `_FIRST_FILED_COLS`. `first_filed` is always the earliest filing
    that reported the period (for `filed_date`). `classify` maps a datapoint to a
    freq label (or None to drop it). Facts dated after their own filing are dropped.
    """
    keep_first = col in _FIRST_FILED_COLS
    chosen: dict[tuple[str, str], tuple[float, str, str]] = {}
    claimed: set[tuple[str, str]] = set()
    for tag in tags:
        concept = facts.get(tag)
        if not concept:
            continue
        pts = _pick_unit(concept.get("units", {}), col)
        if not pts:
            continue
        # key -> (value, filed of that value, earliest filed for the period)
        this_tag: dict[tuple[str, str], tuple[float, str, str]] = {}
        for pt in pts:
            filed = pt.get("filed", "") or ""
            if pt.get("val") is None or (filed and pt.get("end", "") > filed):
                continue
            freq = classify(pt)
            if freq is None:
                continue
            key = (pt["end"], freq)
            prev = this_tag.get(key)
            if prev is None:
                this_tag[key] = (pt["val"], filed, filed)
                continue
            earliest = min(prev[2], filed)
            take = filed < prev[1] if keep_first else filed >= prev[1]
            this_tag[key] = (pt["val"], filed, earliest) if take else (prev[0], prev[1], earliest)
        for key, (val, _, earliest) in this_tag.items():
            if key not in claimed:  # higher-preference tag already owns it -> skip
                chosen[key] = (val, earliest, tag)
        claimed |= set(this_tag)
    return chosen


def _drop_share_spikes(
    points: dict[tuple[str, str], tuple[float, str, str]],
    spike_ratio: float = 2.0,
) -> dict[tuple[str, str], tuple[float, str, str]]:
    """Drop an isolated bad share-count point (one-off XBRL tagging noise).

    Share counts drift slowly period to period. A real split/buyback shifts the
    level and STAYS there — the point right after it sits close to ITS OWN
    successor, just far from what came before. A bad tag instead reverts right
    back next period, so it reads as far from BOTH chronological neighbors —
    that shape, not sheer size of the jump, is what marks it as noise rather
    than a real corporate action. Quarterly and annual points are merged into
    one chronological timeline first (a company's true share count doesn't
    care which report cadence sampled it).

    A rare cost: two independently-bad points close together can make the one
    good point between them look like the outlier too, dropping it along with
    the real noise. That's an acceptable trade — a dropped point just gets
    interpolated from its neighbors in sector_index.py, a small smoothing,
    versus leaving a wildly wrong share count driving the float-cap weighting
    for a whole quarter.
    """
    seq = sorted(points.items(), key=lambda kv: kv[0][0])  # by period_end
    dropped: set[tuple[str, str]] = set()
    for i in range(1, len(seq) - 1):
        key, (val, *_) = seq[i]
        _, (prev, *_) = seq[i - 1]
        _, (nxt, *_) = seq[i + 1]
        if prev <= 0 or nxt <= 0 or val <= 0:
            continue
        far_from_prev = val / prev > spike_ratio or prev / val > spike_ratio
        far_from_next = val / nxt > spike_ratio or nxt / val > spike_ratio
        if far_from_prev and far_from_next:
            dropped.add(key)
    return {k: v for k, v in points.items() if k not in dropped}


def _is_annual_form(form: str | None) -> bool:
    return bool(form) and str(form).startswith(_ANNUAL_FORMS)


def _full_year_source(form: str | None) -> bool:
    """Any filing except a quarterly report may carry a full-year figure."""
    return bool(form) and not str(form).startswith(_QUARTERLY_FORMS)


def _annual_flow_points(facts: dict) -> Iterator[dict]:
    """Every ~1-year flow fact of the mapped flow concepts, any unit."""
    for tags in FLOW_CONCEPTS.values():
        for tag in tags:
            concept = facts.get(tag)
            if not concept:
                continue
            for pts in concept.get("units", {}).values():
                for pt in pts:
                    if _flow_freq(pt.get("start"), pt.get("end")) == "annual":
                        yield pt


def _fye_months(facts: dict) -> set[int]:
    """The company's fiscal-year-end month(s): the month (snapped to month-end) of
    the LATEST annual-duration end in each annual-report filing (`accn`).

    A filing's latest annual end is its own fiscal year, never a comparative or a
    TTM figure, so a misdated or trailing-12-month fact can't add a month. A
    change of fiscal year shows up as a second month. A company with no annual
    report in XBRL (Canadian National files its results as 6-Ks; a new registrant
    has only an S-1) falls back to its other non-quarterly filings.
    """
    annual: dict[str, str] = {}
    other: dict[str, str] = {}
    for pt in _annual_flow_points(facts):
        form, accn = pt.get("form"), pt.get("accn")
        if not accn or not _full_year_source(form):
            continue
        latest = annual if _is_annual_form(form) else other
        if pt["end"] > latest.get(accn, ""):
            latest[accn] = pt["end"]
    months = set()
    for end in (annual or other).values():
        d = _to_date(end)
        if d:
            months.add(_month_end(d).month)
    return months


def _fiscal_year_ends(facts: dict, fye_months: set[int]) -> set[str]:
    """Raw end dates of every accepted full-year flow, in two tiers.

    1. **Regular:** a ~1-year fact from an annual report ending in a fiscal-year-
       end month — always accepted.
    2. **Fill-in:** a ~1-year fact from another non-quarterly filing in a fiscal-
       year-end month (a 6-K results release, an 8-K recast, a proxy table), or
       from an annual report at another month-end (a company that changed its
       year-end). Accepted, oldest first, only when it sits at least
       `_MIN_YEAR_GAP_DAYS` from every year already accepted — a merger filing
       carrying a partner's differently-dated years, or a misdated span, sits closer.

    Also labels balance-sheet (instant) dates: an instant on one of these dates is
    annual, otherwise quarterly.
    """
    regular: set[str] = set()
    fill_in: set[str] = set()
    for pt in _annual_flow_points(facts):
        form = pt.get("form")
        d = _to_date(pt.get("end"))
        if d is None or not _full_year_source(form):
            continue
        in_fye_month = _month_end(d).month in fye_months
        if in_fye_month and _is_annual_form(form):
            regular.add(pt["end"])
        elif in_fye_month or _is_annual_form(form):
            fill_in.add(pt["end"])
    accepted = [d for e in regular if (d := _to_date(e))]
    kept: set[str] = set()
    for e in sorted(fill_in - regular):
        d = _to_date(e)
        if d is not None and all(abs((d - a).days) >= _MIN_YEAR_GAP_DAYS for a in accepted):
            kept.add(e)
            accepted.append(d)
    return regular | kept


def extract_financials(symbol: str, facts: dict) -> pd.DataFrame:
    """Reshape one company's us-gaap facts into wide financials rows.

    Returns columns: symbol, period_end, freq, <canonical line items>, filed_date,
    quarter_sum_flag, is_complete, source, plus the internal `_rev_tag` (the
    revenue tag behind each row, used by `_write`'s seam test and dropped before
    the write) — one row per (period_end, freq). Empty if nothing maps.
    """
    fye_months = _fye_months(facts)
    fye = _fiscal_year_ends(facts, fye_months)

    def _flow_freq_checked(pt: dict) -> str | None:
        freq = _flow_freq(pt.get("start"), pt.get("end"))
        if freq == "annual" and not (
            _full_year_source(pt.get("form")) and pt.get("end") in fye
        ):
            return None  # TTM figure in a 10-Q, or a misdated span
        return freq

    def _instant_freq(pt: dict) -> str | None:
        if pt.get("start"):  # instants must not carry a duration
            return None
        end = pt.get("end")
        if not end:
            return None
        if end in fye:
            return "annual"
        # A fiscal-year end the flows didn't capture: an annual report's balance
        # sheet in a fiscal-year-end month. (Not every 10-K instant: a cover-page
        # share count dated weeks after year-end must not become a "year".)
        d = _to_date(end)
        if _is_annual_form(pt.get("form")) and d and _month_end(d).month in fye_months:
            return "annual"
        return "quarterly"

    # column -> {(period_end, freq): (value, first_filed, tag)}
    cols: dict[str, dict[tuple[str, str], tuple[float, str, str]]] = {}
    for col, tags in FLOW_CONCEPTS.items():
        m = _merge_tags(facts, col, tags, _flow_freq_checked)
        if m:
            cols[col] = m
    for col, tags in INSTANT_CONCEPTS.items():
        m = _merge_tags(facts, col, tags, _instant_freq)
        if col in _SHARE_COUNT_COLS:
            m = _drop_share_spikes(m)
        if m:
            cols[col] = m

    if not cols:
        return pd.DataFrame()

    # Pivot the per-column maps into one row per (period_end, freq). period_end is
    # month-end-normalized here so EDGAR shares one date grid with yfinance.
    rows: dict[tuple[str, str], dict] = {}
    for col, valmap in cols.items():
        for (end, freq), (val, filed, tag) in valmap.items():
            if col in _ALWAYS_NEGATIVE_COLS:
                val = -abs(val)
            elif col in _NEGATE_COLS:
                val = -val
            me = _month_end_str(end) or end
            row = rows.setdefault((me, freq), {"period_end": me, "freq": freq})
            row[col] = val
            if filed and (not row.get("filed_date") or filed < row["filed_date"]):
                row["filed_date"] = filed
            if col == "total_revenue":
                row["_rev_tag"] = tag

    out = pd.DataFrame(list(rows.values()))
    out.insert(0, "symbol", symbol)
    out["quarter_sum_flag"] = _quarter_sum_flags(out)
    present = [c for c in _COMPLETENESS_FIELDS if c in out.columns]
    out["is_complete"] = (
        out[present].notna().all(axis=1).astype(int) if present else 0
    )
    out["source"] = "edgar"
    out = clean_financials(out)[0]
    return out.sort_values(["freq", "period_end"]).reset_index(drop=True)


# Four quarters vs the year (fix list §4): a gap above this flags the year.
_QUARTER_SUM_TOLERANCE = 0.02


def _shift_month_end(me: str, months: int) -> str | None:
    d = _to_date(me)
    if d is None:
        return None
    y, m = divmod(d.year * 12 + d.month - 1 + months, 12)
    return date(y, m + 1, calendar.monthrange(y, m + 1)[1]).isoformat()


def _quarter_sum_flags(out: pd.DataFrame) -> pd.Series:
    """Per row: 1 when an annual row's four quarters (ending 0/3/6/9 months before
    it) sum to more than 2% away from the annual `total_revenue` or `net_income`,
    0 when they agree, NULL when fewer than four quarters exist or for quarterly rows.
    """
    # float, not object: an object column is created as TEXT in SQLite
    flags = pd.Series(float("nan"), index=out.index, dtype="float64")
    q = out[out["freq"] == "quarterly"].set_index("period_end")
    for idx, row in out[out["freq"] == "annual"].iterrows():
        ends = [_shift_month_end(row["period_end"], -k) for k in (0, 3, 6, 9)]
        checked = False
        for col in ("total_revenue", "net_income"):
            if col not in out.columns or pd.isna(row.get(col)):
                continue
            vals = [q[col].get(e) if col in q.columns and e in q.index else None for e in ends]
            if any(v is None or pd.isna(v) for v in vals):
                continue
            checked = True
            total = sum(float(v) for v in vals if v is not None)
            annual = float(row[col])
            if abs(total - annual) > _QUARTER_SUM_TOLERANCE * max(abs(annual), 1.0):
                flags[idx] = 1.0
                break
        else:
            if checked:
                flags[idx] = 0.0
    return flags


class EDGARFinancials(BaseFetcher):
    """Deep-history financials backfill from SEC company-facts (additive-only)."""

    name = "edgar_financials"
    api = "edgar"
    applies_to = ("stock", "reit", "adr")  # same scope tier as yfinance financials
    target_db = "FINANCIALS_DB"
    table = "financials"
    write_mode = "upsert"
    upsert_key = ["symbol", "period_end", "freq"]
    # Refresh cadence (2026-11-11). EDGAR began as a one-time backfill (lock
    # 36500 days); it now also supplies columns Yahoo never has (filed_date,
    # non_interest_expense), so a company is re-fetched when a new filing can
    # exist — see staleness.edgar_not_due. The lock only spaces the tries. Each
    # re-fetch replaces the company's own EDGAR rows; `_keep_unreproduced_history`
    # guards stored years against a re-pointed company record.
    lock_days = settings.EDGAR_REFRESH_LOCK_DAYS

    def __init__(self, batch_size: int | None = None):
        super().__init__(batch_size)
        self._cik: dict[str, int] | None = None  # symbol -> CIK, lazy-loaded

    def stale_symbols(self, candidates: list[str], status_db: "Database") -> set[str]:
        return staleness.financials_stale(
            candidates, fetch_status.last_fetched_dates(status_db, self.name)
        )

    def not_due_symbols(self, candidates: list[str]) -> set[str]:
        from core.database import Database

        with Database(settings.SYMBOLS_DB) as sdb:
            last = fetch_status.last_fetched_dates(sdb, self.name)
        return staleness.edgar_not_due(candidates, last)

    def run(self, symbols_df: pd.DataFrame, status_db: "Database", respect_lock: bool = True) -> dict:
        """Resolve CIKs for the due symbols up front (bulk ticker map + name-
        based fallback layers for anything still missing), then fetch normally.

        `fetch_one()` itself stays a plain dict lookup — no fallback logic
        inside the per-symbol loop. `select_due` is called here and again
        inside `super().run()`; a cheap DB read, not worth restructuring the
        base class to avoid.
        """
        due, _ = self.select_due(symbols_df, status_db, respect_lock)
        cik_map = self._cik_map()
        missing = [s for s in due if s.upper() not in cik_map]
        if missing:
            self._resolve_missing_ciks(missing)
        return super().run(symbols_df, status_db, respect_lock)

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

    def _resolve_missing_ciks(self, symbols: list[str]) -> None:
        """Layer 2 (bulk name registry) then Layer 3 (per-symbol browse-edgar)
        for symbols missing from the primary ticker map. Conservative
        throughout — only a unique single-CIK match is accepted at either
        layer; ambiguous or unmatched symbols are simply left out of
        `self._cik`, which `fetch_one()` already treats as "nothing to add".
        """
        import requests

        from core.database import Database

        assert self._cik is not None
        with Database(settings.SYMBOLS_DB) as sdb:
            placeholders = ", ".join("?" for _ in symbols)
            names = sdb.query(
                f"SELECT symbol, name FROM symbols WHERE symbol IN ({placeholders})",
                symbols,
            )
        name_by_symbol = dict(zip(names["symbol"], names["name"]))

        # Layer 2: bulk name-keyed registry — every SEC filer ever, ~1M entries,
        # one request. SEC_TICKERS_URL only covers a curated ~10k active-ticker
        # subset; this is the broader, name-only fallback.
        resp = requests.get(
            CIK_LOOKUP_URL, headers={"User-Agent": secrets.SEC_USER_AGENT}, timeout=60
        )
        resp.raise_for_status()
        registry: dict[str, set[int]] = {}
        for line in resp.text.splitlines():
            parts = line.split(":")
            if len(parts) < 3 or not parts[1].isdigit():
                continue
            registry.setdefault(_norm_name(parts[0]), set()).add(int(parts[1]))

        still_missing = []
        for sym in symbols:
            nm = name_by_symbol.get(sym)
            ciks = None
            if nm:
                ciks = registry.get(_norm_name(nm)) or registry.get(
                    _norm_name(_clean_security_suffix(nm))
                )
            if ciks and len(ciks) == 1:
                self._cik[sym.upper()] = next(iter(ciks))
            else:
                still_missing.append(sym)

        resolved_l2 = len(symbols) - len(still_missing)
        if resolved_l2:
            self.log.info(
                "EDGAR CIK fallback — %d/%d resolved via name registry",
                resolved_l2, len(symbols),
            )

        # Layer 3: browse-edgar per-symbol company search (prefix match against
        # SEC's registered, usually-abbreviated name) — one request each, so
        # only run against whatever Layer 2 couldn't resolve.
        resolved_l3 = 0
        for sym in still_missing:
            nm = name_by_symbol.get(sym)
            query = _browse_query_name(nm) if nm else sym
            try:
                cik = self._browse_edgar_lookup(query)
            except Exception as exc:  # transient SEC errors (e.g. 503) skip, not crash
                self.log.debug("‖ browse-edgar lookup failed for %s: %s", sym, exc)
                continue
            if cik is not None:
                self._cik[sym.upper()] = cik
                resolved_l3 += 1
        if resolved_l3:
            self.log.info(
                "EDGAR CIK fallback — %d/%d resolved via browse-edgar",
                resolved_l3, len(still_missing),
            )

    @sleep_and_retry
    @limits(calls=settings.RATE_LIMITS["edgar"][0], period=settings.RATE_LIMITS["edgar"][1])
    def _browse_edgar_lookup(self, query_name: str) -> int | None:
        """One browse-edgar company-name search; a CIK only for a single,
        unambiguous match — never guesses among multiple hits.
        """
        import requests

        resp = requests.get(
            BROWSE_EDGAR_URL,
            params={
                "action": "getcompany", "company": query_name, "type": "10-K",
                "dateb": "", "owner": "include", "count": "10", "output": "atom",
            },
            headers={"User-Agent": secrets.SEC_USER_AGENT},
            timeout=45,
        )
        resp.raise_for_status()
        ciks = set(re.findall(r"<cik>(\d+)</cik>", resp.text))
        return int(next(iter(ciks))) if len(ciks) == 1 else None

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
        if rows.empty:
            return None
        # Rate-regulated (ASC 980) detection — a per-COMPANY fact, not a
        # per-period one, so it rides along just long enough for _write() to
        # split it off into symbols.db; it never reaches financials.db itself.
        rows["regulatory"] = int(_has_regulatory_data(facts))
        return rows

    # -- additive-only write ------------------------------------------------ #
    def _write(self, db: "Database", rows: pd.DataFrame) -> None:
        """Write a batch: replace each company's own EDGAR rows, fill only periods
        yfinance doesn't already own, and put the EDGAR-only columns on the ones it does.

        EDGAR is a fallback and must never overwrite — or duplicate — a period
        yfinance already covers. Both sources sit on a month-end grid (EDGAR is
        normalized at extraction; yfinance already month-ends), so a plain
        (symbol, period_end, freq) match cleanly identifies the shared fiscal
        period: any incoming EDGAR period already present from a non-edgar source
        is dropped, leaving EDGAR to fill only the genuinely older years.

        Each fetched company's previous `source='edgar'` rows are deleted first, so
        a period the current rules no longer produce (a quarter-end "year", a future
        date) disappears instead of lingering — the same reasoning as OHLCV's
        replace_by. Columns Yahoo never supplies (`_EDGAR_ONLY_COLS`) are also
        written onto the Yahoo-owned periods; Yahoo's upsert only touches the
        columns it sends, so nothing overwrites them.
        """
        if rows.empty:
            return
        self._write_regulatory_flag(rows)
        fin_rows = self._keep_unreproduced_history(db, rows.drop(columns=["regulatory"]))
        if fin_rows.empty:
            return
        syms = fin_rows["symbol"].unique().tolist()
        yf_rows = self._yf_rows(db, syms)
        fin_rows = self._seam_check(fin_rows, yf_rows)
        fin_rows, owned = self._split_yf_owned(fin_rows, yf_rows)
        fin_rows = fin_rows.drop(columns=["_rev_tag"], errors="ignore")
        if db.table_exists(self.table) and "source" in db.columns(self.table):
            placeholders = ", ".join("?" for _ in syms)
            db.execute(
                f'DELETE FROM "{self.table}" WHERE source = \'edgar\' '
                f"AND symbol IN ({placeholders})",
                syms,
            )
        if not fin_rows.empty:
            super()._write(db, fin_rows)
        self._write_edgar_only(db, owned)

    # Safety net for the replace-on-fetch: a new fetch may not wipe out stored
    # fiscal years it can't reproduce. Seen 2026-11-11: the SEC re-pointed NAVI and
    # XPRO at new holding-company records with almost no history, and SUPN at
    # another company. More than this many lost calendar years -> keep the old rows.
    _GUARD_MAX_LOST_YEARS = 1
    guard_history = True

    def _keep_unreproduced_history(self, db: "Database", rows: pd.DataFrame) -> pd.DataFrame:
        """Drop the companies whose fetch would lose stored fiscal years (with
        revenue or net income), so their existing EDGAR rows stay untouched."""
        if not self.guard_history or not db.table_exists(self.table):
            return rows
        syms = rows["symbol"].unique().tolist()
        stored = db.query(
            f'SELECT symbol, substr(period_end, 1, 4) AS y FROM "{self.table}" '
            "WHERE source = 'edgar' AND freq = 'annual' "
            "AND (total_revenue IS NOT NULL OR net_income IS NOT NULL) "
            f"AND symbol IN ({', '.join('?' for _ in syms)})",
            syms,
        )
        if stored.empty:
            return rows
        real_cols = [c for c in ("total_revenue", "net_income") if c in rows.columns]
        new = rows[(rows["freq"] == "annual") & rows[real_cols].notna().any(axis=1)] if real_cols else rows.iloc[0:0]
        new_years = set(zip(new["symbol"], new["period_end"].str[:4]))
        lost = (
            stored[[(s, y) not in new_years for s, y in zip(stored["symbol"], stored["y"])]]
            .groupby("symbol").size()
        )
        guarded = lost[lost > self._GUARD_MAX_LOST_YEARS]
        if guarded.empty:
            return rows
        self.log.warning(
            "EDGAR — kept stored history for %d companies whose new fetch would lose "
            "fiscal years (company record changed?): %s",
            len(guarded), ", ".join(f"{s} ({n})" for s, n in guarded.items()),
        )
        return rows[~rows["symbol"].isin(guarded.index)]

    # Revenue seam test (fix list §13, step 3): the oldest-to-newest hand-over from
    # EDGAR to Yahoo. Revenue that far off while assets barely moved is a sub-line tag.
    _SEAM_REVENUE_RATIO = 3.0
    _SEAM_ASSETS_RATIO = 1.5

    def _yf_rows(self, db: "Database", syms: list[str]) -> pd.DataFrame:
        """Stored non-EDGAR rows for `syms` (keys + the two seam-test columns)."""
        cols = ["symbol", "period_end", "freq", "total_revenue", "total_assets"]
        if not db.table_exists(self.table):
            return pd.DataFrame(columns=cols)
        existing = set(db.columns(self.table))
        sel = ", ".join(c if c in existing else f"NULL AS {c}" for c in cols)
        where = "source IS NULL OR source != 'edgar'" if "source" in existing else "1=1"
        placeholders = ", ".join("?" for _ in syms)
        return db.query(
            f'SELECT {sel} FROM "{self.table}" WHERE symbol IN ({placeholders}) AND ({where})',
            syms,
        )

    def _seam_check(self, rows: pd.DataFrame, yf_rows: pd.DataFrame) -> pd.DataFrame:
        """NULL a company's EDGAR revenue from one tag when its last EDGAR year before
        Yahoo's first year is > 3x off Yahoo's revenue while assets moved < 1.5x."""
        if "total_revenue" not in rows.columns or "_rev_tag" not in rows.columns or yf_rows.empty:
            return rows
        yf_ann = yf_rows[(yf_rows["freq"] == "annual")
                         & yf_rows["total_revenue"].gt(0) & yf_rows["total_assets"].gt(0)]
        first_yf = yf_ann.sort_values("period_end").groupby("symbol").first()
        nulled = 0
        for sym, yf in first_yf.iterrows():
            ed = rows[(rows["symbol"] == sym) & (rows["freq"] == "annual")
                      & (rows["period_end"] < yf["period_end"])
                      & rows["total_revenue"].gt(0)]
            if "total_assets" in rows.columns:
                ed = ed[ed["total_assets"].gt(0)]
            else:
                continue
            if ed.empty:
                continue
            last = ed.sort_values("period_end").iloc[-1]
            rev_ratio = last["total_revenue"] / yf["total_revenue"]
            assets_ratio = last["total_assets"] / yf["total_assets"]
            rev_off = rev_ratio > self._SEAM_REVENUE_RATIO or rev_ratio < 1 / self._SEAM_REVENUE_RATIO
            assets_close = 1 / self._SEAM_ASSETS_RATIO <= assets_ratio <= self._SEAM_ASSETS_RATIO
            if rev_off and assets_close:
                hit = (rows["symbol"] == sym) & (rows["_rev_tag"] == last["_rev_tag"])
                rows.loc[hit, "total_revenue"] = None
                nulled += 1
        if nulled:
            self.log.info("EDGAR — revenue seam test nulled a sub-line revenue tag for %d companies", nulled)
        return rows

    def _write_regulatory_flag(self, rows: pd.DataFrame) -> None:
        """Set each symbol's rate-regulated status in symbols.db — set once,
        never needs refreshing (it's a structural fact, not a time series), and
        living on the per-company table means a new yfinance financials row
        next year has nothing to do with it.
        """
        from core.database import Database

        flags = rows.groupby("symbol", as_index=False)["regulatory"].max()
        with Database(settings.SYMBOLS_DB) as sdb:
            sdb.upsert("symbols", flags, "symbol")

    def _split_yf_owned(
        self, rows: pd.DataFrame, yf_rows: pd.DataFrame
    ) -> tuple[pd.DataFrame, pd.DataFrame]:
        """(periods yfinance doesn't own, Yahoo-owned matches with the STORED key).

        The second frame carries `_EDGAR_ONLY_COLS` keyed by the stored yfinance
        `period_end`, ready for `_write_edgar_only`.
        """
        if yf_rows.empty:
            return rows, pd.DataFrame()
        # Month-end normalize the stored side too, so a yfinance row that isn't
        # already a clean month-end still lines up with EDGAR's normalized grid.
        owned = {
            (s, _month_end_str(pe), fr): pe
            for s, pe, fr in zip(yf_rows["symbol"], yf_rows["period_end"], yf_rows["freq"])
        }
        stored_pe = [
            owned.get((s, pe, fr))
            for s, pe, fr in zip(rows["symbol"], rows["period_end"], rows["freq"])
        ]
        is_owned = pd.Series([pe is not None for pe in stored_pe], index=rows.index)
        kept = rows[~is_owned]
        matched = rows[is_owned].copy()
        matched["period_end"] = [pe for pe in stored_pe if pe is not None]
        if len(matched):
            self.log.info(
                "EDGAR — kept %d new periods, skipped %d already owned by yfinance",
                len(kept), len(matched),
            )
        return kept, matched

    def _write_edgar_only(self, db: "Database", owned: pd.DataFrame) -> None:
        """Fill `_EDGAR_ONLY_COLS` on Yahoo-owned periods (only non-NULL values)."""
        cols = [c for c in _EDGAR_ONLY_COLS if c in owned.columns]
        if owned.empty or not cols:
            return
        key = list(self.upsert_key)
        for col in cols:
            part = owned.loc[owned[col].notna(), key + [col]]
            if not part.empty:
                db.upsert(self.table, part, key=key)
