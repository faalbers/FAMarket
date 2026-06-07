"""
Security-type normalization (Topic 3.2).

Different APIs label the same instrument differently. Type resolution happens
*first* in the pipeline: Polygon's ticker `type` provides the initial type,
normalized through TYPE_MAP; yfinance `quoteType` is the fallback for unknowns.
The normalized `security_type` (+ `sub_type`) is stored on every symbol in
symbols.db before any data fetch begins.

Keep this a plain lookup table — Frank edits it by hand as new raw labels show
up in the wild.
"""

from __future__ import annotations

# Canonical security types used everywhere downstream (filter UI, scope tiers).
SECURITY_TYPES = (
    "stock",          # common stock
    "etf",
    "reit",
    "mutual_fund",
    "adr",
    "preferred",
    "closed_end_fund",
    "spac",
    "warrant",
    "unit",
    "index",
)

# Raw API label (UPPER, source-prefixed where ambiguous) -> canonical type.
TYPE_MAP: dict[str, str] = {
    # yfinance quoteType
    "EQUITY": "stock",
    "ETF": "etf",
    "MUTUALFUND": "mutual_fund",
    "INDEX": "index",
    # Polygon ticker type
    "CS": "stock",          # common stock
    "ADRC": "adr",
    "ETV": "etf",
    "ETN": "etf",
    "ETS": "etf",           # single-security / option-income ETF

    "FUND": "closed_end_fund",
    "PFD": "preferred",
    "WARRANT": "warrant",
    "UNIT": "unit",
    "SP": "spac",
    "RIGHT": "warrant",
    # REIT is usually detected from sector/industry, not type — see analysis layer.
}

# Common Stock / ADR sub-types (Topic 5.1) — auto-detected from sector/industry,
# manual override available in the UI.
COMMON_STOCK_SUBTYPES = ("standard", "bank_financial", "insurance")

# Scope tiers (Topic 3.2): how much data each type gets fetched.
SCOPE_TIERS: dict[str, str] = {
    "stock": "full",
    "reit": "full",
    "adr": "full",
    "etf": "full_etf",
    "closed_end_fund": "full_etf",
    "preferred": "income_only",   # quotes + yield + dividend history
    "mutual_fund": "limited",     # quotes + basic info
    "spac": "minimal",            # quotes only
    "warrant": "minimal",
    "unit": "minimal",
    "index": "minimal",
}


def normalize_type(raw: str | None) -> str | None:
    """Map a raw API type label to a canonical security type, or None."""
    if not raw:
        return None
    return TYPE_MAP.get(raw.strip().upper())
