"""
Screen-type classification (Topic 3.2 / Topic 5).

analysis.db stores only `security_type` (stock/adr/etf/mutual_fund/…). The Filter
page groups on a finer **screen type** that splits common stock / ADR into
standard / bank / insurance / reit by sector+industry — exactly the
"detected from sector/industry … manual override available in the UI" rule noted in
`config/type_map.py`.

This is computed **once, during the analysis rebuild** (written as the `screen_type`
column by `pipeline.run_analysis`) so every consumer — Filter, Output, future
overrides — reads one canonical value instead of re-deriving it. The Filter UI
imports these constants and `classify` for its labels/help and as a fallback when
an older analysis.db predates the column.
"""

from __future__ import annotations

# Canonical screen-type keys.
STANDARD = "standard"
BANK = "bank"
INSURANCE = "insurance"
REIT = "reit"
ETF = "etf"
CEF = "closed_end_fund"
MUTUAL_FUND = "mutual_fund"
PREFERRED = "preferred"
MINIMAL = "minimal"  # spac / warrant / unit / index — price + technicals only

# Raw analysis.db security_type -> screen type, for the non-derived cases.
_DIRECT: dict[str, str] = {
    "etf": ETF,
    "closed_end_fund": CEF,
    "mutual_fund": MUTUAL_FUND,
    "preferred": PREFERRED,
    "spac": MINIMAL,
    "warrant": MINIMAL,
    "unit": MINIMAL,
    "index": MINIMAL,
}


def classify(security_type: str | None, sector: str | None, industry: str | None) -> str:
    """Resolve (security_type, sector, industry) to a screen type.

    stock/adr split into standard / bank / insurance / reit by sector+industry; all
    other security types map directly. Unknown/None -> MINIMAL so a row is never
    silently dropped.
    """
    st = (security_type or "").strip().lower()
    if st in _DIRECT:
        return _DIRECT[st]
    if st in ("stock", "adr"):
        sec = (sector or "").strip().lower()
        ind = (industry or "").strip().lower()
        if sec == "real estate" or "reit" in ind:
            return REIT
        if "bank" in ind or "mortgage finance" in ind or "capital markets" in ind:
            return BANK
        if "insurance" in ind:
            return INSURANCE
        return STANDARD
    return MINIMAL
