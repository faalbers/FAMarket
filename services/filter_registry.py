"""
Filter parameter registry (Topic 5).

The Filter page's headline behaviour — "metrics show/hide automatically by security
type" — is backed by this registry. It is the single source of truth for:

  * which screenable **security types / sub-types** exist (the selector), and how a
    stored row maps onto one (`classify`), since analysis.db carries only
    `security_type` + `sector` + `industry` (no `sub_type` column, and REITs ride
    as `security_type='stock'` with `sector='Real Estate'`);
  * the ~30 **base metrics**, their category, and the set of types each is
    *meaningful* for (strict mode: a metric is hidden for a type where it misleads —
    e.g. EV/EBITDA for a bank, P/E for a REIT). Decided topic-by-topic with the user;
    see the `filter-page-param-applicability` memory note for the full rationale.

Two **variant axes** ride on the bases instead of being enumerated as separate
entries (Option B — base + modifier UI):

  * **growth** — the four statement line-items (revenue/eps/fcf/book_value) each
    expand to 7 columns (`_cagr_1y/3y/5y`, `_yoy_q`, `_growth_vol/r2/cv`);
  * **peer-relative** — `_vs_sector` / `_vs_industry`, offered for a concrete column
    only when that column actually exists in analysis.db (data-driven, so we never
    offer a peer toggle for a metric the analysis layer didn't compute one for).

Applicability is decided once per base; the variants inherit it. Adding a metric to
analysis.db later (e.g. FFO — see `deferred-metrics-to-compute`) means adding one
entry here, not touching the page.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache

from config import settings
from config.param_hints import PARAM_HINTS
from core.database import Database
# Canonical screen-type keys + the sector/industry classifier live in the analysis
# layer (it computes the stored `screen_type` column). The Filter UI re-exports them
# and uses `classify` only as a fallback for an analysis.db that predates the column.
from analysis_layer.screen_type import (  # noqa: F401  (re-exported for the page)
    STANDARD, BANK, INSURANCE, REIT, ETF, CEF, MUTUAL_FUND, PREFERRED, MINIMAL,
    classify,
)

# --------------------------------------------------------------------------- #
# Screen types — the selector labels/help (keys come from analysis_layer).
# --------------------------------------------------------------------------- #
# Ordered for the selector checklist, with hover help (2-4 plain sentences).
SCREEN_TYPES: dict[str, dict[str, str]] = {
    STANDARD: {
        "label": "Common Stock — Standard",
        "help": "Ordinary operating companies that make or sell things (and standard "
        "ADRs). The full metric set applies: valuation, profitability, debt, growth, "
        "income, technicals, intrinsic value.",
    },
    BANK: {
        "label": "Common Stock — Bank / Financial",
        "help": "Banks and lenders. Their economics are inverted — deposits/debt are "
        "the raw material — so sales-, cash-flow- and EV-based ratios and the standard "
        "debt/liquidity ratios are hidden; ROE/ROA, P/B and book-value growth lead.",
    },
    INSURANCE: {
        "label": "Common Stock — Insurance",
        "help": "Insurers. Like banks, judged on ROE/ROA, P/B and book-value growth; "
        "margins, EV multiples and the standard debt/liquidity ratios don't apply.",
    },
    REIT: {
        "label": "REIT (Real Estate)",
        "help": "Real-estate trusts. GAAP depreciation crushes their earnings, so "
        "earnings-based metrics (P/E, margins, DCF) are hidden; they're screened on "
        "P/B, leverage, revenue growth and dividends. (Detected from sector/industry.)",
    },
    ETF: {
        "label": "ETF",
        "help": "Exchange-traded funds. Not operating businesses, so company metrics "
        "don't apply — only price/technicals, yield and RS rank exist today. "
        "(Expense ratio, AUM, etc. are planned — see deferred metrics.)",
    },
    CEF: {
        "label": "Closed-End Fund",
        "help": "Closed-end funds. Same as ETFs for now — technicals + yield. Their "
        "signature metric (premium/discount to NAV) is a planned addition.",
    },
    MUTUAL_FUND: {
        "label": "Mutual Fund",
        "help": "Mutual funds price once daily as a flat NAV (no intraday range or "
        "volume), so volume- and range-based technicals (volume, ATR) are hidden; "
        "moving averages, RSI, MACD, Bollinger and yield still apply.",
    },
    PREFERRED: {
        "label": "Preferred Stock",
        "help": "Preferred shares exist for fixed income — only yield and dividend "
        "rate apply (the payment doesn't grow), plus technicals.",
    },
    MINIMAL: {
        "label": "Other (SPAC / Warrant / Unit / Index)",
        "help": "Minimal-data instruments — only price and technical chart metrics "
        "apply; they have no fundamentals.",
    },
}

# Convenience groupings used in the applicability sets below.
COMPANY = frozenset({STANDARD, BANK, INSURANCE, REIT})
FINANCIALS = frozenset({BANK, INSURANCE})
ALL_TRADED = frozenset(
    {STANDARD, BANK, INSURANCE, REIT, ETF, CEF, MUTUAL_FUND, PREFERRED, MINIMAL}
)
TRADED_NO_MF = ALL_TRADED - {MUTUAL_FUND}
DIVIDEND_PAYERS = frozenset(
    {STANDARD, BANK, INSURANCE, REIT, ETF, CEF, MUTUAL_FUND, PREFERRED}
)
FUND_DIV = frozenset({STANDARD, BANK, INSURANCE, REIT, ETF, CEF, MUTUAL_FUND})  # no preferred


# --------------------------------------------------------------------------- #
# Base metrics — the dropdown entries (Option B). Variants ride on top.
# --------------------------------------------------------------------------- #
# Growth suffix -> human label, in display order. A growth base expands to these.
GROWTH_WINDOWS: dict[str, str] = {
    "cagr_1y": "1Y CAGR",
    "cagr_3y": "3Y CAGR",
    "cagr_5y": "5Y CAGR",
    "yoy_q": "YoY (latest quarter)",
    "growth_vol": "Growth volatility",
    "growth_r2": "Growth trend R²",
    "growth_cv": "Growth variability (CV)",
}


@dataclass(frozen=True)
class Base:
    key: str                      # analysis.db column (or growth/peer stem)
    name: str                     # short UI label
    category: str
    applies: frozenset            # screen types this metric is meaningful for
    growth: bool = False          # expands to the GROWTH_WINDOWS columns
    unit: str = field(default="")  # from param_hints when known


def _unit(key: str) -> str:
    h = PARAM_HINTS.get(key)
    return h.get("unit", "") if h else ""


def _b(key, name, category, applies, growth=False) -> Base:
    return Base(key, name, category, applies, growth, _unit(key))


# Categories in display order.
CATEGORY_ORDER = [
    "Price", "Size", "Valuation", "Profitability", "Balance Sheet", "Growth", "Estimates",
    "Earnings", "Ownership", "Income", "Technical", "Intrinsic Value", "Relative Strength",
    "Score", "Classification",
]

# Fund types — for classification bases that only apply to funds.
FUNDS = frozenset({ETF, CEF, MUTUAL_FUND})

BASES: list[Base] = [
    # -- Price -------------------------------------------------------------- #
    _b("price", "Price", "Price", ALL_TRADED),
    # -- Size --------------------------------------------------------------- #
    _b("market_cap", "Market cap", "Size", COMPANY),
    # -- Valuation ---------------------------------------------------------- #
    _b("pe", "P/E", "Valuation", frozenset({STANDARD, BANK, INSURANCE})),
    _b("forward_pe", "Forward P/E", "Valuation", frozenset({STANDARD, BANK, INSURANCE})),
    _b("peg", "PEG", "Valuation", frozenset({STANDARD, BANK, INSURANCE})),
    _b("pb", "P/B", "Valuation", frozenset({STANDARD, BANK, INSURANCE, REIT})),
    _b("ps", "P/S", "Valuation", frozenset({STANDARD})),
    _b("p_fcf", "P/FCF", "Valuation", frozenset({STANDARD})),
    _b("ev_ebitda", "EV/EBITDA", "Valuation", frozenset({STANDARD})),
    _b("ev_revenue", "EV/Revenue", "Valuation", frozenset({STANDARD})),
    _b("eps_ttm", "EPS (TTM)", "Valuation", frozenset({STANDARD, BANK, INSURANCE})),
    # -- Profitability ------------------------------------------------------ #
    _b("roe", "ROE", "Profitability", frozenset({STANDARD, BANK, INSURANCE})),
    _b("roa", "ROA", "Profitability", frozenset({STANDARD, BANK, INSURANCE})),
    _b("roe_roa_gap", "ROE-ROA gap", "Profitability", frozenset({STANDARD, BANK, INSURANCE})),
    _b("asset_turnover", "Asset turnover", "Profitability", frozenset({STANDARD, BANK, INSURANCE})),
    _b("equity_multiplier", "Equity multiplier", "Profitability", frozenset({STANDARD, BANK, INSURANCE})),
    _b("roic", "ROIC", "Profitability", frozenset({STANDARD})),
    _b("wacc", "WACC", "Profitability", frozenset({STANDARD})),
    _b("roic_vs_wacc", "ROIC - WACC", "Profitability", frozenset({STANDARD})),
    _b("gross_margin", "Gross margin", "Profitability", frozenset({STANDARD})),
    _b("operating_margin", "Operating margin", "Profitability", frozenset({STANDARD})),
    _b("net_margin", "Net margin", "Profitability", frozenset({STANDARD, BANK, INSURANCE})),
    _b("fcf_margin", "FCF margin", "Profitability", frozenset({STANDARD})),
    _b("ocf_to_ni_3y", "Cash conversion (3y)", "Profitability", frozenset({STANDARD})),
    _b("ocf_to_ni_5y", "Cash conversion (5y)", "Profitability", frozenset({STANDARD})),
    _b("gross_margin_trend_3y", "Gross margin trend (3y)", "Profitability", frozenset({STANDARD})),
    _b("operating_margin_trend_3y", "Operating margin trend (3y)", "Profitability", frozenset({STANDARD})),
    # -- Balance sheet ------------------------------------------------------ #
    _b("debt_to_equity", "Debt/Equity", "Balance Sheet", frozenset({STANDARD, REIT})),
    _b("debt_to_ebitda", "Debt/EBITDA", "Balance Sheet", frozenset({STANDARD, REIT})),
    _b("interest_coverage", "Interest coverage", "Balance Sheet", frozenset({STANDARD, REIT})),
    _b("current_ratio", "Current ratio", "Balance Sheet", frozenset({STANDARD})),
    _b("quick_ratio", "Quick ratio", "Balance Sheet", frozenset({STANDARD})),
    _b("cash_ratio", "Cash ratio", "Balance Sheet", frozenset({STANDARD})),
    _b("altman_z", "Altman Z", "Balance Sheet", frozenset({STANDARD})),
    _b("beneish_m_score", "Beneish M", "Balance Sheet", frozenset({STANDARD})),
    _b("quick_health_score", "Quick health", "Balance Sheet", frozenset({STANDARD})),
    # -- Growth (base + window) --------------------------------------------- #
    _b("revenue", "Revenue growth", "Growth", frozenset({STANDARD, BANK, INSURANCE, REIT}), growth=True),
    _b("eps", "EPS growth", "Growth", frozenset({STANDARD, BANK, INSURANCE}), growth=True),
    _b("fcf", "FCF growth", "Growth", frozenset({STANDARD}), growth=True),
    _b("book_value", "Book-value growth", "Growth", frozenset({STANDARD, BANK, INSURANCE}), growth=True),
    _b("revenue_accel", "Revenue acceleration", "Growth", frozenset({STANDARD, BANK, INSURANCE, REIT})),
    _b("eps_accel", "EPS acceleration", "Growth", frozenset({STANDARD, BANK, INSURANCE})),
    _b("share_count_chg_1y", "Share count change (1y)", "Growth", frozenset({STANDARD, BANK, INSURANCE})),
    # -- Estimates (forward analyst data; only fetched for stock/reit/adr) --- #
    _b("forward_eps_growth", "Forward EPS growth", "Estimates", COMPANY),
    _b("forward_rev_growth", "Forward revenue growth", "Estimates", COMPANY),
    _b("forward_peg", "Forward PEG", "Estimates", COMPANY),
    _b("eps_revision_1m", "EPS revision (1m)", "Estimates", COMPANY),
    _b("eps_revision_3m", "EPS revision (3m)", "Estimates", COMPANY),
    _b("eps_revision_breadth", "EPS revision breadth", "Estimates", COMPANY),
    _b("analyst_count", "Analyst count", "Estimates", COMPANY),
    # -- Earnings (surprise history + next earnings date; stock/reit/adr only) - #
    _b("earnings_surprise_avg", "Earnings surprise (avg)", "Earnings", COMPANY),
    _b("earnings_surprise_last", "Earnings surprise (last)", "Earnings", COMPANY),
    _b("earnings_beat_rate", "Earnings beat rate", "Earnings", COMPANY),
    _b("days_to_next_earnings", "Days to next earnings", "Earnings", COMPANY),
    # -- Ownership (insider + institutional; stock/reit/adr only) ------------ #
    _b("insider_net_buy_pct", "Insider net buying", "Ownership", COMPANY),
    _b("institutions_count", "Institutional holders", "Ownership", COMPANY),
    # -- Income ------------------------------------------------------------- #
    _b("div_yield_ttm", "Dividend yield (TTM)", "Income", DIVIDEND_PAYERS),
    _b("div_rate_ttm", "Dividend rate (TTM)", "Income", DIVIDEND_PAYERS),
    _b("div", "Dividend growth", "Income", FUND_DIV, growth=True),
    _b("div_consecutive_years", "Consecutive years", "Income", FUND_DIV),
    _b("div_consistency", "Dividend consistency", "Income", FUND_DIV),
    _b("div_payout_ratio", "Payout ratio", "Income", frozenset({STANDARD, BANK, INSURANCE})),
    _b("div_coverage", "Dividend coverage", "Income", frozenset({STANDARD, BANK, INSURANCE})),
    # -- Technical (all traded; mutual funds drop volume + ATR) ------------- #
    _b("price_vs_ma_50", "Price vs 50-day MA", "Technical", ALL_TRADED),
    _b("price_vs_ma_150", "Price vs 150-day MA", "Technical", ALL_TRADED),
    _b("price_vs_ma_200", "Price vs 200-day MA", "Technical", ALL_TRADED),
    _b("ma_50", "50-day MA", "Technical", ALL_TRADED),
    _b("ma_150", "150-day MA", "Technical", ALL_TRADED),
    _b("ma_200", "200-day MA", "Technical", ALL_TRADED),
    _b("rsi_14", "RSI(14)", "Technical", ALL_TRADED),
    _b("macd_line", "MACD line", "Technical", ALL_TRADED),
    _b("macd_signal", "MACD signal", "Technical", ALL_TRADED),
    _b("macd_hist", "MACD histogram", "Technical", ALL_TRADED),
    _b("macd_crossover", "MACD crossover", "Technical", ALL_TRADED),
    _b("macd_hist_trend", "MACD histogram trend", "Technical", ALL_TRADED),
    _b("bb_pct", "Bollinger %B", "Technical", ALL_TRADED),
    _b("bb_width", "Bollinger width", "Technical", ALL_TRADED),
    _b("bb_position", "Bollinger position", "Technical", ALL_TRADED),
    _b("bb_squeeze", "Bollinger squeeze", "Technical", ALL_TRADED),
    _b("pct_from_52w_high", "% from 52-week high", "Technical", ALL_TRADED),
    _b("pct_from_52w_low", "% from 52-week low", "Technical", ALL_TRADED),
    _b("trend", "Trend", "Technical", ALL_TRADED),
    _b("vol_20d_avg", "Avg volume (20d)", "Technical", TRADED_NO_MF),
    _b("vol_ratio", "Volume ratio", "Technical", TRADED_NO_MF),
    _b("vol_trend", "Volume trend", "Technical", TRADED_NO_MF),
    _b("atr_pct", "ATR %", "Technical", TRADED_NO_MF),
    _b("history_years", "History (yrs)", "Technical", ALL_TRADED),
    # -- Intrinsic value ---------------------------------------------------- #
    _b("intrinsic_value_graham", "Graham value", "Intrinsic Value", frozenset({STANDARD, BANK, INSURANCE})),
    _b("intrinsic_value_lynch", "Lynch value", "Intrinsic Value", frozenset({STANDARD, BANK, INSURANCE})),
    _b("intrinsic_value_dcf", "DCF value", "Intrinsic Value", frozenset({STANDARD})),
    _b("margin_of_safety", "Margin of safety", "Intrinsic Value", frozenset({STANDARD, BANK, INSURANCE})),
    # -- Relative strength -------------------------------------------------- #
    _b("rs_rank", "RS Rank", "Relative Strength", ALL_TRADED),
    # -- Scores (engine-gated; offered where meaningful) -------------------- #
    _b("overall_score", "Overall", "Score", ALL_TRADED),
    _b("value_score", "Value", "Score", COMPANY),
    _b("quality_score", "Quality", "Score", COMPANY),
    _b("growth_score", "Growth", "Score", COMPANY),
    _b("orphan_score", "Orphan", "Score", frozenset({STANDARD, REIT})),
    _b("momentum_score", "Momentum", "Score", ALL_TRADED),
    _b("income_score", "Income", "Score", DIVIDEND_PAYERS),
    # -- Classification (text labels; filtered via the multi-pick value list) - #
    _b("sector", "Sector", "Classification", COMPANY),
    _b("industry", "Industry", "Classification", COMPANY),
    _b("fund_family", "Fund family", "Classification", FUNDS),
]

BASE_BY_KEY: dict[str, Base] = {b.key: b for b in BASES}


# --------------------------------------------------------------------------- #
# Column existence (peer-relative availability is data-driven from analysis.db).
# --------------------------------------------------------------------------- #
@lru_cache(maxsize=1)
def analysis_columns() -> frozenset[str]:
    """Columns present in analysis.db's `analysis` table (cached)."""
    if not settings.ANALYSIS_DB.exists():
        return frozenset()
    with Database(settings.ANALYSIS_DB) as db:
        return frozenset(db.columns("analysis"))


def peer_columns(column: str) -> dict[str, str]:
    """Available peer-relative variants for a concrete column.

    Returns {"vs_sector": "<col>_vs_sector", ...} for whichever exist in analysis.db.
    """
    cols = analysis_columns()
    out: dict[str, str] = {}
    for suffix in ("vs_sector", "vs_industry", "vs_type"):
        c = f"{column}_{suffix}"
        if c in cols:
            out[suffix] = c
    return out


def score_column(column: str) -> str | None:
    """The stored per-metric goodness ("Score") column for a concrete column, if the
    analysis layer computed one. Data-driven mirror of `peer_columns` — the Filter
    page offers the "Score" variant only when this column actually exists.
    """
    c = f"{column}_goodness"
    return c if c in analysis_columns() else None


def growth_windows(base_key: str) -> dict[str, str]:
    """GROWTH_WINDOWS filtered to the suffixes that exist as `{base_key}_{suffix}`
    columns in analysis.db — not every growth base carries all 7 (dividends have
    no quarterly cadence, so there's no `div_yoy_q`). Falls back to the full set
    before analysis.db exists, same as `peer_columns`/`score_column` have nothing
    to check yet.
    """
    cols = analysis_columns()
    if not cols:
        return dict(GROWTH_WINDOWS)
    return {w: label for w, label in GROWTH_WINDOWS.items() if f"{base_key}_{w}" in cols}


def bases_for_types(selected: set[str]) -> list[Base]:
    """Bases meaningful for ALL selected screen types (strict intersection).

    Empty selection -> nothing (the page asks the user to pick a type first).
    """
    if not selected:
        return []
    return [b for b in BASES if selected <= b.applies]


def bases_by_category(selected: set[str]) -> dict[str, list[Base]]:
    """`bases_for_types` grouped into ordered categories (skips empty categories),
    each category's bases sorted alphabetically by name for the picker lists."""
    chosen = bases_for_types(selected)
    grouped: dict[str, list[Base]] = {cat: [] for cat in CATEGORY_ORDER}
    for b in chosen:
        grouped.setdefault(b.category, []).append(b)
    return {cat: sorted(items, key=lambda b: b.name.lower())
            for cat, items in grouped.items() if items}


def bases_for_types_any(selected: set[str]) -> list[Base]:
    """Bases meaningful for AT LEAST ONE selected screen type (union, not the
    strict intersection `bases_for_types` uses).

    For the Output page's Add-columns picker: the "selected" types there are
    just whatever screen_types happen to be in a loaded/custom result — not a
    deliberate combined-screen choice the way the Filter page's Security Type
    checklist is (ROADMAP Topic 5: "mixed types -> only shared metrics shown",
    so a filter BLOCK never silently zeroes out a whole type it doesn't apply
    to). A column is just NULL for rows it doesn't apply to — the normal
    "not applicable" convention — so there's no such downside on Output.
    """
    if not selected:
        return []
    return [b for b in BASES if selected & b.applies]


def bases_by_category_any(selected: set[str]) -> dict[str, list[Base]]:
    """`bases_for_types_any` grouped into ordered categories (skips empty
    categories), each category's bases sorted alphabetically by name."""
    chosen = bases_for_types_any(selected)
    grouped: dict[str, list[Base]] = {cat: [] for cat in CATEGORY_ORDER}
    for b in chosen:
        grouped.setdefault(b.category, []).append(b)
    return {cat: sorted(items, key=lambda b: b.name.lower())
            for cat, items in grouped.items() if items}
