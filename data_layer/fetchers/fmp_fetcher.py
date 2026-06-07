"""
FMP / Financial Modeling Prep fetcher (Topic 3.1) — financial statements & ratios
supplement targeting yfinance gaps. Free tier ~250 req/day (settings.RATE_LIMITS).

Usage strategy (deferred to coding phase): Key Metrics endpoint, prioritize high
market-cap symbols, fill where yfinance returns None/NaN.

SKELETON — Phase 1.
"""

from __future__ import annotations

# from data_layer.fetchers.base import BaseFetcher
