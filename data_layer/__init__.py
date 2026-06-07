"""
Data Layer (Phase 1).

Responsibilities:
  * Symbol discovery (Polygon primary, SEC EDGAR gap-fill) and normalization
    (Polygon -> yfinance/E*Trade symbol formats).
  * Symbol state management: is_active / is_validated / in_polygon flags,
    reassessed at the end of every fetch run.
  * Per-API fetchers (see fetchers/), each with a paired sanitize function.
  * fetch_status tracking for resumable runs (5-day per-fetcher lock).

The layer writes to symbols.db, quotes.db, ohlcv.db, financials.db, macro.db.
Cross-database work is done in pandas, never in SQL joins across files.
"""
