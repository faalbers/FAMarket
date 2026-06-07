"""
Polygon fetcher (Topic 3.1) — symbol universe (primary), historical OHLCV,
reference data (splits, dividends). Free tier: 5 req/min (see settings.RATE_LIMITS).

Provides the ticker `type` used for first-pass type resolution, and the raw
symbol that data_layer.symbols.normalize_symbol() converts to yfinance format.

SKELETON — Phase 1.
"""

from __future__ import annotations

# from data_layer.fetchers.base import BaseFetcher
