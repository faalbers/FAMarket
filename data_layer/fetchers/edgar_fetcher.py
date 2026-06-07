"""
SEC EDGAR fetcher (Topic 3.1) — free, no rate limit. Secondary symbol source and
fallback for financial filings.

Symbol source: https://www.sec.gov/files/company_tickers.json
EDGAR symbols enter symbols.db with source=edgar and is_validated=False; they get
validated as a byproduct of the normal yfinance fetch (no extra API calls):
  1. yfinance info returns real data -> quote written to quotes.db
  2. OHLCV has data within the last few weeks -> actively trading
  3. financials have recent periods -> not a dead/shell company
All three must pass for is_validated=True.

Note: SEC requires a descriptive User-Agent header on requests.

SKELETON — Phase 1.
"""

from __future__ import annotations

# from data_layer.fetchers.base import BaseFetcher
