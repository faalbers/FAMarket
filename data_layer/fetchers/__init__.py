"""
Per-API fetchers (Topic 3.1).

Group 1 — Symbol Discovery (sequential, always first): polygon, edgar.
Group 2 — Data Fetch (single-threaded in Phase 1, parallel in Phase 2):
          yfinance, fmp, etrade, fred.

Every fetcher follows the same flow (Topic 9.4):
    fetch raw -> sanitize -> conditional enrichment -> sanitize enrichment -> write
Each fetcher owns its paired sanitize function; sanitize runs on the raw API
response before anything touches a database.
"""
